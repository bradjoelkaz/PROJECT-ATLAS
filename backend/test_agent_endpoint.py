import json
import urllib.request
import urllib.error
import sys

# 테스트 목적지 URL
URL = "http://localhost:8000/api/agent/report"

def run_test():
    print("=== STARTING AGENT ENDPOINT QA TEST ===")
    
    # 1. 빈 대화 기록 테스트 (Validation Error)
    print("\n1. Testing empty dialogue history validation...")
    data_empty = {"history": []}
    req_empty = urllib.request.Request(
        URL,
        data=json.dumps(data_empty).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req_empty) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            print(f"   Response received: {res_body}")
            assert res_body["success"] is False
            assert res_body["error_type"] == "empty_history"
            print("   [PASS] Empty history validation succeeded!")
    except urllib.error.URLError as e:
        print(f"   [FAIL] Server connection error: {e}")
        print("   Make sure the backend server (FastAPI) is running at http://localhost:8000")
        sys.exit(1)
    except AssertionError:
        print("   [FAIL] Expected success: False, error_type: 'empty_history'")
        sys.exit(1)

    # 2. 모의 대화 데이터를 보내 에이전트 구동 및 Quota 한도 예외 처리 검증
    print("\n2. Testing mock dialogue history and Agent quota handling...")
    mock_history = {
        "history": [
            {"speaker": "host", "original": "안녕하세요 어디로 가시나요?", "translated": "Hello, where are you going?"},
            {"speaker": "guest", "original": "Lotte Hotel, please.", "translated": "롯데호텔로 가주세요."}
        ]
    }
    
    req_mock = urllib.request.Request(
        URL,
        data=json.dumps(mock_history).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    print("   Sending mock dialogue history. Calling Antigravity Agent (takes time to evaluate)...")
    try:
        with urllib.request.urlopen(req_mock) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            print(f"   Response received: {res_body}")
            
            # Quota 에러가 발생하는 환경이므로 success는 False여야 하며,
            # error_type은 'quota_limit'로 예외 처리되어 반환되어야 합니다.
            assert res_body["success"] is False
            assert res_body["error_type"] == "quota_limit"
            assert "quota" in res_body["message"] or "할당량" in res_body["message"]
            print("   [PASS] Antigravity Agent quota limitation gracefully handled and mapped!")
            
    except urllib.error.URLError as e:
        print(f"   [FAIL] Server connection error: {e}")
        sys.exit(1)
    except AssertionError as e:
        print(f"   [FAIL] Assertion failed: {e}")
        print("   If you have whitelisted access and billing active, success might be True. Verify manual report.")
        sys.exit(1)

    print("\n=== ALL ENDPOINT TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_test()
