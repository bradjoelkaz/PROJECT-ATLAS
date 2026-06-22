// AudioStreamer — 번역 음성 연속 재생 엔진
// ------------------------------------------------------------
// 구글 공식 live-api-web-console 의 src/lib/audio-streamer.ts (Apache-2.0) 를
// 의존성 없는 순수 JS로 이식한 버전. 24kHz PCM16 → Float32 변환 후,
// 큐잉 + AudioContext 시간축 스케줄링으로 끊김/지지직 없이 연속 재생한다.
//
// 핵심 개선 사항 (QA 반영):
//  - 들어온 PCM을 임시 버퍼에 누적 후 2048샘플(약 85ms) 단위로 쪼개 큐에 적재 (오버헤드 및 조각화 방지)
//  - 재생 도중 큐가 비어 소리가 끊기는 현상(Underrun) 발생 시, 150ms 초기 지터버퍼 자동 복구
//  - setInterval 100ms 폴링 제거: 새 데이터 인입 즉시 스케줄링 처리하여 불필요한 대기 지연(stutter) 차단
//  - 타이머 중복 생성 방지: nextBufferTimeout 관리로 동시 스케줄러 방지

class AudioStreamer {
  constructor(context) {
    this.context = context;
    this.sampleRate = 24000;
    this.minChunkSize = 2048; // 약 85ms @24kHz
    this.audioQueue = [];
    this.leftover = new Float32Array(0);
    this.isPlaying = false;
    this.isStreamComplete = false;
    this.nextBufferTimeout = null;
    this.scheduledTime = 0;
    this.initialBufferTime = 0.15; // 150ms 초기 버퍼로 지터 노이즈 방어
    this.gainNode = this.context.createGain();
    this.gainNode.connect(this.context.destination);
    this.endOfQueueAudioSource = null;
    this.onComplete = () => {};
    this.addPCM16 = this.addPCM16.bind(this);
  }

  _processPCM16Chunk(chunk) {
    const float32Array = new Float32Array(Math.floor(chunk.length / 2));
    const dataView = new DataView(chunk.buffer, chunk.byteOffset, chunk.byteLength);
    for (let i = 0; i < float32Array.length; i++) {
      try {
        float32Array[i] = dataView.getInt16(i * 2, true) / 32768;
      } catch (e) {
        // ignore malformed tail
      }
    }
    return float32Array;
  }

  // chunk: Uint8Array (raw PCM16 little-endian)
  addPCM16(chunk) {
    this.isStreamComplete = false;
    const newSamples = this._processPCM16Chunk(chunk);

    // 샘플 축적 (leftover와 신규 샘플 결합)
    const combined = new Float32Array(this.leftover.length + newSamples.length);
    combined.set(this.leftover);
    combined.set(newSamples, this.leftover.length);

    let processingBuffer = combined;
    while (processingBuffer.length >= this.minChunkSize) {
      this.audioQueue.push(processingBuffer.slice(0, this.minChunkSize));
      processingBuffer = processingBuffer.slice(this.minChunkSize);
    }
    this.leftover = processingBuffer;

    if (this.audioQueue.length > 0) {
      // 1. 아직 재생 전이거나, 2. 중간에 큐가 비어 재생헤드가 스케줄 시점보다 지나간 경우 (버퍼 언더런 복구)
      if (!this.isPlaying || this.scheduledTime < this.context.currentTime) {
        this.isPlaying = true;
        this.scheduledTime = this.context.currentTime + this.initialBufferTime;
      }
      this.scheduleNextBuffer();
    }
  }

  createAudioBuffer(audioData) {
    const audioBuffer = this.context.createBuffer(1, audioData.length, this.sampleRate);
    audioBuffer.getChannelData(0).set(audioData);
    return audioBuffer;
  }

  scheduleNextBuffer() {
    const SCHEDULE_AHEAD_TIME = 0.25; // 최대 250ms 앞서 예약

    if (this.nextBufferTimeout) {
      clearTimeout(this.nextBufferTimeout);
      this.nextBufferTimeout = null;
    }

    while (
      this.audioQueue.length > 0 &&
      this.scheduledTime < this.context.currentTime + SCHEDULE_AHEAD_TIME
    ) {
      const audioData = this.audioQueue.shift();
      const audioBuffer = this.createAudioBuffer(audioData);
      const source = this.context.createBufferSource();

      if (this.audioQueue.length === 0) {
        if (this.endOfQueueAudioSource) this.endOfQueueAudioSource.onended = null;
        this.endOfQueueAudioSource = source;
        source.onended = () => {
          if (!this.audioQueue.length && this.endOfQueueAudioSource === source) {
            this.endOfQueueAudioSource = null;
            this.onComplete();
          }
        };
      }

      source.buffer = audioBuffer;
      source.connect(this.gainNode);

      // 과거 시점에 재생되지 않도록 보정하며 스케줄
      const startTime = Math.max(this.scheduledTime, this.context.currentTime);
      source.start(startTime);
      this.scheduledTime = startTime + audioBuffer.duration;
    }

    // 큐에 잔여량이 있다면 예약 한계 시점 직전에 다음 예약을 실행하도록 타이머 설정
    if (this.audioQueue.length > 0) {
      const nextCheckTime = (this.scheduledTime - this.context.currentTime) * 1000;
      this.nextBufferTimeout = setTimeout(() => {
        this.nextBufferTimeout = null;
        this.scheduleNextBuffer();
      }, Math.max(0, nextCheckTime - 50));
    } else if (this.isStreamComplete) {
      this.isPlaying = false;
    }
  }

  // 턴 종료(turn_complete) 시 남은 잔여 샘플 강제 렌더링
  flush() {
    if (this.leftover && this.leftover.length > 0) {
      this.audioQueue.push(this.leftover);
      this.leftover = new Float32Array(0);

      if (!this.isPlaying || this.scheduledTime < this.context.currentTime) {
        this.isPlaying = true;
        this.scheduledTime = this.context.currentTime + this.initialBufferTime;
      }
      this.scheduleNextBuffer();
    }
  }

  // 즉시 중단 + 큐 비우기 (화자 전환 시)
  stop() {
    this.isPlaying = false;
    this.isStreamComplete = true;
    this.audioQueue = [];
    this.leftover = new Float32Array(0);
    this.scheduledTime = this.context.currentTime;
    if (this.nextBufferTimeout) {
      clearTimeout(this.nextBufferTimeout);
      this.nextBufferTimeout = null;
    }
    try {
      this.gainNode.gain.linearRampToValueAtTime(0, this.context.currentTime + 0.05);
    } catch (e) {}
    setTimeout(() => {
      try { this.gainNode.disconnect(); } catch (e) {}
      this.gainNode = this.context.createGain();
      this.gainNode.connect(this.context.destination);
    }, 100);
  }

  async resume() {
    if (this.context.state === "suspended") await this.context.resume();
    this.isStreamComplete = false;
    this.scheduledTime = this.context.currentTime + this.initialBufferTime;
    try { this.gainNode.gain.setValueAtTime(1, this.context.currentTime); } catch (e) {}
  }
}
