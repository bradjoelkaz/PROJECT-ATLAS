// PCM 캡처 AudioWorklet
// ---------------------
// 마이크 입력(Float32, AudioContext 실제 샘플레이트)을 받아서
// 16kHz, 16-bit PCM(Int16, little-endian)으로 변환해 메인 스레드로 보낸다.
//
// 주의: 일부 브라우저(특히 iOS Safari)는 AudioContext 를 16kHz 로 만들어도
// 하드웨어 기본값(예: 48kHz)으로 동작한다. 그래서 워크릿 전역 `sampleRate`
// (= 실제 컨텍스트 레이트)를 기준으로 16kHz 로 직접 리샘플링한다.
//
// 약 100ms(1600 샘플 @16kHz) 단위로 모아서 전송 → 메시지 오버헤드 감소.

const TARGET_RATE = 16000;

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / TARGET_RATE; // 입력레이트 / 16000 (예: 48000/16000 = 3)
    this._pos = 0;          // 리샘플링 위치(입력 샘플 단위, 소수)
    this._tail = [];        // process 호출 경계를 넘는 잔여 입력 샘플
    this._out = [];         // 16kHz 변환된 출력 샘플 누적
    this._chunkSize = 640;  // 40ms @16kHz로 딜레이 단축 (기존 60ms)
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) {
      return true;
    }

    // 이전 잔여분 + 이번 블록을 이어붙여 연속 신호로 처리
    const incoming = input[0];
    const buf = this._tail.length ? this._tail.concat(Array.from(incoming)) : Array.from(incoming);

    // 선형 보간 리샘플링: TARGET_RATE 로 다운/업샘플
    let pos = this._pos;
    const lastIndex = buf.length - 1;
    while (pos < lastIndex) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const sample = buf[i] * (1 - frac) + buf[i + 1] * frac;
      this._out.push(sample);
      pos += this._ratio;
    }
    // 다음 호출에서 이어쓸 수 있도록 마지막 정수 인덱스 이후 샘플을 잔여로 보관
    const consumed = Math.floor(pos);
    this._tail = buf.slice(consumed);
    this._pos = pos - consumed;

    // 16kHz 출력이 충분히 쌓이면 Int16 로 변환해 전송
    while (this._out.length >= this._chunkSize) {
      const frame = this._out.splice(0, this._chunkSize);
      const pcm = new Int16Array(frame.length);
      for (let j = 0; j < frame.length; j++) {
        let s = Math.max(-1, Math.min(1, frame[j]));
        pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
