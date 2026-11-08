// All sound is made here, with the Web Audio API, in the style of old arcade games: square and triangle
// waves for tones, filtered noise for drums and hits. There are no sound files.
//
//   punch(kind, guarded)  a throw "whoosh" followed by an impact, different for each punch
//   music                 a shuffle beat (kick, snare, swung hi-hats, bass line) that plays during the fight
//   ready / fight         the "READY ... FIGHT!" jingles
//   warn / dodge / block / hurt / move / combo / tick / result   the rest of the game
//
// The punch sounds and the round bell are also made in Python for the training tool (punch_sounds.py).
// If you change punch(), _throw(), _impact() or roundBell() here, change punch_sounds.py to match.
//
// Every effect takes an optional last argument `at`, a time on the audio clock. SoundEngine can also be
// given any AudioContext, including an OfflineAudioContext, which is how the sounds are checked without
// speakers.

const BPM = 104;           // the shuffle's tempo
const HURRY_BPM = 126;     // ...for the last 15 seconds of a round
const STEPS = 48;          // 4 bars of 12 steps: a bar is 4 beats and a beat is 3 triplet steps, which is what makes it swing
const MUSIC_LEVEL = 0.4;   // the music sits under the effects

const hz = (midi) => 440 * 2 ** ((midi - 69) / 12);

// Bass notes (MIDI numbers) by bar and step. E minor blues: E, E, A, B.
const BASS = [
  { 0: 40, 2: 40, 3: 43, 5: 45, 6: 47, 8: 45, 9: 43, 11: 40 },
  { 0: 40, 2: 40, 3: 43, 5: 45, 6: 47, 8: 45, 9: 43, 11: 45 },
  { 0: 45, 2: 45, 3: 48, 5: 50, 6: 52, 8: 50, 9: 48, 11: 45 },
  { 0: 47, 2: 47, 3: 50, 5: 54, 6: 52, 8: 50, 9: 47, 11: 46 },
];
const STAB = { 1: [64, 71], 3: [62, 69] };         // a short two-note chord on beat 4 of bars 2 and 4
const HAT_STEPS = new Set([0, 2, 3, 5, 6, 8, 9, 11]);  // the long-short "shuffle" pattern

export class SoundEngine {
  /** Pass an AudioContext to use (for tests). Otherwise one is made on the first unlock(), which needs a click or key. */
  constructor(ctx = null) {
    this.ctx = null;
    this.hurry = false;               // faster music, set by the game near the end of a round
    this._muted = false;
    this._musicEnabled = true;        // the player's choice
    this._musicWanted = false;        // the game's request: it is in a fight
    this._timer = null;
    this._gates = {};
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', () => this._syncMusic());
    if (ctx) { this.ctx = ctx; this._build(); }
  }

  // --- set up ---

  unlock() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this.ctx = new AC();
      this._build();
    }
    this.ctx.resume?.();
    this._syncMusic();
  }

  _build() {
    const c = this.ctx;
    this.master = c.createGain();
    this.master.gain.value = this._muted ? 0 : 1;
    const compressor = c.createDynamicsCompressor();  // keeps a drum and three effects at once from clipping
    compressor.threshold.value = -14;
    compressor.ratio.value = 6;
    compressor.attack.value = 0.003;
    compressor.release.value = 0.15;
    this.sfx = c.createGain();
    this.sfx.gain.value = 0.9;
    this.music = c.createGain();
    this.music.gain.value = MUSIC_LEVEL;
    const limiter = c.createWaveShaper();  // a soft ceiling, so a loud moment saturates a little instead of clipping
    const curve = new Float32Array(2048);
    for (let i = 0; i < curve.length; i++) curve[i] = Math.tanh((i / (curve.length - 1)) * 2 - 1);
    limiter.curve = curve;
    this.sfx.connect(this.master);
    this.music.connect(this.master);
    this.master.connect(compressor);
    compressor.connect(limiter);
    limiter.connect(c.destination);

    const length = c.sampleRate * 2;  // two seconds of white noise, shared by every noise sound
    this.noiseBuffer = c.createBuffer(1, length, c.sampleRate);
    const data = this.noiseBuffer.getChannelData(0);
    for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
  }

  get muted() { return this._muted; }
  set muted(value) {
    this._muted = Boolean(value);
    if (this.master) this.master.gain.setTargetAtTime(this._muted ? 0 : 1, this.ctx.currentTime, 0.02);
    this._syncMusic();
  }

  get musicEnabled() { return this._musicEnabled; }
  set musicEnabled(value) { this._musicEnabled = Boolean(value); this._syncMusic(); }

  now() { return this.ctx ? this.ctx.currentTime : 0; }
  get _live() { return Boolean(this.ctx) && !this._muted; }  // can we make sound right now

  // --- building blocks ---

  /**
   * One oscillator with a short fade in and an exponential fade out.
   * options: to (end pitch), path ([[seconds, pitch], ...] for a sweep with several legs), attack, bus.
   */
  _tone(type, freq, t, dur, vol, { to = null, path = null, attack = 0.004, bus = this.sfx } = {}) {
    const osc = this.ctx.createOscillator(), gain = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    for (const [dt, f] of path || (to ? [[dur, to]] : [])) osc.frequency.exponentialRampToValueAtTime(f, t + dt);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.linearRampToValueAtTime(vol, t + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain).connect(bus);
    osc.start(t);
    osc.stop(t + dur + 0.03);
  }

  /** Filtered noise. kind: 'lowpass', 'highpass' or 'bandpass'. options: to (end cutoff), q, attack, bus. */
  _noise(kind, freq, t, dur, vol, { to = null, q = 1, attack = 0.002, bus = this.sfx } = {}) {
    const src = this.ctx.createBufferSource(), filter = this.ctx.createBiquadFilter(), gain = this.ctx.createGain();
    src.buffer = this.noiseBuffer;
    filter.type = kind;
    filter.Q.value = q;
    filter.frequency.setValueAtTime(freq, t);
    if (to) filter.frequency.exponentialRampToValueAtTime(to, t + dur);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.linearRampToValueAtTime(vol, t + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(filter).connect(gain).connect(bus);
    src.start(t, Math.random() * 0.7);
    src.stop(t + dur + 0.03);
  }

  /** False if the same sound played less than `seconds` ago: a tracker that flickers must not machine-gun. */
  _gate(name, seconds, t) {
    if (t - (this._gates[name] ?? -9) < seconds) return false;
    this._gates[name] = t;
    return true;
  }

  // --- punches: each one sounds different ---

  /** The whoosh of the throw, then the impact 45 ms later (or a clank if the bot was guarding). */
  punch(kind, guarded = false, at = this.now()) {
    if (!this._live) return;
    this._throw(kind, at);
    this._impact(kind, guarded, at + 0.045);
  }

  _throw(kind, t) {
    switch (String(kind).toUpperCase()) {
      case 'JAB':  // quick and light: a high "tik"
        this._noise('highpass', 4000, t, 0.07, 0.22, { to: 7000 });
        this._tone('square', 1200, t, 0.06, 0.1, { to: 1900 });
        break;
      case 'CROSS':  // longer and heavier: a falling "pyoo"
        this._noise('bandpass', 2000, t, 0.12, 0.26, { to: 800, q: 1.2 });
        this._tone('square', 560, t, 0.12, 0.13, { to: 240 });
        break;
      case 'HOOK':  // a wide swing: the pitch goes up and comes back down, "whoo-oop"
        this._noise('bandpass', 600, t, 0.24, 0.24, { to: 1800, q: 0.9 });
        this._tone('sawtooth', 240, t, 0.26, 0.15, { path: [[0.12, 720], [0.26, 200]] });
        break;
      case 'UPPERCUT':  // a rising zip, like an arcade power-up
        this._tone('square', 170, t, 0.2, 0.15, { to: 1500 });
        this._tone('triangle', 85, t, 0.2, 0.2, { to: 600 });
        this._noise('highpass', 1500, t, 0.2, 0.16, { to: 6000 });
        break;
      default:
        this._noise('bandpass', 1500, t, 0.1, 0.2);
    }
  }

  _impact(kind, guarded, t) {
    if (guarded) {  // a metallic clank
      this._tone('square', 1320, t, 0.06, 0.12);
      this._tone('square', 1976, t, 0.05, 0.08);
      this._noise('bandpass', 3200, t, 0.05, 0.2);
      return;
    }
    switch (String(kind).toUpperCase()) {
      case 'JAB':  // a tap
        this._noise('lowpass', 2200, t, 0.06, 0.3, { to: 600 });
        this._tone('sine', 260, t, 0.08, 0.35, { to: 120 });
        break;
      case 'CROSS':  // a thud
        this._noise('lowpass', 1500, t, 0.1, 0.4, { to: 400 });
        this._tone('sine', 170, t, 0.16, 0.55, { to: 55 });
        break;
      case 'HOOK':  // a bright slap, then the follow-through of the swing
        this._noise('bandpass', 3200, t, 0.09, 0.4, { to: 1100, q: 0.8 });
        this._tone('sine', 230, t, 0.18, 0.5, { to: 80 });
        this._tone('square', 95, t, 0.1, 0.12, { to: 60 });
        this._noise('bandpass', 1400, t + 0.07, 0.1, 0.22, { to: 500 });
        this._tone('sine', 110, t + 0.07, 0.2, 0.35, { to: 40 });
        break;
      case 'UPPERCUT':  // a boom, with an arcade "ting" on top
        this._noise('lowpass', 1100, t, 0.24, 0.5, { to: 200 });
        this._tone('sine', 120, t, 0.4, 0.8, { to: 32 });
        this._tone('square', 70, t, 0.15, 0.18, { to: 40 });
        this._tone('square', 1760, t + 0.03, 0.09, 0.09);
        break;
      default:
        this._noise('lowpass', 1500, t, 0.1, 0.3);
    }
  }

  /** A rising blip for each punch of a combo. */
  combo(count, at = this.now() + 0.08) {
    if (!this._live || count < 2) return;
    const note = 76 + (Math.min(count, 9) - 2) * 2;
    this._tone('square', hz(note), at, 0.09, 0.08);
    if (count >= 4) this._tone('square', hz(note + 12), at + 0.05, 0.09, 0.06);
  }

  // --- the bot and the defence ---

  /** The bot is winding up. SLIP! is two high beeps, DUCK! is a high beep then a low one. */
  warn(attack, at = this.now()) {
    if (!this._live) return;
    if (attack === 'HOOK') {
      this._tone('square', 1175, at, 0.06, 0.09);
      this._tone('square', 784, at + 0.1, 0.07, 0.09);
    } else {
      this._tone('square', 1568, at, 0.06, 0.09);
      this._tone('square', 1568, at + 0.1, 0.06, 0.09);
    }
  }

  dodge(at = this.now()) {  // a quick upward arpeggio, like picking up a coin
    if (!this._live) return;
    [79, 83, 86].forEach((note, i) => this._tone('square', hz(note), at + i * 0.05, 0.06, 0.09));
    this._tone('triangle', hz(91), at + 0.15, 0.14, 0.12);
  }

  block(at = this.now()) {  // glove on glove
    if (!this._live) return;
    this._tone('triangle', 330, at, 0.12, 0.3, { to: 180 });
    this._noise('bandpass', 2600, at, 0.05, 0.2);
  }

  hurt(at = this.now()) {  // you took a hit: a low crunch and a falling buzz
    if (!this._live) return;
    this._noise('lowpass', 700, at, 0.22, 0.5, { to: 150 });
    this._tone('sawtooth', 200, at, 0.28, 0.25, { to: 55 });
    this._tone('square', 110, at, 0.1, 0.15, { to: 70 });
  }

  /** Your body moves: a swish for a slip, a lower one for a duck, a glove tap for a block. */
  move(state, at = this.now()) {
    if (!this._live || !this._gate('move ' + state, 0.25, at)) return;
    if (state === 'SLIP LEFT' || state === 'SLIP RIGHT') this._noise('bandpass', 2600, at, 0.15, 0.14, { to: 900, q: 2 });
    else if (state === 'DUCK') this._noise('bandpass', 1000, at, 0.18, 0.16, { to: 300, q: 1.5 });
    else if (state === 'BLOCK') {
      this._noise('bandpass', 1600, at, 0.05, 0.22);
      this._tone('sine', 190, at, 0.06, 0.25, { to: 110 });
    }
  }

  // --- the round ---

  /** "READY": three rising notes. */
  ready(at = this.now()) {
    if (!this._live) return;
    [[72, 0, 0.1], [76, 0.11, 0.1], [79, 0.22, 0.32]].forEach(([note, dt, dur]) => {
      this._tone('square', hz(note), at + dt, dur, 0.12);
      this._tone('triangle', hz(note - 12), at + dt, dur, 0.18);
    });
  }

  /** "FIGHT!": a big chord, a cymbal, a kick and the bell. */
  fight(at = this.now()) {
    if (!this._live) return;
    [72, 76, 79, 84].forEach((note) => this._tone('square', hz(note), at, 0.6, 0.05));
    this._noise('highpass', 5000, at, 0.5, 0.15);
    this._kick(at, 0.5, this.sfx);
    this._tone('triangle', hz(81), at, 1.0, 0.12);
    this._tone('sine', hz(93), at, 0.6, 0.05);
  }

  roundBell(times = 1, at = this.now()) {
    if (!this._live) return;
    for (let i = 0; i < times; i++) {
      this._tone('triangle', 1175, at + i * 0.5, 0.9, 0.25);
      this._tone('sine', 2350, at + i * 0.5, 0.5, 0.1);
    }
  }

  /** The last seconds: a tick each second, higher for the last three. */
  tick(secondsLeft, at = this.now()) {
    if (!this._live) return;
    const last = secondsLeft <= 3;
    this._tone('square', last ? 1760 : 1175, at, 0.05, last ? 0.12 : 0.07);
  }

  /** How the round ended. A knockout gets a big hit and a crowd roar before the jingle. */
  result(winner, reason, at = this.now()) {
    if (!this._live) return;
    let t = at;
    if (reason === 'KO') {
      this._impact('UPPERCUT', false, t);
      this._tone('sawtooth', 800, t, 0.8, 0.12, { to: 60 });
      this._crowd(1.2, t);
      t += 0.7;
    } else {
      this.roundBell(3, t);
      t += 1.4;
    }
    if (winner === 'you') {  // a rising fanfare
      [72, 76, 79, 84].forEach((note, i) => this._tone('square', hz(note), t + i * 0.1, 0.14, 0.12));
      [72, 76, 79, 84].forEach((note) => this._tone('square', hz(note), t + 0.45, 0.9, 0.07));
      this._crowd(1.4, t + 0.3);
    } else if (winner === 'bot') {  // "wah wah wah wahhh"
      [67, 66, 65].forEach((note, i) => this._tone('sawtooth', hz(note), t + i * 0.36, 0.33, 0.13, { to: hz(note) * 0.94 }));
      this._tone('sawtooth', hz(64), t + 1.08, 0.9, 0.13, { to: hz(64) * 0.85 });
    } else {
      this._tone('square', hz(72), t, 0.2, 0.11);
      this._tone('square', hz(67), t + 0.22, 0.35, 0.11);
    }
  }

  _crowd(dur, t) {  // a swell of noise
    this._noise('bandpass', 900, t, dur, 0.28, { q: 0.6, attack: dur * 0.3 });
  }

  // --- menus and set up ---

  countdown(n, at = this.now()) {  // the calibration countdown rises as it runs out
    if (!this._live) return;
    this._tone('square', hz(64 + Math.max(0, 5 - n) * 2), at, 0.1, 0.1);
  }

  calibrated(at = this.now()) {
    if (!this._live) return;
    this._tone('square', hz(79), at, 0.08, 0.12);
    this._tone('square', hz(84), at + 0.09, 0.2, 0.12);
    this._tone('triangle', hz(91), at + 0.09, 0.25, 0.08);
  }

  cameraOn(at = this.now()) {  // a power-up sweep
    if (!this._live) return;
    this._tone('square', 300, at, 0.18, 0.09, { to: 1200 });
    this._tone('square', 1500, at + 0.18, 0.06, 0.07);
  }

  // --- the music: a shuffle ---

  /** The game says whether a fight is on. The beat plays only then, and only if the player wants music. */
  wantMusic(on) {
    this._musicWanted = Boolean(on);
    this._syncMusic();
  }

  _shouldPlay() {
    const visible = typeof document === 'undefined' || !document.hidden;  // timers crawl in a hidden tab, so stop instead of stuttering
    return Boolean(this.ctx) && this._musicWanted && this._musicEnabled && !this._muted && visible;
  }

  _syncMusic() {
    if (!this.ctx) return;
    const play = this._shouldPlay(), now = this.ctx.currentTime;
    if (play && !this._timer) {
      this.step = 0;
      this.nextTime = now + 0.05;
      this.music.gain.cancelScheduledValues(now);
      this.music.gain.setValueAtTime(MUSIC_LEVEL, now);
      this._schedule();
      this._timer = setInterval(() => this._schedule(), 25);
    } else if (!play && this._timer) {
      clearInterval(this._timer);
      this._timer = null;
      this.music.gain.cancelScheduledValues(now);
      this.music.gain.setTargetAtTime(0, now, 0.12);  // fade, so notes already queued do not cut off
    }
  }

  /** Queue every step that falls in the next 150 ms. The timer wakes every 25 ms, so timing stays exact. */
  _schedule() {
    const c = this.ctx;
    if (this.nextTime < c.currentTime) this.nextTime = c.currentTime + 0.05;  // after a stall, carry on instead of rushing to catch up
    while (this.nextTime < c.currentTime + 0.15) {
      this._playStep(this.step, this.nextTime);
      this.nextTime += 60 / (this.hurry ? HURRY_BPM : BPM) / 3;
      this.step = (this.step + 1) % STEPS;
    }
  }

  /** Everything that happens on one step of the 4-bar loop. */
  _playStep(step, t) {
    const bar = Math.floor(step / 12), s = step % 12, m = this.music;
    if (s === 0 || s === 6 || (s === 8 && bar % 2 === 1)) this._kick(t);
    if (s === 3 || s === 9) this._snare(t);
    if (HAT_STEPS.has(s)) this._hat(t, s % 6 === 0, bar === 3 && s === 11);
    const note = BASS[bar][s];
    if (note !== undefined) this._tone('triangle', hz(note), t, 0.16, 0.55, { bus: m, attack: 0.003 });
    if (s === 9 && STAB[bar]) STAB[bar].forEach((n) => this._tone('square', hz(n), t, 0.09, 0.06, { bus: m }));
  }

  _kick(t, vol = 0.85, bus = this.music) {
    this._tone('sine', 150, t, 0.17, vol, { to: 42, attack: 0.002, bus });
  }

  _snare(t) {
    this._noise('bandpass', 1900, t, 0.13, 0.35, { q: 0.8, bus: this.music });
    this._tone('triangle', 185, t, 0.09, 0.25, { to: 120, bus: this.music });
  }

  _hat(t, accent, open) {
    this._noise('highpass', 7500, t, open ? 0.12 : accent ? 0.05 : 0.032, accent ? 0.16 : 0.1, { bus: this.music });
  }

  /** For checking the beat offline: queue `count` steps starting at time `start`. */
  renderSteps(start, count, bpm = BPM) {
    for (let i = 0; i < count; i++) this._playStep(i % STEPS, start + (i * 60) / bpm / 3);
  }
}

export const sound = new SoundEngine();
