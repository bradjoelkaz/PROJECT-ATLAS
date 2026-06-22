"""
PROJECT ATLAS - Antigravity Agent Helper Utility
==================================================

이 파일은 구글의 신규 관리형 에이전트(Antigravity Preview) API를 
언제든지 호출할 수 있도록 미리 작성해 둔 헬퍼 스크립트입니다.

현재 실시간 번역 세션(초저지연 필수)에는 속도 제약으로 직접 사용하지 않으나,
추후 대화 분석, 운행 리포트 자동 생성, 분실물 등록 등 
자율 행동 비서 기능이 필요할 때 가져다 쓸 수(import) 있습니다.
"""

import asyncio
import os
import sys
from google import genai
from google.genai import types
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

class AntigravityHelper:
    def __init__(self):
        if not API_KEY:
            raise ValueError("GEMINI_API_KEY 가 설정되지 않았습니다. backend/.env 파일을 확인하세요.")
        # google-genai 클라이언트 초기화
        self.client = genai.Client(api_key=API_KEY)
        self.agent_model = "antigravity-preview-05-2026"

    def run_task(self, prompt: str, search_enabled: bool = True) -> str:
        """에이전트에게 자율 리서치/코드 실행 태스크를 요청하고 텍스트 결과를 반환합니다."""
        tools = [{"type": "code_execution"}]
        if search_enabled:
            tools.append({"type": "google_search"})
            tools.append({"type": "url_context"})

        print(f"[Antigravity] 에이전트 구동 중 (태스크: {prompt[:40]}...)", flush=True)
        try:
            interaction = self.client.interactions.create(
                agent=self.agent_model,
                input=prompt,
                environment="remote",  # 구글 보안 리눅스 샌드박스 활성화
                tools=tools
            )
            return interaction.output_text
        except Exception as e:
            return f"에이전트 실행 실패: {e}"

    def analyze_dialogue_history(self, history: list) -> str:
        """기사와 승객의 대화 기록 리스트를 기반으로 요약 보고서를 작성하게 시킵니다."""
        formatted_history = ""
        for item in history:
            speaker = "기사(한국어)" if item.get("speaker") == "host" else "승객(외국어)"
            formatted_history += f"- {speaker} 원본: {item.get('original', '')} | 번역: {item.get('translated', '')}\n"

        prompt = f"""
다음은 택시 안에서 기사와 외국인 승객이 나눈 대화 기록입니다:
{formatted_history}

이 대화 기록을 분석해서 아래 형식으로 리포트를 작성해 주세요.
그리고 이 리포트를 샌드박스 내부의 'taxi_run_report.md' 파일로도 직접 생성해서 저장해 주세요.

## 분석 리포트 형식:
1. 승객의 탑승 목적지 (대화 속 정보 기반 추출)
2. 대화 요약 (어떤 상황이었는지)
3. 주요 특징 및 특이사항 (기념품 추천 요청, 요금 합산 요청 여부 등)
4. 사용된 외국어 언어 및 승객의 전반적인 분위기 평가
"""
        return self.run_task(prompt, search_enabled=False)

def run_demo():
    """모의 대화 데이터를 기반으로 헬퍼를 단독 테스트 실행하는 데모 함수"""
    helper = AntigravityHelper()
    
    # 모의 대화 로그 (실제 index.html에서 대화 완료 시 쌓이는 구조와 동일)
    mock_history = [
        {"speaker": "host", "original": "안녕하세요, 어디로 모실까요?", "translated": "Hello, where can I take you?"},
        {"speaker": "guest", "original": "Please go to the Lotte Hotel in Myeongdong.", "translated": "명동 롯데호텔로 가주세요."},
        {"speaker": "host", "original": "알겠습니다. 40분 정도 소요되며 요금은 약 2만 원입니다. 경로상 남산터널을 지납니다.", "translated": "Understood. It takes about 40 minutes, and the fare is around 20,000 won. We'll pass through Namsan Tunnel."},
        {"speaker": "guest", "original": "That is fine. Is there a good ginseng chicken soup restaurant near the hotel?", "translated": "괜찮습니다. 호텔 근처에 맛있는 삼계탕집이 있나요?"},
        {"speaker": "host", "original": "네, 명동 영양센터 삼계탕이 아주 유명합니다. 하차하실 때 정확한 위치를 알려드릴게요.", "translated": "Yes, Myeongdong Yeongyang Center's Samgyetang is very famous. I'll point out the exact location when you get off."}
    ]
    
    print("=== Antigravity 에이전트 대화 요약 데모 시작 ===")
    result = helper.analyze_dialogue_history(mock_history)
    print("\n[에이전트 결과 출력]")
    print(result)

if __name__ == "__main__":
    # python agent_helper.py 로 단독 실행 시 데모 수행
    if not API_KEY:
        print("오류: GEMINI_API_KEY가 없습니다. .env 파일을 채운 후 실행하세요.")
        sys.exit(1)
    run_demo()
