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






def build_config(target_language_code: str, system_instruction: str = None) -> types.LiveConnectConfig:
    """주어진 목적지 언어로 번역하는 단방향 세션 설정을 만든다."""
    config_args = {
        "response_modalities": ["AUDIO"],
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "translation_config": types.TranslationConfig(
            target_language_code=target_language_code,
            # 입력이 이미 목적지 언어일 때 따라 말하지 않고 침묵.
            # (버튼으로 화자를 지정하므로 잘못된 언어가 들어올 일이 적지만 안전하게 False)
            echo_target_language=False,
        ),
    }
    if system_instruction:
        config_args["system_instruction"] = types.Content(
            parts=[types.Part(text=system_instruction)]
        )
    return types.LiveConnectConfig(**config_args)


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

    # 세션 A: 무엇이든 한국어(HOST)로 번역 → 기사가 들음 (출력 방향: to_host)
    host_instr = (
        "You are a professional taxi driver's translator. Translate the passenger's foreign language speech into polite and natural Korean for the Korean driver. "
        "Keep it concise, simple, and easy for the driver to understand immediately."
    )
    cfg_to_host = build_config(HOST_LANGUAGE, system_instruction=host_instr)
    
    # 세션 B: 무엇이든 외국어(GUEST)로 번역 → 승객이 들음 (출력 방향: to_guest)
    guest_instr = (
        f"You are a professional translator for a tourist passenger in a taxi. Translate the driver's Korean speech into polite and natural {GUEST_LANGUAGE} for the passenger. "
        "Keep it friendly and concise so the passenger feels comfortable."
    )
    cfg_to_guest = build_config(GUEST_LANGUAGE, system_instruction=guest_instr)

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
                "guest_language": GUEST_LANGUAGE,
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

# 정적 프론트엔드 서빙 (반드시 /ws 라우트 정의 이후에 마운트).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
