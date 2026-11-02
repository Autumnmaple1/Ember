// Web Locks provide exclusive ownership; BroadcastChannel is only the takeover signal.
export class PlaybackOwner {
  constructor(name, onLost) {
    this.name = name;
    this.id = crypto.randomUUID();
    this.onLost = onLost;
    this.releaseLock = null;
    this.channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(name) : null;
    if (this.channel) this.channel.onmessage = ({ data }) => {
      if (data.type === 'takeover' && data.id !== this.id && this.releaseLock) {
        this.onLost();
        this.release();
      }
    };
  }
  async claim(manual = false) {
    if (this.releaseLock) return true;
    // Fail closed for automatic sound if exclusive ownership cannot be enforced.
    if (!navigator.locks) return manual;
    if (manual) {
      this.channel?.postMessage({ type: 'takeover', id: this.id });
      await new Promise(resolve => setTimeout(resolve, 80));
    }
    return new Promise(resolve => {
      navigator.locks.request(this.name, { ifAvailable: true }, lock => {
        if (!lock) { resolve(false); return; }
        return new Promise(release => { this.releaseLock = release; resolve(true); });
      }).catch(() => resolve(false));
    });
  }
  release() { this.releaseLock?.(); this.releaseLock = null; }
  close() { this.release(); this.channel?.close(); }
}
