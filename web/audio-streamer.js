// AudioStreamer — 번역 음성 연속 재생 엔진
// ------------------------------------------------------------
// 구글 공식 live-api-web-console 의 src/lib/audio-streamer.ts (Apache-2.0) 를
// 의존성 없는 순수 JS로 이식한 버전. 24kHz PCM16 → Float32 변환 후,
// 큐잉 + AudioContext 시간축 스케줄링으로 끊김/지지직 없이 연속 재생한다.
//
// 핵심:
//  - 들어온 PCM을 7680샘플(약 320ms) 단위로 묶어 큐에 적재 (조각 과다 방지)
//  - 100ms 초기 지터버퍼 후 재생 시작
//  - scheduledTime 을 누적하며 "과거에 예약하지 않도록" max(scheduledTime, now) 보정
//  - 큐가 비면 100ms 간격으로 폴링하다가 새 오디오가 오면 이어서 스케줄
//  - createBufferSource 기반(워크릿 아님) → 모바일 무음 버그 없음

class AudioStreamer {
  constructor(context) {
    this.context = context;
    this.sampleRate = 24000;
    this.bufferSize = 7680; // 약 320ms @24kHz
    this.audioQueue = [];
    this.isPlaying = false;
    this.isStreamComplete = false;
    this.checkInterval = null;
    this.scheduledTime = 0;
    this.initialBufferTime = 0.1; // 100ms 초기 버퍼
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
    let processingBuffer = this._processPCM16Chunk(chunk);
    while (processingBuffer.length >= this.bufferSize) {
      this.audioQueue.push(processingBuffer.slice(0, this.bufferSize));
      processingBuffer = processingBuffer.slice(this.bufferSize);
    }
    if (processingBuffer.length > 0) this.audioQueue.push(processingBuffer);

    if (!this.isPlaying) {
      this.isPlaying = true;
      this.scheduledTime = this.context.currentTime + this.initialBufferTime;
      this.scheduleNextBuffer();
    }
  }

  createAudioBuffer(audioData) {
    const audioBuffer = this.context.createBuffer(1, audioData.length, this.sampleRate);
    audioBuffer.getChannelData(0).set(audioData);
    return audioBuffer;
  }

  scheduleNextBuffer() {
    const SCHEDULE_AHEAD_TIME = 0.2;
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

      // 절대 과거 시점에 예약하지 않도록 보정
      const startTime = Math.max(this.scheduledTime, this.context.currentTime);
      source.start(startTime);
      this.scheduledTime = startTime + audioBuffer.duration;
    }

    if (this.audioQueue.length === 0) {
      if (this.isStreamComplete) {
        this.isPlaying = false;
        if (this.checkInterval) {
          clearInterval(this.checkInterval);
          this.checkInterval = null;
        }
      } else if (!this.checkInterval) {
        this.checkInterval = window.setInterval(() => {
          if (this.audioQueue.length > 0) this.scheduleNextBuffer();
        }, 100);
      }
    } else {
      const nextCheckTime = (this.scheduledTime - this.context.currentTime) * 1000;
      setTimeout(() => this.scheduleNextBuffer(), Math.max(0, nextCheckTime - 50));
    }
  }

  // 즉시 중단 + 큐 비우기 (인터럽트/화자 강제전환 시)
  stop() {
    this.isPlaying = false;
    this.isStreamComplete = true;
    this.audioQueue = [];
    this.scheduledTime = this.context.currentTime;
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
    try {
      this.gainNode.gain.linearRampToValueAtTime(0, this.context.currentTime + 0.1);
    } catch (e) {}
    setTimeout(() => {
      try { this.gainNode.disconnect(); } catch (e) {}
      this.gainNode = this.context.createGain();
      this.gainNode.connect(this.context.destination);
    }, 200);
  }

  async resume() {
    if (this.context.state === "suspended") await this.context.resume();
    this.isStreamComplete = false;
    this.scheduledTime = this.context.currentTime + this.initialBufferTime;
    try { this.gainNode.gain.setValueAtTime(1, this.context.currentTime); } catch (e) {}
  }
}
