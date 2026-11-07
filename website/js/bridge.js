// Client for keybridge.py, the small program on your laptop that presses real keys.
//
// A web page cannot press keys in another window, so for the real Browser Boxing the page sends the
// punches and body states to the bridge, and the bridge presses the mapped keys (game_input.py).
// The bridge also forwards the F7/F8/F9 hotkeys and the glove, which a web page cannot hear.

const BRIDGE_PORT = 8765;
const SILENT_MS = 4000;  // no message for this long: treat the bridge as gone

export class Bridge {
  /** handlers: onConnection(status | null), onHotkey(name), onOutput(enabled), onGlove(event). */
  constructor(handlers = {}) {
    this.h = handlers;
    this.base = null;
    this.status = null;
    this.source = null;
    this.lastHeard = 0;
    this.lastStates = '';
    this.lastStatesAt = 0;
  }

  get connected() { return this.base !== null; }

  /** Where to look: the page's own server if it is the bridge, then the usual local address. */
  _candidates() {
    const local = ['127.0.0.1', 'localhost'].includes(location.hostname);
    return [...(local ? [''] : []), `http://127.0.0.1:${BRIDGE_PORT}`];
  }

  start() {
    this._probe();
    setInterval(() => {
      if (!this.connected) this._probe();
      else if (performance.now() - this.lastHeard > SILENT_MS) this._lost();
    }, 3000);
  }

  async _probe() {
    for (const base of this._candidates()) {
      try {
        const response = await fetch(`${base}/bridge/status`, { cache: 'no-store', signal: AbortSignal.timeout(900) });
        const status = await response.json();
        if (response.ok && status.app === 'keybridge') { this._connect(base, status); return; }
      } catch { /* nothing there; try the next address */ }
    }
  }

  _connect(base, status) {
    this.base = base;
    this._setStatus(status);
    this.source = new EventSource(`${base}/bridge/events`);
    this.source.onmessage = (e) => this._message(JSON.parse(e.data));
    this.source.onerror = () => this._lost();
  }

  _lost() {
    this.source?.close();
    this.source = null;
    this.base = null;
    this.status = null;
    this.lastStates = '';
    this.h.onConnection?.(null);
  }

  _setStatus(status) {
    this.status = status;
    this.lastHeard = performance.now();
    this.h.onConnection?.(status);
  }

  _message(m) {
    this.lastHeard = performance.now();
    if (m.type === 'status') this._setStatus(m.status);
    else if (m.type === 'hotkey') this.h.onHotkey?.(m.name);
    else if (m.type === 'output') { this.status = { ...this.status, output: m.enabled }; this.h.onOutput?.(m.enabled); }
    else if (m.type === 'glove') this.h.onGlove?.(m);
  }

  _post(path, body) {
    if (!this.connected) return;
    // text/plain keeps this a "simple" request, so the browser sends no preflight for each punch
    fetch(`${this.base}/bridge/${path}`, {
      method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'text/plain' }, keepalive: true,
    }).catch(() => {});
  }

  punch(kind) { this._post('punch', { kind }); }

  /** Body states are held keys. Sent when they change, and every 400 ms otherwise so the bridge knows we are alive. */
  states(list, now = performance.now(), force = false) {
    const key = list.join('|');
    if (!force && key === this.lastStates && now - this.lastStatesAt < 400) return;
    this.lastStates = key;
    this.lastStatesAt = now;
    this._post('states', { states: list });
  }

  setOutput(enabled) {
    this._post('output', { enabled });
    if (!enabled) this.lastStates = '';
  }
}

/** The camera's punch only counts if that hand's glove felt one (glove_punch.GlovePunches.confirms). */
export class GloveConfirm {
  constructor() { this.reset(); }
  reset() { this.present = new Set(); this.active = { L: false, R: false }; this.lastPunch = { L: null, R: null }; }

  /** event: { side, active } while a punch is happening, or { side, punch: true, age } when one finished. */
  onEvent(e, now = performance.now() / 1000) {
    if (Array.isArray(e.gloves)) this.present = new Set(e.gloves);
    if (!e.side) return;
    this.present.add(e.side);
    if ('active' in e) this.active[e.side] = e.active;
    if (e.punch) this.lastPunch[e.side] = now - (e.age || 0);
  }

  /** A hand with no glove connected is never blocked, so a single glove still works. */
  confirms(side, now, within = 0.6) {
    if (!this.present.has(side)) return true;
    if (this.active[side]) return true;
    const t = this.lastPunch[side];
    return t !== null && now - t >= -0.1 && now - t <= within;
  }
}
