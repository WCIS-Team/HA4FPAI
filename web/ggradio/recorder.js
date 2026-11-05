/* Web Audio capture; no compressed MediaRecorder formats. Pure helpers are also usable in Node. */
(function (root) {
  'use strict';
  const WAV_RATE = 16000;
  const MAX_SECONDS = 300;

  function resampleMono(samples, sourceRate, targetRate = WAV_RATE) {
    if (!Number.isFinite(sourceRate) || sourceRate <= 0 || !Number.isFinite(targetRate) || targetRate <= 0)
      throw new RangeError('采样率必须为正数');
    if (!samples.length) throw new RangeError('没有录到音频');
    if (sourceRate === targetRate) return Float32Array.from(samples);
    const output = new Float32Array(Math.max(1, Math.round(samples.length * targetRate / sourceRate)));
    // Windowed sinc interpolation, with a lower cutoff when downsampling to avoid aliasing.
    const cutoff = Math.min(1, targetRate / sourceRate);
    const radius = Math.ceil(12 / cutoff);
    for (let i = 0; i < output.length; i++) {
      const position = i * sourceRate / targetRate;
      let total = 0, weight = 0;
      for (let j = Math.max(0, Math.ceil(position - radius)); j <= Math.min(samples.length - 1, Math.floor(position + radius)); j++) {
        const distance = position - j;
        const x = Math.PI * distance * cutoff;
        const sinc = Math.abs(x) < 1e-8 ? 1 : Math.sin(x) / x;
        const w = sinc * (0.5 + 0.5 * Math.cos(Math.PI * distance / radius));
        total += samples[j] * w; weight += w;
      }
      output[i] = weight ? total / weight : 0;
    }
    return output;
  }

  function encodeWavPCM16(samples, sourceRate) {
    const pcm = resampleMono(samples, sourceRate);
    const buffer = new ArrayBuffer(44 + pcm.length * 2), view = new DataView(buffer);
    const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, WAV_RATE, true); view.setUint32(28, WAV_RATE * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) {
      const value = Math.max(-1, Math.min(1, Number.isFinite(pcm[i]) ? pcm[i] : 0));
      view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
    }
    return buffer;
  }

  function microphoneError(error) {
    const messages = {
      NotAllowedError: '麦克风权限被拒绝。请在浏览器站点权限中允许麦克风，并确认页面使用 HTTPS 或 localhost。',
      SecurityError: '浏览器安全策略禁止麦克风，请检查站点权限或嵌入页面的麦克风策略。',
      NotFoundError: '未找到麦克风，请连接设备后重试。',
      NotReadableError: '无法读取麦克风，设备可能被其他程序占用。',
      OverconstrainedError: '麦克风不支持请求的设置，请更换设备。'
    };
    return messages[error.name] || ('录音失败：' + (error.message || error));
  }

  class Recorder {
    constructor(onLimit) { this.onLimit = onLimit; this.state = 'idle'; this.chunks = []; this.frames = 0; this.generation = 0; }
    async start() {
      if (this.state !== 'idle') throw new Error('录音正在进行或正在等待权限');
      if (!root.isSecureContext) throw new Error('麦克风需要安全上下文：请使用 HTTPS 或 http://localhost；普通局域网 HTTP 地址不能录音，可改用上传文件。');
      if (!root.navigator.mediaDevices || !root.navigator.mediaDevices.getUserMedia) throw new Error('浏览器不支持麦克风采集，可改用上传文件。');
      const AudioContext = root.AudioContext || root.webkitAudioContext;
      if (!AudioContext) throw new Error('浏览器不支持 Web Audio，可改用上传文件。');
      this.state = 'starting'; this.chunks = []; this.frames = 0;
      const generation = ++this.generation;
      try {
        // Create/resume on the button gesture; use the actual context rate for encoding.
        this.context = new AudioContext();
        await this.context.resume();
        const stream = await root.navigator.mediaDevices.getUserMedia({audio: {channelCount: {ideal: 1}, sampleRate: {ideal: WAV_RATE}}, video: false});
        if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return false; }
        this.stream = stream;
        this.rate = this.context.sampleRate;
        this.source = this.context.createMediaStreamSource(stream);
        this.processor = this.context.createScriptProcessor(4096, 1, 1);
        this.silence = this.context.createGain(); this.silence.gain.value = 0;
        this.state = 'recording';
        this.processor.onaudioprocess = event => {
          if (this.state !== 'recording') return;
          const input = event.inputBuffer;
          const count = Math.min(input.length, Math.round(this.rate * MAX_SECONDS) - this.frames);
          if (count > 0) {
            const mono = new Float32Array(count);
            for (let channel = 0; channel < input.numberOfChannels; channel++) {
              const values = input.getChannelData(channel);
              for (let i = 0; i < count; i++) mono[i] += values[i] / input.numberOfChannels;
            }
            this.chunks.push(mono); this.frames += count;
          }
          if (this.frames >= Math.round(this.rate * MAX_SECONDS)) this.finishAutomatically();
        };
        this.source.connect(this.processor); this.processor.connect(this.silence); this.silence.connect(this.context.destination);
        this.timer = root.setTimeout(() => this.finishAutomatically(), MAX_SECONDS * 1000);
        stream.getAudioTracks().forEach(track => { track.onended = () => this.finishAutomatically(); });
        return true;
      } catch (error) { if (generation === this.generation) this.dispose(); throw error; }
    }
    finishAutomatically() {
      if (this.state !== 'recording') return;
      try { const blob = this.stop(); if (this.onLimit) this.onLimit(blob); }
      catch (error) { if (this.onLimit) this.onLimit(null, error); }
    }
    stop() {
      if (this.state !== 'recording') throw new Error('当前没有正在进行的录音');
      const rate = this.rate, frames = this.frames, chunks = this.chunks;
      this.dispose();
      if (!frames) throw new Error('没有录到音频，请录制片刻再停止');
      const samples = new Float32Array(frames); let offset = 0;
      for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
      return new Blob([encodeWavPCM16(samples, rate)], {type: 'audio/wav'});
    }
    dispose() {
      ++this.generation; this.state = 'idle'; root.clearTimeout(this.timer);
      if (this.processor) this.processor.onaudioprocess = null;
      for (const node of [this.source, this.processor, this.silence]) { if (node) { try { node.disconnect(); } catch (_) {} } }
      if (this.stream) this.stream.getTracks().forEach(track => { track.onended = null; track.stop(); });
      if (this.context && this.context.state !== 'closed') this.context.close().catch(() => {});
      this.stream = this.context = this.source = this.processor = this.silence = null;
      this.chunks = []; this.frames = 0;
    }
  }
  const api = {WAV_RATE, MAX_SECONDS, resampleMono, encodeWavPCM16, microphoneError, Recorder};
  root.GGRadioRecorder = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
