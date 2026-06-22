// PCM 연속 재생 AudioWorklet (지지직/끊김 제거용)
// ------------------------------------------------
// 들어오는 24kHz PCM(Float32) 청크들을 링버퍼에 모아, 출력 콜백에서
// 컨텍스트 실제 샘플레이트로 "연속 보간 리샘플링"하여 한 줄기로 흘려보낸다.
//
// 기존 방식(청크마다 createBufferSource + start())의 문제:
//   1) 조각 경계에서 클릭/지지직 발생
//   2) 24kHz 버퍼를 컨텍스트(예: 48kHz)로 조각마다 따로 리샘플 → 경계 잡음
//   3) 스케줄 드리프트로 간헐적 끊김
// 이 워크릿은 하나의 연속 신호로 처리해 위 문제를 모두 없앤다.
//
// + 지터버퍼(prime): 약 120ms를 모은 뒤 재생을 시작해 네트워크/처리 지연에
//   의한 언더런(끊김)을 줄인다. 언더런이 나면 다시 모아서 재개한다.

const SRC_RATE = 24000;

class PCMPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = SRC_RATE / sampleRate;      // 소스(24k) 샘플 / 출력 샘플
    this._size = SRC_RATE * 12;               // 12초 링버퍼
    this._ring = new Float32Array(this._size);
    this._writeCount = 0;                     // 누적 기록 소스 샘플 수
    this._readPos = 0;                        // 소수 읽기 커서(소스 샘플 단위, 절대)
    this._primed = false;
    this._minPrime = Math.floor(SRC_RATE * 0.12); // 120ms 선버퍼

    this.port.onmessage = (e) => {
      const d = e.data;
      if (!d || !d.length) return;
      for (let i = 0; i < d.length; i++) {
        this._ring[this._writeCount % this._size] = d[i];
        this._writeCount++;
      }
      // 오버플로 가드: 쌓인 양이 링을 넘으면 오래된 것 버림
      const avail = this._writeCount - Math.floor(this._readPos);
      if (avail > this._size - 1) {
        this._readPos = this._writeCount - (this._size - 1);
      }
    };
  }

  process(outputs) {
    const out = outputs[0][0];
    if (!out) return true;

    const ratio = this._ratio;
    const size = this._size;

    // 지터버퍼: 충분히 모이기 전엔 무음 출력
    if (!this._primed) {
      if (this._writeCount - Math.floor(this._readPos) < this._minPrime) {
        out.fill(0);
        return true;
      }
      this._primed = true;
    }

    let i = 0;
    for (; i < out.length; i++) {
      const floorPos = Math.floor(this._readPos);
      if (this._writeCount - floorPos < 2) {
        // 언더런: 더 쌓일 때까지 다시 prime
        this._primed = false;
        break;
      }
      const frac = this._readPos - floorPos;
      const a = this._ring[floorPos % size];
      const b = this._ring[(floorPos + 1) % size];
      out[i] = a * (1 - frac) + b * frac;
      this._readPos += ratio;
    }
    // 남은 구간은 무음
    for (; i < out.length; i++) out[i] = 0;

    return true;
  }
}

registerProcessor("pcm-player", PCMPlayer);
