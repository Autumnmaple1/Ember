// One owner for playback. Stopping sound never claims to cancel model generation.
import { SpeechAudioOutput } from './speechAudio.js';
export class AudioController {
  constructor(onChange, createAudio = null) {
    this.onChange = onChange;
    this.output = createAudio ? null : new SpeechAudioOutput();
    this.createAudio = createAudio || (source => this.output.source(source));
    this.enabled = false;
    this.queue = [];
    this.knownMessages = new Set();
    this.suppressed = new Set();
    this.requests = new Set();
    this.current = null;
    this.version = 0;
    this.volume = 1;
    this.paused = false;
    this.acquire = null;
    this.preparing = false;
    this.blocked = null;
  }

  notify(state, audio = null, error = '') { this.onChange({ state, audio, error, canResume: !!this.blocked }); }

  prepare() { this.output?.prepare(); }
  dispose() { this.stop(); this.output?.close(); }

  beginMessage(id) {
    if (!id) return;
    this.knownMessages.add(id);
    if (!this.enabled) this.suppressed.add(id);
    // Recent IDs only; no conversation text is retained here.
    if (this.knownMessages.size > 1024) {
      const oldest = this.knownMessages.values().next().value;
      this.knownMessages.delete(oldest);
      this.suppressed.delete(oldest);
    }
  }

  setEnabled(enabled) {
    this.enabled = enabled;
    if (!enabled) this.stop();
  }

  setVolume(value) {
    this.volume = Math.max(0, Math.min(1, Number(value) || 0));
    if (this.current) this.current.volume = this.volume;
  }

  async togglePause() {
    const audio = this.current;
    if (!audio) return;
    if (!this.paused) {
      audio.pause();
      this.paused = true;
      this.notify('paused', audio);
    } else {
      this.output?.resume();
      await this.playCurrent(audio, this.version);
    }
  }

  suppressMessage(id) {
    this.beginMessage(id);
    if (id) this.suppressed.add(id);
  }

  stop() {
    this.version += 1;
    this.knownMessages.forEach(id => this.suppressed.add(id));
    this.requests.clear();
    this.queue = [];
    this.preparing = false;
    this.blocked = null;
    this.output?.cancelPreparation();
    if (this.current) {
      this.current.onended = null;
      this.current.onerror = null;
      this.current.pause();
      this.current.removeAttribute('src');
      this.current.load();
    } else this.output?.reset();
    this.current = null;
    this.paused = false;
    this.notify('idle');
  }

  request(id) {
    this.stop();
    this.prepare();
    this.requests.add(id);
    this.notify('loading');
  }

  failRequest(id, error) {
    if (!this.requests.delete(id)) return;
    this.notify('failed', null, error);
  }

  fail(event) {
    const error = event.content || '语音生成失败，请重试。';
    if (event.request_id) { this.failRequest(event.request_id, error); return; }
    if (!this.enabled || !this.knownMessages.has(event.message_id) || this.suppressed.has(event.message_id)
      || this.current || this.requests.size) return;
    this.notify('failed', null, error);
  }

  receive(event) {
    const manual = event.request_id && this.requests.delete(event.request_id);
    if (event.request_id && !manual) return;
    if (!manual && (!this.enabled || this.suppressed.has(event.message_id))) {
      this.beginMessage(event.message_id);
      return;
    }
    this.queue.push({ ...event, manual: !!manual });
    this.playNext();
  }

  async playNext() {
    if (this.current || this.preparing || !this.queue.length) return;
    const event = this.queue.shift();
    const requestedVersion = this.version;
    this.preparing = true;
    const acquired = this.acquire ? await this.acquire(event.manual) : true;
    if (requestedVersion !== this.version) return;
    this.preparing = false;
    if (!acquired) { this.queue = []; this.notify('blocked', null, '声音由其他标签页播放；点击“重播这句”可在此接管。'); return; }
    const audio = this.createAudio(`data:audio/${event.format || 'mp3'};base64,${event.audio_base64 || event.content}`);
    audio.volume = this.volume;
    const version = ++this.version;
    this.current = audio;
    this.blocked = null;
    const finish = () => {
      if (this.current !== audio || this.version !== version) return;
      this.current = null;
      this.notify('idle');
      this.playNext();
    };
    audio.onended = finish;
    audio.onerror = () => {
      if (this.current !== audio || this.version !== version) return;
      this.current = null;
      this.queue = [];
      this.notify('failed', null, '声音加载失败，可以重播这句。');
    };
    this.notify('loading');
    await this.playCurrent(audio, version);
  }

  retryBlocked() {
    const pending = this.blocked;
    if (!pending || pending.version !== this.version) return;
    this.blocked = null;
    this.current = pending.audio;
    this.output?.resume();
    // No await before play(): preserve the recovery button's user activation.
    this.playCurrent(pending.audio, pending.version);
  }

  async playCurrent(audio, version) {
    try {
      await audio.play();
      if (version !== this.version || this.current !== audio) {
        if (this.current !== audio) audio.pause();
        return;
      }
      this.paused = false;
      this.notify('playing', audio);
    } catch (error) {
      if (version !== this.version || this.current !== audio) return;
      this.current = null;
      this.queue = [];
      this.blocked = error.name === 'NotAllowedError' ? { audio, version } : null;
      this.notify(error.name === 'NotAllowedError' ? 'blocked' : 'failed', null,
        error.name === 'NotAllowedError' ? '浏览器尚未允许自动播放，点击“启用声音并播放”即可继续。' : '声音播放失败，可以重播这句。');
    }
  }
}
