# PROJECT ATLAS — 양방향 음성 번역 PoC (1단계)

> 택시 기사용 실시간 양방향 음성 번역기. **번역(말하는 거)이 기술적으로 되는지**만
> 검증하는 실험용 시제품입니다. 지도/요금/결제 등 부가기능은 일부러 뺐습니다.

## 무엇을 검증하나

- **양방향 번역**: 승객(외국어) ↔ 기사(한국어) 가 음성으로 대화
- **2세션 구조**: Gemini Live Translation 세션 1개 = 번역 방향 1개 이므로,
  - 세션 A (`target=ko`): 승객 외국어 → 한국어 (기사가 들음)
  - 세션 B (`target=ja` 등): 기사 한국어 → 외국어 (승객이 들음)
- **버튼 라우팅(1단계)**: "누가 말하는지" 자동 감지(화자분리)는 어려우므로,
  버튼으로 말하는 사람을 지정해 알맞은 세션으로만 오디오를 보냅니다. → 엉킴 0%

```
        브라우저(마이크/스피커)
                │  WebSocket (PCM 오디오 + 제어 메시지)
                ▼
        FastAPI 프록시  ──▶ Gemini 세션 A (외국어→한국어)
        (API키는 서버에만)  ──▶ Gemini 세션 B (한국어→외국어)
```

## 폴더 구조

```
PROJECT-ATLAS/
├── backend/
│   ├── main.py            # FastAPI + WebSocket + Gemini Live 2세션 + 라우팅
│   ├── requirements.txt
│   └── .env.example       # 환경변수 템플릿 (복사해서 .env 생성)
├── web/
│   ├── index.html         # 테스트 UI (버튼 2개)
│   └── pcm-processor.js    # 마이크 16kHz PCM 변환 AudioWorklet
└── README.md
```

## 실행 방법 (본인 PC, 인터넷 필요)

> ⚠️ 이 코드는 외부 인터넷이 되는 환경에서 실행해야 합니다 (Gemini 서버 접속 필요).

### 1) API 키 준비

```bash
cd backend
cp .env.example .env
# .env 파일을 열어 GEMINI_API_KEY 에 실제 키를 넣으세요.
# 키 발급: https://aistudio.google.com/apikey
```

### 2) 설치 & 실행

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # (윈도우: .venv\Scripts\activate)
pip install -r requirements.txt
python main.py
```

### 3) 접속

브라우저에서 `http://localhost:8000` 접속 →
**🎤 마이크 권한 허용** → 버튼 누르고 말하기.

- `🇰🇷 내가(기사) 말하기` 버튼: 한국어로 말하면 → 외국어로 번역되어 들림
- `🌐 승객 말하기` 버튼: 외국어로 말하면 → 한국어로 번역되어 들림

## 친구한테 테스트시키는 법

친구 PC가 아니라 **내 PC에서 실행한 걸 친구 폰에서 접속**하게 하는 게 가장 쉽습니다.

- **같은 와이파이**: 친구 폰에서 `http://<내PC_IP>:8000` 접속
- **외부 공유(추천)**: [ngrok](https://ngrok.com) 같은 터널로 임시 https 링크 생성 후 카톡 전송
  ```bash
  ngrok http 8000
  ```
  > 마이크는 보안상 **https** 또는 `localhost` 에서만 동작합니다.
  > 친구 폰에서 쓰려면 ngrok 같은 https 링크가 필요합니다.

## 알려진 한계 (PoC 1단계 의도된 범위)

- 버튼을 눌러야 함 (완전 핸즈프리 아님 → 2단계에서 VAD/화자분리로 자동화)
- 차량 소음 환경 미검증 (실차 테스트로 확인 필요)
- 지도/요금/목적지 연동 없음 (음성 번역만)
- `gemini-3.5-live-translate-preview` 는 프리뷰 모델 (사양 변경 가능)

## 다음 단계 (검증 후)

1. VAD(`@ricky0123/vad-web`) 로 "말 끝남" 자동 감지 → 버튼 떼기 자동화
2. 차량 소음 실차 테스트
3. 카카오 로컬 API + TMAP 으로 목적지/요금 연동
4. 익명화 이벤트 로그 적재 (설계서 Phase 1)
