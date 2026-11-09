// Web Locks provide exclusive ownership; BroadcastChannel is only the takeover signal.
export class PlaybackOwner {
  constructor(name, onLost) {
    this.name = name;
    this.id = crypto.randomUUID();
    this.onLost = onLost;
    this.releaseLock = null;
    this.leaseKey = `${name}:lease`;
    this.leaseTimer = null;
    this.closed = false;
    this.storageChanged = event => {
      if (event.key === this.leaseKey && this.leaseTimer && this.readLease()?.id !== this.id) {
        this.onLost(); this.release();
      }
    };
    globalThis.addEventListener?.('storage', this.storageChanged);
    this.channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(name) : null;
    if (this.channel) this.channel.onmessage = ({ data }) => {
      if (data.type === 'takeover' && data.id !== this.id && (this.releaseLock || this.leaseTimer)) {
        this.onLost();
        this.release();
      }
    };
  }
  async claim(manual = false) {
    if (this.closed) return false;
    if (this.releaseLock) return true;
    // Web Locks is absent on HTTP LAN pages and in some mobile browsers.
    if (!navigator.locks) return this.claimLease(manual);
    if (manual) {
      this.channel?.postMessage({ type: 'takeover', id: this.id });
      await new Promise(resolve => setTimeout(resolve, 80));
    }
    return new Promise(resolve => {
      navigator.locks.request(this.name, { ifAvailable: true }, lock => {
        if (!lock || this.closed) { resolve(false); return; }
        return new Promise(release => { this.releaseLock = release; resolve(true); });
      }).catch(() => resolve(false));
    });
  }
  readLease() { try { return JSON.parse(localStorage.getItem(this.leaseKey)); } catch { return null; } }
  async claimLease(manual) {
    try {
      const lease = this.readLease();
      if (lease?.id === this.id && lease.until > Date.now()) return true;
      if (!manual && lease?.until > Date.now()) return false;
      if (manual) this.channel?.postMessage({ type: 'takeover', id: this.id });
      const write = () => localStorage.setItem(this.leaseKey, JSON.stringify({ id: this.id, until: Date.now() + 5000 }));
      write();
      await new Promise(resolve => setTimeout(resolve, 100));
      if (this.closed || this.readLease()?.id !== this.id) return false;
      this.leaseTimer = setInterval(() => {
        try {
          if (this.readLease()?.id !== this.id) { this.onLost(); this.release(); }
          else write();
        } catch { this.onLost(); this.release(); }
      }, 1000);
      return true;
    } catch { return manual; }
  }
  release() {
    this.releaseLock?.(); this.releaseLock = null;
    clearInterval(this.leaseTimer); this.leaseTimer = null;
    try { if (this.readLease()?.id === this.id) localStorage.removeItem(this.leaseKey); } catch { /* storage unavailable */ }
  }
  close() { this.closed = true; this.release(); this.channel?.close(); globalThis.removeEventListener?.('storage', this.storageChanged); }
}
