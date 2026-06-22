"""
PROJECT ATLAS - 양방향 음성 번역 PoC (1단계)
================================================

택시 기사용 실시간 양방향 음성 번역기의 핵심 가설 검증용 백엔드.

구조:
  브라우저 ──WebSocket──▶ 이 서버 ──▶ Gemini 세션 A (외국어 → 한국어)
                                  └──▶ Gemini 세션 B (한국어 → 외국어)

왜 2세션인가:
  Gemini Live Translation 세션 1개는 "하나의 목적지 언어"로만 번역한다.
  따라서 양방향(승객↔기사) 대화를 하려면 방향별로 세션이 2개 필요하다.
    - 세션 A (target = HOST_LANGUAGE, 예: ko): 승객 외국어 → 한국어 (기사가 들음)
    - 세션 B (target = GUEST_LANGUAGE, 예: ja): 기사 한국어 → 외국어 (승객이 들음)

라우팅 (1단계 - 버튼 방식):
  "누가 말하는지" 자동 감지(화자분리)는 좁은 차 안에서 까다로우므로,
  클라이언트가 버튼으로 화자를 지정한다. 서버는 그 화자의 오디오를
  알맞은 세션 하나에만 보낸다. → 두 세션이 동시에 떠드는 엉킴이 없다.

API 키는 이 서버(.env)에만 존재하며, 브라우저로는 절대 노출되지 않는다.
"""

import asyncio
import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
import uvicorn

# ----------------------------------------------------------------------------
# 환경 설정
# ----------------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GUEST_LANGUAGE = os.getenv("GUEST_LANGUAGE", "ja").strip()   # 외국인(승객) 언어
HOST_LANGUAGE = os.getenv("HOST_LANGUAGE", "ko").strip()     # 기사(자국) 언어
PORT = int(os.getenv("PORT", "8000"))

MODEL = "gemini-3.5-live-translate-preview"

# 입력 16kHz / 출력 24kHz PCM (Gemini Live Translation 규격)
INPUT_MIME = "audio/pcm;rate=16000"

WEB_DIR = Path(__file__).resolve().parent.parent / "web"






def build_config(target_language_code: str) -> types.LiveConnectConfig:
    """주어진 목적지 언어로 번역하는 단방향 세션 설정을 만든다.

    주의: gemini-3.5-live-translate-preview 는 '번역 전용' 파이프라인이라
    system_instruction(시스템 프롬프트)/도구/텍스트 입력을 지원하지 않는다.
    system_instruction 을 넣으면 세션 setup 단계에서 거부되어 연결이 죽을 수 있으므로
    translation_config 외의 지시는 절대 넣지 않는다.
    """
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(
            target_language_code=target_language_code,
            # False: 입력이 목적지 언어가 아니거나 불확실하면 침묵.
            # True 로 두면 무음/잡음을 목적지 언어로 헛인식해 의미없는 음성(예: のね)을
            # 계속 내뱉는다. 번역 전용 단방향 세션에서는 False 가 맞다.
            echo_target_language=False,
        ),
    )


app = FastAPI(title="Project Atlas - 양방향 번역 PoC")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL,
        "guest_language": GUEST_LANGUAGE,
        "host_language": HOST_LANGUAGE,
        "api_key_present": bool(API_KEY),
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    if not API_KEY:
        await websocket.send_text(json.dumps({
            "type": "error",
            "text": "GEMINI_API_KEY 가 설정되지 않았습니다. backend/.env 를 확인하세요.",
        }))
        await websocket.close()
        return

    client = genai.Client(api_key=API_KEY)

    # 승객(외국인) 언어를 클라이언트가 선택할 수 있게 쿼리에서 읽는다. 기본은 환경변수값.
    # 예: ?guest_lang=ja|zh-Hans|en  (to_host 세션은 target=ko로 소스 자동감지라 무관)
    guest_lang = (websocket.query_params.get("guest_lang") or GUEST_LANGUAGE).strip() or GUEST_LANGUAGE

    # 세션 A: 무엇이든 외국어(GUEST)로 듣고 한국어(HOST)로 번역 → 기사가 들음 (출력 방향: to_host)
    cfg_to_host = build_config(target_language_code=HOST_LANGUAGE)

    # 세션 B: 한국어(HOST)를 승객 언어로 번역 → 승객이 들음 (출력 방향: to_guest)
    cfg_to_guest = build_config(target_language_code=guest_lang)

    # 현재 활성 화자. "guest"=승객(외국어), "host"=기사(한국어). 기본은 승객.
    state = {"active": "guest"}

    async def send_json(payload: dict) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    try:
        async with (
            client.aio.live.connect(model=MODEL, config=cfg_to_host) as sess_to_host,
            client.aio.live.connect(model=MODEL, config=cfg_to_guest) as sess_to_guest,
        ):
            await send_json({
                "type": "status",
                "text": "세션 연결됨",
                "guest_language": guest_lang,
                "host_language": HOST_LANGUAGE,
            })

            def active_session():
                """활성 화자에 맞는 번역 세션을 고른다.

                - 승객(guest, 외국어)이 말하면 → 한국어로 번역하는 세션(to_host)
                - 기사(host, 한국어)가 말하면 → 외국어로 번역하는 세션(to_guest)
                """
                return sess_to_host if state["active"] == "guest" else sess_to_guest

            async def browser_to_gemini() -> None:
                """브라우저 → 서버: 제어 메시지 처리 + 오디오를 활성 세션으로 전달."""
                while True:
                    raw_msg = await websocket.receive_text()
                    msg = json.loads(raw_msg)
                    mtype = msg.get("type")

                    if mtype == "active":
                        speaker = msg.get("speaker")
                        if speaker in ("guest", "host"):
                            state["active"] = speaker
                    elif mtype == "audio_end":
                        await active_session().send_realtime_input(audio_stream_end=True)
                    elif mtype == "audio":
                        chunk = base64.b64decode(msg["data"])
                        await active_session().send_realtime_input(
                            audio=types.Blob(data=chunk, mime_type=INPUT_MIME)
                        )

            async def gemini_to_browser(session, direction: str) -> None:
                """서버 ← Gemini: 번역 오디오/자막을 받아 브라우저로 전달.

                direction: "to_host"(기사에게) 또는 "to_guest"(승객에게)
                """
                async for response in session.receive():
                    sc = response.server_content
                    if sc is None:
                        continue

                    if sc.input_transcription and sc.input_transcription.text:
                        await send_json({
                            "type": "input_transcript",
                            "direction": direction,
                            "text": sc.input_transcription.text,
                        })

                    if sc.output_transcription and sc.output_transcription.text:
                        await send_json({
                            "type": "output_transcript",
                            "direction": direction,
                            "text": sc.output_transcription.text,
                        })

                    model_turn = sc.model_turn
                    if model_turn and model_turn.parts:
                        for part in model_turn.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                await send_json({
                                    "type": "audio",
                                    "direction": direction,
                                    "data": base64.b64encode(inline.data).decode("ascii"),
                                })

                    if sc.turn_complete:
                        await send_json({
                            "type": "turn_complete",
                            "direction": direction,
                        })

            # Gemini 수신 펌프 2개를 백그라운드로 돌리고, 브라우저 입력을 메인으로 대기.
            recv_tasks = [
                asyncio.create_task(gemini_to_browser(sess_to_host, "to_host")),
                asyncio.create_task(gemini_to_browser(sess_to_guest, "to_guest")),
            ]
            try:
                await browser_to_gemini()
            finally:
                for t in recv_tasks:
                    t.cancel()
                await asyncio.gather(*recv_tasks, return_exceptions=True)

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - PoC: 어떤 오류든 클라이언트에 알림
        import traceback
        traceback.print_exc()  # 서버 콘솔에 에러 원인 상세 출력
        try:
            await send_json({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass

# ============================================================================
# 2-Device 통화방(Room) 모드 — 탑승 전 기사/승객이 각자 폰으로 통화
# ============================================================================
# 기존 단일기기 /ws 는 그대로 유지(하위 호환). 통화방은 /ws/call 로 분리한다.
#
# 오디오 흐름(크로스):
#   driver(한국어) 입력 → 세션(target=GUEST) → 번역(외국어) → passenger 스피커
#   passenger(외국어) 입력 → 세션(target=HOST)  → 번역(한국어) → driver 스피커
# 두 폰은 각각 한 사람만 담고 언어가 고정이므로 화자분리 문제가 없다.

active_rooms = {}
rooms_lock = asyncio.Lock()


class CallRoom:
    """한 통화방의 두 참가자(driver/passenger)와 Gemini 세션 2개를 관리한다."""

    def __init__(self, room_id: str, guest_lang: str = None):
        self.room_id = room_id
        self.guest_lang = (guest_lang or GUEST_LANGUAGE)
        self.client = genai.Client(api_key=API_KEY)
        self.ws = {"driver": None, "passenger": None}      # 역할별 클라이언트 WebSocket
        self.in_q = {"driver": asyncio.Queue(), "passenger": asyncio.Queue()}  # 역할별 입력 오디오 큐
        self.task = None
        self.started = False

    async def send_to(self, role: str, payload: dict) -> None:
        ws = self.ws.get(role)
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    async def broadcast_presence(self) -> None:
        for role in ("driver", "passenger"):
            await self.send_to(role, {
                "type": "presence",
                "driver": self.ws["driver"] is not None,
                "passenger": self.ws["passenger"] is not None,
                "you": role,
            })

    async def ensure_started(self) -> None:
        if self.started:
            return
        self.started = True
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        # driver(한국어) → 외국어로 번역 → passenger 가 들음
        cfg_driver = build_config(target_language_code=self.guest_lang)
        # passenger(외국어) → 한국어로 번역 → driver 가 들음
        cfg_passenger = build_config(target_language_code=HOST_LANGUAGE)
        try:
            async with (
                self.client.aio.live.connect(model=MODEL, config=cfg_driver) as sess_driver,
                self.client.aio.live.connect(model=MODEL, config=cfg_passenger) as sess_passenger,
            ):
                await self.broadcast_presence()

                async def feed(session, role):
                    """해당 역할의 입력 큐에서 오디오를 꺼내 세션으로 흘려보낸다."""
                    q = self.in_q[role]
                    while True:
                        kind, data = await q.get()
                        if kind == "audio":
                            await session.send_realtime_input(
                                audio=types.Blob(data=data, mime_type=INPUT_MIME))
                        elif kind == "audio_end":
                            await session.send_realtime_input(audio_stream_end=True)

                async def pump(session, speaker_role, listener_role):
                    """세션 출력을 라우팅: 원문 자막→발화자, 번역 자막/음성→상대방."""
                    async for response in session.receive():
                        sc = response.server_content
                        if sc is None:
                            continue
                        if sc.input_transcription and sc.input_transcription.text:
                            await self.send_to(speaker_role, {
                                "type": "input_transcript",
                                "text": sc.input_transcription.text})
                        if sc.output_transcription and sc.output_transcription.text:
                            await self.send_to(listener_role, {
                                "type": "output_transcript",
                                "text": sc.output_transcription.text})
                        mt = sc.model_turn
                        if mt and mt.parts:
                            for part in mt.parts:
                                inline = getattr(part, "inline_data", None)
                                if inline and inline.data:
                                    await self.send_to(listener_role, {
                                        "type": "audio",
                                        "data": base64.b64encode(inline.data).decode("ascii")})
                        if sc.turn_complete:
                            await self.send_to(listener_role, {"type": "turn_complete"})

                await asyncio.gather(
                    feed(sess_driver, "driver"),
                    feed(sess_passenger, "passenger"),
                    pump(sess_driver, "driver", "passenger"),     # 기사 발화 → 승객이 들음
                    pump(sess_passenger, "passenger", "driver"),  # 승객 발화 → 기사가 들음
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            for role in ("driver", "passenger"):
                await self.send_to(role, {"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        finally:
            self.started = False


async def get_room(room_id: str, guest_lang: str = None) -> CallRoom:
    async with rooms_lock:
        room = active_rooms.get(room_id)
        if room is None:
            room = CallRoom(room_id, guest_lang=guest_lang)
            active_rooms[room_id] = room
        return room


@app.websocket("/ws/call")
async def ws_call(websocket: WebSocket) -> None:
    """2-Device 통화방. 쿼리: ?room=<방번호>&role=driver|passenger"""
    await websocket.accept()

    if not API_KEY:
        await websocket.send_text(json.dumps({
            "type": "error", "text": "GEMINI_API_KEY 가 설정되지 않았습니다."}))
        await websocket.close()
        return

    room_id = (websocket.query_params.get("room") or "").strip() or "default"
    role = (websocket.query_params.get("role") or "").strip()
    if role not in ("driver", "passenger"):
        await websocket.send_text(json.dumps({
            "type": "error", "text": "role 은 driver 또는 passenger 여야 합니다."}))
        await websocket.close()
        return

    guest_lang = (websocket.query_params.get("guest_lang") or GUEST_LANGUAGE).strip() or GUEST_LANGUAGE
    room = await get_room(room_id, guest_lang=guest_lang)
    room.ws[role] = websocket
    await room.ensure_started()
    await room.send_to(role, {
        "type": "status", "text": "통화방 입장",
        "guest_language": room.guest_lang, "host_language": HOST_LANGUAGE, "role": role,
    })
    await room.broadcast_presence()

    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            mtype = msg.get("type")
            if mtype == "audio":
                await room.in_q[role].put(("audio", base64.b64decode(msg["data"])))
            elif mtype == "audio_end":
                await room.in_q[role].put(("audio_end", None))
    except WebSocketDisconnect:
        pass
    finally:
        if room.ws.get(role) is websocket:
            room.ws[role] = None
        await room.broadcast_presence()
        # 두 참가자 모두 나가면 방과 세션을 정리한다.
        async with rooms_lock:
            if room.ws["driver"] is None and room.ws["passenger"] is None:
                if room.task:
                    room.task.cancel()
                active_rooms.pop(room_id, None)


# 정적 프론트엔드 서빙 (반드시 /ws 라우트 정의 이후에 마운트).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
