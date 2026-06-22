import asyncio
import json
import base64
import sys
import websockets

async def run_test():
    url_driver = "ws://localhost:8000/ws/call?room=test_lock_room&role=driver"
    url_passenger = "ws://localhost:8000/ws/call?room=test_lock_room&role=passenger"
    
    print("1. Connecting Driver client...")
    async with websockets.connect(url_driver) as ws_driver:
        msg_driver_init = json.loads(await ws_driver.recv())
        print(f"   Driver init status: {msg_driver_init}")
        assert msg_driver_init["type"] == "status"
        
        print("2. Connecting Passenger client...")
        async with websockets.connect(url_passenger) as ws_passenger:
            msg_pass_init = json.loads(await ws_passenger.recv())
            print(f"   Passenger init status: {msg_pass_init}")
            assert msg_pass_init["type"] == "status"
            
            # Consume presence updates on both connections
            presence_driver = json.loads(await ws_driver.recv())
            presence_pass = json.loads(await ws_passenger.recv())
            print(f"   Driver presence update: {presence_driver}")
            print(f"   Passenger presence update: {presence_pass}")
            
            print("\n3. Driver starts speaking (sending PCM audio)...")
            # 1 second of silent 16kHz 16-bit PCM = 32000 bytes
            dummy_pcm = b"\x00" * 32000
            audio_payload = base64.b64encode(dummy_pcm).decode("ascii")
            await ws_driver.send(json.dumps({"type": "audio", "data": audio_payload}))
            
            print("4. Verifying Passenger is locked out (other_speaking: True)...")
            lock_msg = json.loads(await ws_passenger.recv())
            print(f"   Passenger received: {lock_msg}")
            assert lock_msg["type"] == "other_speaking"
            assert lock_msg["active"] is True
            print("   [PASS] Passenger mic successfully locked!")
            
            print("\n5. Driver stops speaking (sending audio_end)...")
            await ws_driver.send(json.dumps({"type": "audio_end"}))
            
            print("6. Waiting for Gemini translation to complete and Passenger to unlock...")
            unlocked = False
            # Wait up to 10 seconds for turn complete and unlock
            for _ in range(50):
                try:
                    resp = json.loads(await asyncio.wait_for(ws_passenger.recv(), timeout=0.5))
                    print(f"   Passenger received message: {resp}")
                    if resp["type"] == "other_speaking" and resp["active"] is False:
                        print("   [PASS] Passenger mic successfully unlocked!")
                        unlocked = True
                        break
                except asyncio.TimeoutError:
                    continue
            
            if not unlocked:
                print("   [FAIL] Timeout waiting for passenger unlock!")
                sys.exit(1)

if __name__ == "__main__":
    print("=== STARTING CALL INTERLOCK QA TEST ===")
    try:
        asyncio.run(run_test())
        print("\nALL TESTS PASSED SUCCESSFULLY!")
    except Exception as e:
        print(f"\nTEST RUN ERROR: {str(e).encode('ascii', 'ignore').decode('ascii')}")
        sys.exit(1)
