// Punch detection in the browser: a line-by-line port of the Python pipeline.
//
//   pose_tracker.py   -> Smoother, punchTravel, bodyState, Engine (feature extraction and calibration)
//   punch_detector.py -> SETTING_DEFAULTS, makeSettings, PunchDetector
//   variants/angled_camera/__init__.py -> bodyAngle, AngleGuide
//
// Keep it in step with the Python files. The detector is tuned there (see replay_punches.py), so a
// change to punch_detector.py or a variant's settings.json must be copied here. Everything is in
// shoulder-widths and seconds, exactly as in Python.

export const NOSE = 0, L_SH = 11, R_SH = 12, L_EL = 13, R_EL = 14, L_WR = 15, R_WR = 16, L_HIP = 23, R_HIP = 24;
export const CONNECTIONS = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [11, 23], [12, 24], [23, 24],
  [23, 25], [24, 26], [25, 27], [26, 28], [0, 11], [0, 12],
];

const GUARD_DIST = 1.0;  // wrist this close to the nose => hand is up
const DUCK_DROP = 0.45;  // nose below baseline by this much => duck
const SLIP_SHIFT = 0.5;  // nose sideways from baseline by this much => slip

// --- small vector helpers (points are plain arrays) ---
const sub = (a, b) => a.map((v, i) => v - b[i]);
const norm = (a) => Math.sqrt(a.reduce((s, v) => s + v * v, 0));
const dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);

/** Exponential moving average over an array of points. */
export class Smoother {
  constructor(alpha) { this.alpha = alpha; this.prev = null; }
  update(points) {
    this.prev = this.prev === null
      ? points.map((p) => p.slice())
      : points.map((p, i) => p.map((v, j) => this.alpha * v + (1 - this.alpha) * this.prev[i][j]));
    return this.prev;
  }
}

/** Per hand [extension, right, up] in shoulder-widths (see pose_tracker.punch_travel). */
export function punchTravel(delta, start, end, world, sw) {
  let right = sub(world[R_SH], world[L_SH]);
  right[1] = 0;
  const len = norm(right) + 1e-6;
  right = right.map((v) => v / len);
  return [0, 1].map((i) => [
    (norm(end[i]) - norm(start[i])) / sw,
    dot(delta[i], right),
    -delta[i][1],
  ]);
}

/** Guard and dodge labels from the current pose (see pose_tracker.body_state). */
export function bodyState(img, world, swWorld, baseline) {
  const states = [];
  if (baseline) {
    const drop = (img[NOSE][1] - baseline.nose[1]) / baseline.sw;
    const shift = (img[NOSE][0] - baseline.nose[0]) / baseline.sw;
    if (drop > DUCK_DROP) states.push('DUCK');
    if (Math.abs(shift) > SLIP_SHIFT) states.push('SLIP ' + (shift > 0 ? 'RIGHT' : 'LEFT'));
  }
  const up = [L_WR, R_WR].map((i) => norm(sub(world[i], world[NOSE])) / swWorld < GUARD_DIST);
  if (up.every(Boolean)) states.push('BLOCK');
  else if (!up.some(Boolean)) states.push('GUARD DOWN');
  return states;
}

// ---------------------------------------------------------------------------------------------
// Punch detector settings and the burst-based detector (punch_detector.py)
// ---------------------------------------------------------------------------------------------

export const SETTING_DEFAULTS = Object.freeze({
  start_speed: 5.0, start_frames: 2, drop_ratio: 0.8, max_wait: 0.25, rearm_speed: 4.0, min_gap: 0.45,
  min_inward: 0.7, min_up: 0.8, guard_raise_up: 0.35,
  straight_ext: null, straight_max_up: null, straight_min_travel: 0.9, far_hand_hold: null,
  early: true, early_inward: 1.2, early_up: 1.2, early_ratio: 1.3, lead: 'L',
});

/** Defaults plus the given values. A misspelt name is an error, not ignored (like Settings.from_dict). */
export function makeSettings(values = {}) {
  const unknown = Object.keys(values).filter((k) => !(k in SETTING_DEFAULTS) && !k.startsWith('_'));
  if (unknown.length) throw new Error(`unknown punch setting(s) ${unknown.join(', ')}`);
  const out = { ...SETTING_DEFAULTS };
  for (const [k, v] of Object.entries(values)) if (k in SETTING_DEFAULTS) out[k] = v;
  return out;
}

class Hand {
  constructor(side) { this.side = side; this.armed = true; this.lastFire = -1e9; this.reset(); }
  reset() {
    this.over = 0; this.active = false;
    this.began = 0; this.peak = 0;
    this.ext = 0; this.inward = 0; this.up = 0;
    this.low = false;
  }
}

/** Feed update() every frame. It returns the punches decided this frame as [side, kind] pairs. */
export class PunchDetector {
  constructor(settings = SETTING_DEFAULTS) {
    this.s = settings;
    this.hands = { L: new Hand('L'), R: new Hand('R') };
    this.nearFastAt = -1e9;  // when the near (lead) hand was last moving fast
  }

  /** speeds = [left, right]; travel = 2x3 (extension, right, up) per hand; fromLow = [left, right]. */
  update(now, speeds, travel, fromLow) {
    if (speeds[this.s.lead === 'L' ? 0 : 1] > this.s.start_speed) this.nearFastAt = now;
    const decided = [];
    ['L', 'R'].forEach((side, i) => {
      const kind = this._step(this.hands[side], now, speeds[i], travel[i], fromLow[i]);
      if (kind !== null) decided.push([side, kind]);
    });
    return this._dropGuardRaises(decided, travel, fromLow);
  }

  _step(h, now, speed, travel, low) {
    const s = this.s;
    const [ext, right, up] = travel;
    const inward = h.side === 'L' ? right : -right;
    if (!h.armed) h.armed = now - h.lastFire >= s.min_gap && speed < s.rearm_speed;
    if (!h.active) {
      if (!h.armed || speed <= s.start_speed) { h.over = 0; return null; }
      if (h.over === 0) { h.began = now; h.peak = 0; h.low = false; h.ext = h.inward = h.up = 0; }
      h.over += 1;
    }
    // the hand is fast: keep measuring the burst
    h.peak = Math.max(h.peak, speed);
    h.low = h.low || Boolean(low);
    h.ext = Math.max(h.ext, ext); h.inward = Math.max(h.inward, inward); h.up = Math.max(h.up, up);
    if (h.over >= s.start_frames) h.active = true;
    if (!h.active) return null;
    let kind = s.early ? this._classifyEarly(h) : null;
    if (kind === null) {
      if (speed > s.drop_ratio * h.peak && now - h.began < s.max_wait) return null;  // still speeding up
      kind = this._classify(h);
    }
    if (kind !== null && this._echoOfNearHand(h)) kind = null;  // not a punch, so no lockout
    if (kind !== null) { h.lastFire = now; h.armed = false; }   // only a real punch locks the hand out
    h.reset();
    return kind;
  }

  _echoOfNearHand(h) {
    const s = this.s;
    return s.far_hand_hold !== null && h.side !== s.lead && this.nearFastAt >= h.began - s.far_hand_hold;
  }

  _straightFromGuard(h) {
    const s = this.s;
    return s.straight_max_up !== null && !h.low && h.up <= s.straight_max_up
      && Math.max(h.ext, h.inward) >= s.straight_min_travel;
  }

  _classifyEarly(h) {
    const s = this.s;
    const up = h.low ? h.up : 0.0;
    if (up >= s.early_up && up >= s.early_ratio * h.inward) return 'UPPERCUT';
    if (h.inward >= s.early_inward && h.inward >= s.early_ratio * Math.max(up, h.ext)) {
      if (s.straight_max_up !== null && !h.low && h.up <= s.straight_max_up) return null;
      return 'HOOK';
    }
    return null;
  }

  _classify(h) {
    const s = this.s;
    const straight = h.side === s.lead ? 'JAB' : 'CROSS';
    if (h.low && h.up >= s.min_up && h.up >= h.inward) return 'UPPERCUT';
    if (this._straightFromGuard(h)) return straight;
    if (h.inward >= s.min_inward && h.inward >= h.ext) return 'HOOK';
    if (s.straight_ext !== null && h.ext >= s.straight_ext) return straight;
    return null;
  }

  /** An uppercut while the other hand is also rising from low is both hands raising guard. */
  _dropGuardRaises(decided, travel, fromLow) {
    const out = [];
    for (const [side, kind] of decided) {
      if (kind === 'UPPERCUT') {
        const j = side === 'L' ? 1 : 0;
        const other = this.hands[side === 'L' ? 'R' : 'L'];
        const otherRising = travel[j][2] >= this.s.guard_raise_up && (Boolean(fromLow[j]) || other.low);
        if (otherRising) { other.lastFire = -1e9; continue; }
      }
      out.push([side, kind]);
    }
    return out;
  }
}

// ---------------------------------------------------------------------------------------------
// Per-frame features, calibration and states (the loop in pose_tracker.main)
// ---------------------------------------------------------------------------------------------

/**
 * Turns landmarks into body states and punches, one frame at a time.
 * imgRaw: 33 x [x, y] already mirrored (x = 1 - x), world: 33 x [x, y, z] in metres, vis: 33 numbers.
 */
export class Engine {
  constructor(settings = SETTING_DEFAULTS) {
    this.detector = new PunchDetector(settings);
    this.smoothImg = new Smoother(0.6);
    this.smoothWorld = new Smoother(0.7);
    this.history = [];      // [time, wrist-relative-to-shoulder (2x3), wrist-below-elbow (2)], last 5 frames
    this.baseline = null;   // set by calibrate()
    this.img = null;        // latest smoothed image landmarks, while a body is in view
    this.bodyInView = false;
  }

  /** New detector settings (a different mode or lead hand). Keeps the calibration. */
  setSettings(settings) { this.detector = new PunchDetector(settings); }

  /** Remember where the head is now as the neutral position. False if nobody is in view. */
  calibrate() {
    if (!this.bodyInView || !this.img) return false;
    const img = this.img;
    this.baseline = { nose: img[NOSE].slice(), sw: norm(sub(img[L_SH], img[R_SH])) };
    return true;
  }

  get calibrated() { return this.baseline !== null; }

  /** Nobody in view: no states, so every held key is let go. */
  noBody() {
    this.bodyInView = false;
    return { body: false, states: [], punches: [], speeds: [0, 0] };
  }

  process(now, imgRaw, worldRaw, vis) {
    const img = this.smoothImg.update(imgRaw);
    const world = this.smoothWorld.update(worldRaw);
    this.img = img;
    this.bodyInView = true;
    const sw = norm(sub(world[L_SH], world[R_SH])) + 1e-6;

    const rel = [sub(world[L_WR], world[L_SH]), sub(world[R_WR], world[R_SH])];
    const below = [world[L_WR][1] > world[L_EL][1], world[R_WR][1] > world[R_EL][1]];  // world y points down
    this.history.push([now, rel, below]);
    if (this.history.length > 5) this.history.shift();

    const first = this.history[0], last = this.history[this.history.length - 1];
    const dt = now - first[0];
    let speeds = [0, 0], punches = [];
    if (dt > 0) {
      const delta = [0, 1].map((i) => sub(last[1][i], first[1][i]).map((v) => v / sw));
      const travel = punchTravel(delta, first[1], last[1], world, sw);
      speeds = delta.map((d) => norm(d) / dt);
      punches = this.detector.update(now, speeds, travel, first[2]);
    }
    const states = bodyState(img, world, sw, this.baseline);
    return { body: true, img, world, vis, sw, speeds, punches, states };
  }
}

// ---------------------------------------------------------------------------------------------
// Body angle guide (variants/angled_camera)
// ---------------------------------------------------------------------------------------------

/** How far the shoulder line is turned from facing the camera: 0 = square on, 90 = side on. */
export function bodyAngle(world) {
  const line = sub(world[R_SH], world[L_SH]);
  return Math.atan2(Math.abs(line[2]), Math.abs(line[0])) * 180 / Math.PI;
}

const STALE_SECONDS = 0.5;  // a reading older than this is not trusted: nobody is in view

export class AngleGuide {
  constructor(target = 45, tolerance = 10, smoothing = 0.3) {
    this.target = target; this.tolerance = tolerance; this.smoothing = smoothing;
    this.angle = null; this.updated = -1e9;
  }

  onPose(world, vis, now) {
    if (Math.min(vis[L_SH], vis[R_SH]) < 0.5) return;  // a shoulder is hidden, so the angle would be a guess
    if (now - this.updated > STALE_SECONDS) this.angle = null;  // they left and came back: start fresh
    const raw = bodyAngle(world);
    this.angle = this.angle === null ? raw : this.smoothing * raw + (1 - this.smoothing) * this.angle;
    this.updated = now;
  }

  current(now) { return now - this.updated <= STALE_SECONDS ? this.angle : null; }

  /** { text, level } where level is 'good', 'off' or 'lost'. */
  coach(now) {
    const angle = this.current(now);
    if (angle === null) return { text: 'Step into view', level: 'lost', angle: null };
    const off = angle - this.target;
    if (Math.abs(off) <= this.tolerance) return { text: 'Good, hold it', level: 'good', angle };
    return { text: off < 0 ? 'More sideways' : 'Face the camera more', level: 'off', angle };
  }
}
