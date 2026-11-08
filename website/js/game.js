// The sparring game on the stage. It is played with the same events the real game gets through
// the key bridge: punches (JAB, CROSS, HOOK, UPPERCUT) and body states (BLOCK, DUCK, SLIP LEFT/RIGHT).
// The bot telegraphs each attack. Dodge it with the move on screen, or block it to take a quarter.

import { sound } from './audio.js';

const W = 1280, H = 720;           // the game is drawn on a 16:9 area and scaled to fit the stage
const ROUND_SECONDS = 90;
const HP = 100;
const DAMAGE = { JAB: 4, CROSS: 7, HOOK: 9, UPPERCUT: 11 };
const COOLDOWN = { JAB: 0.14, CROSS: 0.2, HOOK: 0.24, UPPERCUT: 0.3 };
const BOT_ATTACKS = {
  STRAIGHT: { damage: 7, label: 'SLIP!', dodge: ['SLIP LEFT', 'SLIP RIGHT'] },
  HOOK: { damage: 10, label: 'DUCK!', dodge: ['DUCK'] },
};
const GREEN = '#3dff7a', RED = '#ff3355', AMBER = '#ffb020';

const rand = (a, b) => a + Math.random() * (b - a);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const lerp = (a, b, k) => a + (b - a) * k;

// --- the game ---

export class Game {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.states = new Set();
    this.view = { x: 0, y: 0 };      // the camera moves when you slip or duck
    this.floaters = [];
    this.shake = 0;
    this.hurt = 0;
    this.time = 0;
    this.record = { wins: 0, losses: 0 };
    this.phase = 'ready';           // ready (title), intro (READY...), fight, over
    this.paused = false;
    this.introAt = this.fightAt = -9;
    this.reset();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this.last = performance.now();
    requestAnimationFrame((t) => this._frame(t));
  }

  reset() {
    this.player = { hp: HP, cool: 0, glove: { L: null, R: null } };  // glove: { kind, t } while punching
    this.bot = { hp: HP, state: 'idle', t: 0, dur: 1, attack: 'STRAIGHT', hand: 'R', flash: 0 };
    this.combo = { count: 0, last: -9 };
    this.clock = ROUND_SECONDS;
    this.floaters = [];
    this.result = null;
  }

  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(2, Math.round(r.width * dpr));
    this.canvas.height = Math.max(2, Math.round(r.height * dpr));
  }

  setStates(list) {
    const next = new Set(list);
    if (this.phase === 'fight') for (const s of next) if (!this.states.has(s)) sound.move(s);  // a swish when you slip, duck or block
    this.states = next;
  }

  /** Freeze the game while its tab is not showing, so the fight and its music do not carry on behind another tab. */
  setPaused(paused) {
    this.paused = paused;
    this.last = performance.now();
    sound.wantMusic(!paused && this.phase === 'fight');
  }

  /** Start or restart a fight (from ready or over): "READY", then "FIGHT!" a moment later. */
  start() {
    if (this.phase === 'fight' || this.phase === 'intro') return;
    if (this.phase === 'over' && this.time - this.overAt < 1.2) return;  // let the result be read
    this.reset();
    this.phase = 'intro';
    this.introAt = this.time;
    this._setBot('idle', 99);
    sound.ready();
  }

  punch(kind, side) {
    if (this.phase === 'intro') return;  // not until the bell
    if (this.phase !== 'fight') { this.start(); return; }
    const p = this.player;
    if (p.cool > 0) return;
    p.cool = COOLDOWN[kind] || 0.2;
    p.glove[side] = { kind, t: 0 };
    const b = this.bot;
    let dmg = DAMAGE[kind] || 5, guarded = b.state === 'guard';
    if (guarded) dmg *= 0.2;
    else {
      if (this.time - this.combo.last < 1.2) this.combo.count += 1; else this.combo.count = 1;
      this.combo.last = this.time;
      dmg *= 1 + Math.min(0.5, 0.1 * (this.combo.count - 1));
      if (b.state === 'recover' || b.state === 'stunned') dmg *= 1.5;  // you hit it while it was open
    }
    dmg = Math.round(dmg);
    b.hp = Math.max(0, b.hp - dmg);
    b.flash = 0.15;
    this._float(640 + rand(-60, 60), 190, guarded ? 'BLOCKED' : `-${dmg}`, guarded ? '#8aa0c8' : GREEN);
    sound.punch(kind, guarded);
    if (!guarded) sound.combo(this.combo.count);
    this.shake = Math.max(this.shake, guarded ? 2 : 6 + dmg / 2);
    if (!guarded && (b.state === 'windup' || b.state === 'idle')) this._setBot('stunned', b.state === 'windup' ? 0.6 : 0.3);
  }

  // --- bot ---

  _setBot(state, dur = 1) { Object.assign(this.bot, { state, t: 0, dur }); }

  _botThink() {
    const b = this.bot;
    if (Math.random() < 0.3) { this._setBot('guard', rand(1.0, 1.6)); return; }
    b.attack = Math.random() < 0.5 ? 'STRAIGHT' : 'HOOK';
    b.hand = Math.random() < 0.5 ? 'L' : 'R';
    this._setBot('windup', Math.max(0.6, 1.0 - (ROUND_SECONDS - this.clock) / 220));  // gets quicker as the round goes on
    sound.warn(b.attack);
  }

  _botStrike() {
    const b = this.bot, a = BOT_ATTACKS[b.attack], p = this.player;
    let dmg = a.damage;
    if (a.dodge.some((s) => this.states.has(s))) {
      dmg = 0;
      this._float(640, 520, 'DODGED!', GREEN);
      sound.dodge();
    } else if (this.states.has('BLOCK')) {
      dmg = Math.max(1, Math.round(dmg * 0.25));
      this._float(640, 520, 'BLOCKED', '#8aa0c8');
      sound.block();
    } else {
      this._float(640, 520, `-${dmg}`, RED);
      sound.hurt();
    }
    if (dmg > 0) { p.hp = Math.max(0, p.hp - dmg); this.hurt = 0.35; this.shake = Math.max(this.shake, 4 + dmg); }
    this._setBot('strike', 0.18);
  }

  _updateBot(dt) {
    const b = this.bot;
    b.t += dt;
    b.flash = Math.max(0, b.flash - dt);
    if (this.phase !== 'fight') { if (b.state !== 'idle') this._setBot('idle', 99); return; }
    if (b.state === 'idle' && b.t >= b.dur) this._botThink();
    else if (b.state === 'guard' && b.t >= b.dur) this._setBot('idle', rand(0.5, 1.0));
    else if (b.state === 'windup' && b.t >= b.dur) this._botStrike();
    else if (b.state === 'strike' && b.t >= b.dur) this._setBot('recover', 0.8);
    else if ((b.state === 'recover' || b.state === 'stunned') && b.t >= b.dur) this._setBot('idle', rand(0.5, 1.0));
  }

  _float(x, y, text, color) { this.floaters.push({ x, y, text, color, t: 0 }); }

  _finish(winner, reason) {
    this.phase = 'over';
    this.overAt = this.time;
    this.result = { winner, reason };
    if (winner === 'you') this.record.wins += 1; else if (winner === 'bot') this.record.losses += 1;
    sound.wantMusic(false);
    sound.hurry = false;
    sound.result(winner, reason);
  }

  // --- time ---

  _frame(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    if (!this.paused) {
      this.update(dt);
      this.draw();
    }
    requestAnimationFrame((t) => this._frame(t));
  }

  update(dt) {
    this.time += dt;
    const p = this.player;
    p.cool = Math.max(0, p.cool - dt);
    for (const side of ['L', 'R']) {
      const g = p.glove[side];
      if (g) { g.t += dt; if (g.t > 0.24) p.glove[side] = null; }
    }
    if (this.phase === 'intro' && this.time - this.introAt >= 1.1) {  // "READY" is over: "FIGHT!"
      this.phase = 'fight';
      this.fightAt = this.time;
      this._setBot('idle', 1.0);
      sound.fight();
      sound.wantMusic(true);
    }
    this._updateBot(dt);
    if (this.phase === 'fight') {
      const before = Math.ceil(this.clock);
      this.clock = Math.max(0, this.clock - dt);
      const after = Math.ceil(this.clock);
      sound.hurry = this.clock <= 15;  // the beat speeds up for the last 15 seconds
      if (after < before && after >= 1 && after <= 10) sound.tick(after);
      if (this.bot.hp <= 0) this._finish('you', 'KO');
      else if (p.hp <= 0) this._finish('bot', 'KO');
      else if (this.clock <= 0) {
        const diff = p.hp - this.bot.hp;
        this._finish(diff > 0 ? 'you' : diff < 0 ? 'bot' : 'draw', 'TIME');
      }
    }
    // the camera follows your head: down when you duck, sideways when you slip
    const tx = this.states.has('SLIP LEFT') ? 110 : this.states.has('SLIP RIGHT') ? -110 : 0;
    const ty = this.states.has('DUCK') ? -90 : 0;
    this.view.x = lerp(this.view.x, tx, clamp(dt * 12, 0, 1));
    this.view.y = lerp(this.view.y, ty, clamp(dt * 12, 0, 1));
    this.shake = Math.max(0, this.shake - dt * 40);
    this.hurt = Math.max(0, this.hurt - dt);
    for (const f of this.floaters) f.t += dt;
    this.floaters = this.floaters.filter((f) => f.t < 0.9);
  }

  // --- drawing ---

  draw() {
    const c = this.canvas, ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#050609';
    ctx.fillRect(0, 0, c.width, c.height);
    const s = Math.min(c.width / W, c.height / H);
    ctx.setTransform(s, 0, 0, s, (c.width - W * s) / 2, (c.height - H * s) / 2);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, W, H);
    ctx.clip();

    ctx.save();
    ctx.translate(this.view.x + rand(-1, 1) * this.shake, this.view.y + rand(-1, 1) * this.shake);
    this._drawRing();
    this._drawBot();
    for (const f of this.floaters) this._drawFloater(f);
    ctx.restore();

    this._drawGloves();
    if (this.hurt > 0) {  // red vignette when you are hit
      const g = ctx.createRadialGradient(W / 2, H / 2, H * 0.3, W / 2, H / 2, H * 0.85);
      g.addColorStop(0, 'rgba(255,51,85,0)');
      g.addColorStop(1, `rgba(255,51,85,${(this.hurt / 0.35) * 0.55})`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }
    this._drawHud();
    this._drawBanner();
    ctx.restore();
  }

  _drawRing() {
    const ctx = this.ctx;
    const wall = ctx.createLinearGradient(0, 0, 0, 450);
    wall.addColorStop(0, '#0d0f18');
    wall.addColorStop(1, '#171a2b');
    ctx.fillStyle = wall;
    ctx.fillRect(-200, -200, W + 400, 700);
    ctx.fillStyle = '#10131d';  // the crowd
    for (let i = -8; i < 64; i++) {
      ctx.beginPath();
      ctx.arc(i * 22 + (i % 2) * 9, 235 + Math.sin(i * 12.9) * 12, 15, 0, Math.PI * 2);
      ctx.fill();
    }
    const spot = ctx.createRadialGradient(W / 2, 40, 10, W / 2, 300, 520);
    spot.addColorStop(0, 'rgba(255,255,255,0.22)');
    spot.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = spot;
    ctx.fillRect(-200, -200, W + 400, 800);

    const floor = ctx.createLinearGradient(0, 430, 0, H + 120);
    floor.addColorStop(0, '#262b44');
    floor.addColorStop(1, '#15182a');
    ctx.fillStyle = floor;
    ctx.beginPath();
    ctx.moveTo(-260, H + 140); ctx.lineTo(W + 260, H + 140); ctx.lineTo(1110, 430); ctx.lineTo(170, 430);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 2;
    for (let i = 1; i < 6; i++) {
      const y = 430 + (i * i) * 11;
      ctx.beginPath(); ctx.moveTo(170 - i * 60, y); ctx.lineTo(1110 + i * 60, y); ctx.stroke();
    }
    ctx.fillStyle = '#2a2d3d';  // corner posts
    ctx.fillRect(160, 240, 20, 200);
    ctx.fillRect(1100, 240, 20, 200);
    ctx.lineCap = 'round';
    ctx.lineWidth = 7;
    ['#e0243b', '#f4f4f6', '#e0243b'].forEach((color, i) => {
      ctx.strokeStyle = color;
      ctx.beginPath(); ctx.moveTo(170, 290 + i * 55); ctx.lineTo(1110, 290 + i * 55); ctx.stroke();
    });
  }

  _glove(x, y, r, color = '#c01c32') {
    const ctx = this.ctx;
    const g = ctx.createRadialGradient(x - r * 0.3, y - r * 0.35, r * 0.1, x, y, r);
    g.addColorStop(0, '#ff7a8c');
    g.addColorStop(0.35, color);
    g.addColorStop(1, '#6b0f1d');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#f2ece4';  // the cuff
    ctx.fillRect(x - r * 0.55, y + r * 0.75, r * 1.1, r * 0.4);
  }

  _drawBot() {
    const ctx = this.ctx, b = this.bot;
    const bob = Math.sin(this.time * 3) * 6;
    const stunned = b.state === 'stunned';
    const lean = stunned ? Math.sin(this.time * 40) * 6 : 0;
    const skin = b.flash > 0 ? '#ffffff' : '#d9a27e';
    const cx = 640 + lean, hy = 255 + bob + (stunned ? 14 : 0);

    ctx.fillStyle = skin;  // legs, torso, neck, head
    ctx.fillRect(cx - 55, 575, 38, 90);
    ctx.fillRect(cx + 17, 575, 38, 90);
    ctx.fillStyle = '#1b1d2c';
    ctx.fillRect(cx - 62, 655, 50, 24);
    ctx.fillRect(cx + 12, 655, 50, 24);
    ctx.fillStyle = skin;
    ctx.beginPath(); ctx.roundRect(cx - 90, 322 + bob, 180, 230, 38); ctx.fill();
    ctx.fillRect(cx - 18, hy + 40, 36, 50);
    ctx.fillStyle = '#e0243b';  // shorts
    ctx.beginPath(); ctx.roundRect(cx - 94, 520 + bob, 188, 78, 12); ctx.fill();
    ctx.fillStyle = '#f4f4f6';
    ctx.fillRect(cx - 94, 520 + bob, 188, 14);
    ctx.fillStyle = skin;
    ctx.beginPath(); ctx.arc(cx, hy, 58, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#2a1d18';  // hair, eyes and mouth
    ctx.beginPath(); ctx.arc(cx, hy - 14, 58, Math.PI * 1.02, Math.PI * 1.98); ctx.fill();
    const angry = b.state === 'windup';
    ctx.fillStyle = '#111';
    ctx.fillRect(cx - 30, hy - 2, 18, angry ? 6 : 9);
    ctx.fillRect(cx + 12, hy - 2, 18, angry ? 6 : 9);
    ctx.strokeStyle = '#111'; ctx.lineWidth = 5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(cx - 20, hy + 30); ctx.lineTo(cx + 20, hy + 30 + (stunned ? 8 : 0)); ctx.stroke();

    // gloves: where they are depends on what the bot is doing
    const sh = { L: [cx - 80, 350 + bob], R: [cx + 80, 350 + bob] };
    const rest = { L: [cx - 105, 410 + bob, 46], R: [cx + 105, 410 + bob, 46] };
    const pose = { L: rest.L.slice(), R: rest.R.slice() };
    if (b.state === 'guard') { pose.L = [cx - 50, hy + 25, 50]; pose.R = [cx + 50, hy + 25, 50]; }
    else if (b.state === 'recover' || stunned) { pose.L = [cx - 120, 500, 46]; pose.R = [cx + 120, 500, 46]; }
    else if (b.state === 'windup' || b.state === 'strike') {
      const k = b.state === 'windup' ? clamp(b.t / b.dur, 0, 1) : 1;
      const mine = b.hand, out = mine === 'L' ? -1 : 1;
      if (b.attack === 'STRAIGHT') {
        pose[mine] = b.state === 'windup' ? [cx + out * (110 + 40 * k), 430 + 30 * k, 46 + 4 * k] : [cx + out * 20, 330, 95];
      } else {
        pose[mine] = b.state === 'windup' ? [cx + out * (150 + 80 * k), 360 + 10 * k, 46 + 6 * k] : [cx - out * 60, 300, 90];
      }
    }
    ctx.strokeStyle = skin; ctx.lineWidth = 34;
    for (const side of ['L', 'R']) {
      ctx.beginPath(); ctx.moveTo(...sh[side]); ctx.lineTo(pose[side][0], pose[side][1]); ctx.stroke();
    }
    for (const side of ['L', 'R']) this._glove(pose[side][0], pose[side][1], pose[side][2]);

    if (b.state === 'windup') {  // tell the player what to do
      const a = BOT_ATTACKS[b.attack], k = clamp(b.t / b.dur, 0, 1);
      ctx.textAlign = 'center';
      ctx.font = '900 64px system-ui, sans-serif';
      ctx.lineWidth = 10; ctx.strokeStyle = '#000'; ctx.strokeText(a.label, 640, 150);
      ctx.fillStyle = k > 0.65 ? RED : AMBER;
      ctx.fillText(a.label, 640, 150);
      ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(540, 168, 200, 10);
      ctx.fillStyle = k > 0.65 ? RED : AMBER; ctx.fillRect(540, 168, 200 * k, 10);
    }
  }

  _drawGloves() {
    const ctx = this.ctx, blocking = this.states.has('BLOCK');
    const bases = { L: [400, 660], R: [880, 660] };
    for (const side of ['L', 'R']) {
      let [x, y] = bases[side], r = 96;
      const out = side === 'L' ? -1 : 1;
      y += Math.sin(this.time * 2.4 + (side === 'L' ? 0 : 1.5)) * 5;
      if (blocking) { x = 640 + out * 130; y = 470; r = 100; }
      const g = this.player.glove[side];
      if (g) {
        const k = Math.sin(clamp(g.t / 0.24, 0, 1) * Math.PI);  // out and back
        if (g.kind === 'JAB' || g.kind === 'CROSS') { x = lerp(x, 640 + out * 20, k); y = lerp(y, 350, k); r = lerp(r, 62, k); }
        else if (g.kind === 'HOOK') { x = lerp(x, 640 - out * 70, k); y = lerp(y, 320 - 40 * k, k); r = lerp(r, 68, k); }
        else { x = lerp(x, 640 + out * 60, k); y = lerp(y, 300, k); r = lerp(r, 70, k); }  // uppercut
      }
      this._glove(x, y, r, '#2563eb');
    }
  }

  _drawFloater(f) {
    const ctx = this.ctx, k = f.t / 0.9;
    ctx.save();
    ctx.globalAlpha = 1 - k * k;
    ctx.textAlign = 'center';
    ctx.font = '900 54px system-ui, sans-serif';
    ctx.lineWidth = 8; ctx.strokeStyle = '#000';
    ctx.strokeText(f.text, f.x, f.y - 70 * k);
    ctx.fillStyle = f.color;
    ctx.fillText(f.text, f.x, f.y - 70 * k);
    ctx.restore();
  }

  _bar(x, y, w, h, frac, rightToLeft, label) {
    const ctx = this.ctx;
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    ctx.fillRect(x - 4, y - 4, w + 8, h + 8);
    ctx.fillStyle = frac > 0.5 ? GREEN : frac > 0.25 ? AMBER : RED;
    const fw = w * clamp(frac, 0, 1);
    ctx.fillRect(rightToLeft ? x + w - fw : x, y, fw, h);
    ctx.fillStyle = '#fff';
    ctx.font = '800 20px system-ui, sans-serif';
    ctx.textAlign = rightToLeft ? 'right' : 'left';
    ctx.fillText(label, rightToLeft ? x + w : x, y - 12);
  }

  _drawHud() {
    const ctx = this.ctx;
    this._bar(40, 50, 480, 26, this.player.hp / HP, false, 'YOU');
    this._bar(760, 50, 480, 26, this.bot.hp / HP, true, 'SPARRING BOT');
    ctx.textAlign = 'center';
    ctx.font = '900 44px system-ui, sans-serif';
    ctx.lineWidth = 8; ctx.strokeStyle = '#000';
    const secs = Math.ceil(this.clock);
    const text = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
    ctx.strokeText(text, 640, 78);
    ctx.fillStyle = '#fff';
    ctx.fillText(text, 640, 78);
    if (this.phase === 'fight' && this.combo.count > 1 && this.time - this.combo.last < 1.2) {
      ctx.font = '900 30px system-ui, sans-serif';
      ctx.fillStyle = AMBER;
      ctx.fillText(`${this.combo.count} HIT COMBO`, 640, 118);
    }
    ctx.textAlign = 'left';
    ctx.font = '700 18px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.fillText(`Wins ${this.record.wins}   Losses ${this.record.losses}`, 40, 112);
  }

  _banner(title, sub, color) {
    const ctx = this.ctx;
    ctx.fillStyle = 'rgba(5,6,9,0.62)';
    ctx.fillRect(0, 210, W, 250);
    ctx.textAlign = 'center';
    ctx.font = '900 96px system-ui, sans-serif';
    ctx.lineWidth = 12; ctx.strokeStyle = '#000';
    ctx.strokeText(title, 640, 340);
    ctx.fillStyle = color;
    ctx.fillText(title, 640, 340);
    ctx.font = '700 30px system-ui, sans-serif';
    ctx.fillStyle = '#fff';
    ctx.fillText(sub, 640, 400);
  }

  _drawBanner() {
    if (this.phase === 'ready') {
      this._banner('KEYBOARD WARRIOR', 'Throw a punch to start. Press Space if you have no camera.', RED);
    } else if (this.phase === 'intro') {
      this._banner('READY', 'ROUND 1', AMBER);
    } else if (this.phase === 'fight' && this.time - this.fightAt < 0.8) {
      this._banner('FIGHT!', ' ', RED);
    } else if (this.phase === 'over') {
      const { winner, reason } = this.result;
      const title = winner === 'you' ? (reason === 'KO' ? 'KO! YOU WIN' : 'YOU WIN ON POINTS')
        : winner === 'bot' ? (reason === 'KO' ? 'KNOCKED OUT' : 'YOU LOSE ON POINTS') : 'DRAW';
      this._banner(title, this.time - this.overAt < 1.2 ? ' ' : 'Throw a punch to fight again', winner === 'you' ? GREEN : winner === 'bot' ? RED : AMBER);
    }
  }
}
