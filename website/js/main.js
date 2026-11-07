// Keyboard Warrior: wires the camera, the pose model, the punch detector, the game and the key bridge together.

import { Engine, AngleGuide, makeSettings } from './detect.js';
import { MODES, loadSettings } from './modes.js';
import { Game, sound } from './game.js';
import { Bridge, GloveConfirm } from './bridge.js';
import { loadPose, listCameras, openCamera, closeCamera, PoseLoop, drawCamera, framingHint } from './pose.js';

const $ = (id) => document.getElementById(id);
const el = Object.fromEntries([
  'game', 'gameWrap', 'bbPane', 'stage', 'tabSpar', 'tabBB', 'chipCam', 'chipBody', 'chipCal', 'chipBridge', 'muteBtn',
  'cam', 'video', 'camFrame', 'camPlaceholder', 'startBtn', 'stopBtn', 'calBtn', 'countdown', 'countdownNumber', 'hint', 'gauge', 'gaugeZone',
  'gaugeDot', 'steps', 'modeList', 'states', 'meterL', 'meterR', 'counts', 'lastPunch', 'bridgeText', 'outputSwitch',
  'cameraSelect', 'leadSelect', 'engineInfo', 'settingsSource', 'settingsDump', 'bridgeCmd', 'bridgeLine', 'copyCmd', 'openGameBtn',
].map((id) => [id, $(id)]));

const GAME_URL = 'https://browserboxing.com/fight';
const KEY_PUNCH = { j: ['JAB', 'L'], k: ['CROSS', 'R'], h: ['HOOK', 'L'], u: ['UPPERCUT', 'R'] };  // the real game's keys
const KEY_STATE = { w: 'BLOCK', s: 'DUCK', a: 'SLIP LEFT', d: 'SLIP RIGHT' };
const params = new URLSearchParams(location.search);
const clock = () => performance.now() / 1000;
const CALIBRATE_DELAY = 5;  // seconds between asking to calibrate and it happening, as in pose_tracker.py

const app = {
  mode: MODES.angled_camera,
  modeChosen: false,
  lead: 'L',
  settings: makeSettings(),
  engine: new Engine(),
  angle: new AngleGuide(),
  glove: new GloveConfirm(),
  stage: 'spar',                     // 'spar' (built-in game) or 'bb' (the real Browser Boxing)
  cameraOn: false,
  pose: null,                        // { landmarker, delegate, local }
  loop: null,
  outputOn: false,
  cameraStates: [],
  keyStates: new Set(),              // W S A D pressed on the keyboard, for playing without a camera
  flash: { L: ['', 0], R: ['', 0] },
  counts: { JAB: 0, CROSS: 0, HOOK: 0, UPPERCUT: 0 },
  speeds: [0, 0],
  body: false,
  positioned: false,
  fought: false,
  hint: { text: '', level: '' },
  notice: { text: '', level: '', until: 0 },   // a message that stays for a moment, such as "Calibrated"
  lastText: '',
  countdownTimer: null,
  frames: 0, fps: 0, fpsAt: clock(),
};

const game = new Game(el.game);
const camCtx = el.cam.getContext('2d');

// ---------------------------------------------------------------------------------------------
// key bridge
// ---------------------------------------------------------------------------------------------

const bridge = new Bridge({
  onConnection(status) {
    if (status) app.glove.onEvent({ gloves: status.gloves || [] });
    app.outputOn = Boolean(status?.output);
    renderBridge();
  },
  onHotkey(name) { if (name === 'calibrate') calibrateWithCountdown(); },
  onOutput(enabled) { app.outputOn = enabled; renderBridge(); },
  onGlove(event) { app.glove.onEvent(event); },
});

/** Keys are only ever sent for the real game, and only while the switch is on. */
const sendingKeys = () => bridge.connected && app.outputOn && app.stage === 'bb';

function renderBridge() {
  const s = bridge.status, on = bridge.connected;
  const wantsGlove = app.mode.glove;
  el.bridgeCmd.textContent = `python keybridge.py --live${wantsGlove ? ' --glove' : ''}`;
  el.outputSwitch.disabled = !on;
  el.outputSwitch.checked = on && app.outputOn;
  el.chipBridge.className = 'chip ' + (!on ? '' : s.live ? 'ok' : 'warn');
  el.chipBridge.textContent = !on ? 'Bridge off' : (s.live ? 'Bridge live' : 'Bridge dry run') + (app.outputOn ? ', keys ON' : '');
  let text = 'The key bridge is not running. You only need it for the real Browser Boxing.';
  let line = 'Bridge not found yet. Start it with the command above.';
  if (on) {
    text = `Bridge connected${s.live ? '' : ' in dry-run mode: keys are only printed, restart it with --live to press them'}. Keys are ${app.outputOn ? 'ON' : 'OFF'}.`;
    line = s.live ? 'Bridge connected. Keys will be pressed.' : 'Bridge connected in dry-run mode (keys are only printed). Restart it with --live.';
    if (wantsGlove && !s.glove_enabled) text += ' Glove mode needs the bridge started with --glove.';
  }
  el.bridgeText.textContent = text;
  el.bridgeLine.textContent = line;
  el.bridgeLine.classList.toggle('ok', on && s.live);
}

// ---------------------------------------------------------------------------------------------
// modes
// ---------------------------------------------------------------------------------------------

function renderModes() {
  el.modeList.replaceChildren();
  for (const mode of Object.values(MODES)) {
    const b = document.createElement('button');
    b.className = 'mode';
    b.disabled = !mode.available;
    b.setAttribute('aria-pressed', String(mode === app.mode));
    b.innerHTML = `<b><span></span><span class="badge"></span></b><span class="desc"></span><span class="more"></span>`;
    b.querySelector('b span').textContent = mode.title;
    b.querySelector('.badge').textContent = mode.badge;
    b.querySelector('.desc').textContent = mode.summary;
    b.querySelector('.more').textContent = `Needs: ${mode.needs} Detects: ${mode.detects}.`;
    b.addEventListener('click', () => { app.modeChosen = true; setMode(mode.id); });
    el.modeList.append(b);
  }
}

async function setMode(id) {
  const mode = MODES[id];
  if (!mode?.available) return;
  app.mode = mode;
  el.gauge.hidden = !mode.angleGuide;
  renderModes();
  await applySettings();
  renderBridge();
}

async function applySettings() {
  try {
    const { settings, source } = await loadSettings(app.mode, app.lead);
    app.settings = settings;
    app.engine.setSettings(settings);
    el.settingsSource.textContent = source;
    el.settingsDump.textContent = Object.entries(settings).map(([k, v]) => `${k}: ${v}`).join('\n');
  } catch (e) {
    setHint(`Could not load the settings for this mode: ${e.message}`, 'lost');
  }
}

// ---------------------------------------------------------------------------------------------
// camera and pose
// ---------------------------------------------------------------------------------------------

function setHint(text, level = '') {
  if (app.hint.text === text && app.hint.level === level) return;
  app.hint = { text, level };
  el.hint.textContent = text;
  el.hint.className = 'hint ' + level;
}

/** Show a message for a couple of seconds, over the usual per-frame guidance. */
function notice(text, level = '', seconds = 2.5) {
  app.notice = { text, level, until: clock() + seconds };
  setHint(text, level);
}

function cameraError(e) {
  const name = e?.name;
  if (name === 'NotAllowedError') return 'The camera is blocked. Allow it in the address bar, then press Start camera again.';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No camera was found. Plug one in or pick another under Advanced.';
  if (name === 'NotReadableError') return 'The camera is busy. Close any other app that is using it.';
  return `Could not start: ${e?.message || e}`;
}

async function startCamera() {
  el.startBtn.disabled = true;
  sound.unlock();
  const placeholder = (text) => { el.camPlaceholder.querySelector('p').textContent = text; };
  try {
    placeholder('Asking for the camera…');
    const stream = await openCamera(el.video, el.cameraSelect.value);
    const track = stream.getVideoTracks()[0].getSettings();
    const sized = el.video.videoWidth > 16;  // trust the video once it reports a real size
    el.cam.width = sized ? el.video.videoWidth : track.width || 640;
    el.cam.height = sized ? el.video.videoHeight : track.height || 480;
    el.camFrame.style.aspectRatio = `${el.cam.width} / ${el.cam.height}`;
    await fillCameraList();
    if (!app.pose) app.pose = await loadPose({ cpu: params.has('cpu'), onStatus: placeholder });
  } catch (e) {
    closeCamera(el.video);
    placeholder(cameraError(e));
    el.startBtn.disabled = false;
    el.startBtn.textContent = 'Try again';
    console.error(e);
    return;
  }
  app.cameraOn = true;
  app.loop = new PoseLoop(el.video, app.pose.landmarker, { onResult, onStall: pause });
  app.loop.start();
  el.camPlaceholder.hidden = true;
  el.stopBtn.hidden = false;
  el.calBtn.disabled = false;
  el.engineInfo.textContent = `Pose model runs on the ${app.pose.delegate}, loaded from ${app.pose.local ? 'this site' : 'the internet'}.`;
  setHint('Looking for you…');
}

function stopCamera() {
  app.loop?.stop();
  app.loop = null;
  closeCamera(el.video);
  app.cameraOn = false;
  app.engine.noBody();
  app.body = app.positioned = false;
  pause();
  camCtx.clearRect(0, 0, el.cam.width, el.cam.height);
  el.camPlaceholder.hidden = false;
  el.camPlaceholder.querySelector('p').textContent = 'Camera stopped.';
  el.startBtn.disabled = false;
  el.startBtn.textContent = 'Start camera';
  el.stopBtn.hidden = true;
  el.calBtn.disabled = true;
  setHint('Start the camera to begin.');
}

async function fillCameraList() {
  const current = el.cameraSelect.value;
  const cameras = await listCameras();
  el.cameraSelect.replaceChildren(new Option('Default camera', ''));
  cameras.forEach((c, i) => { if (c.deviceId) el.cameraSelect.append(new Option(c.label || `Camera ${i + 1}`, c.deviceId)); });
  el.cameraSelect.value = current;
}

/** Tracking stopped (window hidden, covered or camera stopped): let go of everything. */
function pause() {
  app.cameraStates = [];
  applyStates(true);
  if (app.cameraOn) setHint('Tracking paused. Keep this window visible, the camera needs it.', 'lost');
}

document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });

function onResult(now, result) {
  const lm = result.landmarks?.[0], world3d = result.worldLandmarks?.[0];
  let out;
  if (lm && world3d) {
    const vis = lm.map((p) => p.visibility ?? 0);
    const world = world3d.map((p) => [p.x, p.y, p.z]);
    out = app.engine.process(now, lm.map((p) => [1 - p.x, p.y]), world, vis);  // x mirrored, like the picture
    app.angle.onPose(world, vis, now);
  } else {
    out = app.engine.noBody();
  }
  app.body = out.body;
  app.speeds = out.speeds;

  for (const [side, kind] of out.punches) {
    if (app.mode.glove && !app.glove.confirms(side, now)) { app.lastText = `${side === 'L' ? 'Left' : 'Right'} ${kind.toLowerCase()} ignored: that glove felt no punch`; continue; }
    firePunch(kind, side, now);
  }
  app.cameraStates = out.states;
  applyStates();

  const view = describeView(now, out);
  if (now < app.notice.until) setHint(app.notice.text, app.notice.level);
  else setHint(view.hint, view.level);
  drawCamera(camCtx, el.video, {
    body: out.body, img: out.img, vis: out.vis, flash: app.flash, now,
    level: view.border, showGuide: view.showGuide,
  });

  app.frames += 1;
  if (now - app.fpsAt >= 1) { app.fps = Math.round(app.frames / (now - app.fpsAt)); app.frames = 0; app.fpsAt = now; }
}

/** What to tell the boxer, and the colour of the border: are they in a good place? */
function describeView(now, out) {
  app.positioned = false;
  if (app.countdownTimer) return { hint: 'Hold your guard: hands up by your face…', level: 'off', border: 'off', showGuide: false };
  if (!out.body) return { hint: 'No body detected. Step into the frame.', level: 'lost', border: 'lost', showGuide: true };
  const framing = framingHint(out.img, out.vis);
  if (framing) return { hint: framing, level: 'off', border: 'off', showGuide: true };
  if (app.mode.angleGuide) {
    const c = app.angle.coach(now);
    if (c.level !== 'good') {
      const text = c.angle === null ? c.text : `Body angle ${c.angle.toFixed(0)}°. ${c.text} (aim for 45°)`;
      return { hint: text, level: 'off', border: 'off', showGuide: false };
    }
  }
  app.positioned = true;
  if (!app.engine.calibrated) return { hint: 'In position. Press Calibrate, then put your hands up in guard.', level: 'off', border: 'good', showGuide: false };
  return { hint: 'Ready. Throw a punch.', level: 'good', border: 'good', showGuide: false };
}

// ---------------------------------------------------------------------------------------------
// punches, states and what they do
// ---------------------------------------------------------------------------------------------

function firePunch(kind, side, now) {
  app.counts[kind] += 1;
  app.flash[side] = [kind, now];
  app.fought = true;
  app.lastText = `${side === 'L' ? 'Left' : 'Right'} ${kind.toLowerCase()}`;
  if (app.stage === 'spar') game.punch(kind, side);
  else if (sendingKeys()) bridge.punch(kind);
}

/** Camera states (and W S A D when playing without a camera) go to the game, or to the bridge as held keys. */
function applyStates(force = false) {
  if (app.stage === 'spar') game.setStates([...app.cameraStates, ...app.keyStates]);
  else game.setStates([]);
  if (sendingKeys() || force) bridge.states(sendingKeys() ? app.cameraStates : [], undefined, force);
}

function calibrateNow() {
  if (!app.cameraOn) { notice('Start the camera first.', 'off'); return false; }
  if (app.engine.calibrate()) {
    sound.unlock();
    sound.play('beep');
    notice('Calibrated. Duck and slip are measured from this position.', 'good');
    return true;
  }
  notice('Calibration skipped: no body in view.', 'lost');
  return false;
}

/** Like pose_tracker: a request waits a few seconds so you can step back and get in guard; asking again restarts it. */
function calibrateWithCountdown(seconds = CALIBRATE_DELAY) {
  if (!app.cameraOn) { notice('Start the camera first.', 'off'); return; }
  clearInterval(app.countdownTimer);
  let n = seconds;
  const show = () => { el.countdown.hidden = false; el.countdownNumber.textContent = String(n); sound.unlock(); sound.play('beep'); };
  show();
  app.countdownTimer = setInterval(() => {
    n -= 1;
    if (n > 0) { show(); return; }
    clearInterval(app.countdownTimer);
    app.countdownTimer = null;
    el.countdown.hidden = true;
    calibrateNow();
  }, 1000);
}

// ---------------------------------------------------------------------------------------------
// tabs, the real game, keyboard
// ---------------------------------------------------------------------------------------------

function setStage(name) {
  app.stage = name;
  el.tabSpar.setAttribute('aria-selected', String(name === 'spar'));
  el.tabBB.setAttribute('aria-selected', String(name === 'bb'));
  el.gameWrap.hidden = name !== 'spar';
  el.bbPane.hidden = name !== 'bb';
  bridge.states([], undefined, true);  // never leave a key held across a switch
  applyStates();
}

function openGameWindow() {
  const r = el.stage.getBoundingClientRect();
  const sideBorder = (window.outerWidth - window.innerWidth) / 2;
  const toolbar = window.outerHeight - window.innerHeight - sideBorder;
  const features = [
    'popup=yes', `left=${Math.round(window.screenX + sideBorder + r.left)}`, `top=${Math.round(window.screenY + toolbar + r.top)}`,
    `width=${Math.round(r.width)}`, `height=${Math.round(r.height)}`,
  ].join(',');
  const win = window.open(GAME_URL, 'kw-browserboxing', features);
  el.bridgeLine.textContent = win ? el.bridgeLine.textContent : 'The game window was blocked. Allow pop-ups for this page and try again.';
}

function toggleMute() {
  sound.muted = !sound.muted;
  el.muteBtn.textContent = sound.muted ? 'Sound off' : 'Sound on';
  el.muteBtn.setAttribute('aria-pressed', String(sound.muted));
  try { localStorage.setItem('kw-muted', sound.muted ? '1' : '0'); } catch { /* storage may be off */ }
}

window.addEventListener('keydown', (e) => {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  const k = e.key.toLowerCase();
  if (k === 'c') { calibrateWithCountdown(); return; }
  if (k === 'm') { toggleMute(); return; }
  if (app.stage !== 'spar') return;  // in the real-game tab these keys would only echo what the bridge presses
  sound.unlock();
  if ((k === ' ' || k === 'enter') && document.activeElement === document.body) { game.start(); e.preventDefault(); }
  else if (k in KEY_PUNCH) game.punch(...KEY_PUNCH[k]);
  else if (k in KEY_STATE) { app.keyStates.add(KEY_STATE[k]); applyStates(); }
});
window.addEventListener('keyup', (e) => {
  const state = KEY_STATE[e.key.toLowerCase()];
  if (state && app.keyStates.delete(state)) applyStates();
});
window.addEventListener('blur', () => { if (app.keyStates.size) { app.keyStates.clear(); applyStates(); } });
window.addEventListener('pagehide', () => { bridge.states([], undefined, true); bridge.setOutput(false); });

// ---------------------------------------------------------------------------------------------
// the panel
// ---------------------------------------------------------------------------------------------

function renderPanel() {
  const now = clock();
  const on = app.cameraOn;
  const cal = app.engine.calibrated;

  setChip(el.chipCam, on ? 'ok' : '', on ? `Camera on, ${app.fps} fps` : 'Camera off');
  setChip(el.chipBody, !on ? '' : app.body ? 'ok' : 'bad', !on ? 'No body' : app.body ? 'Body in view' : 'No body');
  setChip(el.chipCal, cal ? 'ok' : 'warn', cal ? 'Calibrated' : 'Not calibrated');

  const done = { camera: on, mode: on && (app.modeChosen || app.positioned || cal), position: on && (app.positioned || cal), calibrate: cal, fight: app.fought };
  let marked = false;
  [...el.steps.children].forEach((li) => {
    const isDone = done[li.dataset.step];
    li.classList.toggle('done', isDone);
    li.classList.toggle('now', !isDone && !marked);
    if (!isDone) marked = true;
  });

  const pills = [];
  if (on && app.body) {
    for (const s of app.cameraStates) pills.push(`<span class="pill${s === 'GUARD DOWN' ? ' warn' : ''}">${s}</span>`);
    if (!app.engine.calibrated) pills.push('<span class="pill warn">calibrate for duck and slip</span>');
  }
  el.states.innerHTML = pills.join('') || '<span class="pill dim">no moves yet</span>';
  el.meterL.style.width = `${Math.min(100, (app.speeds[0] / 12) * 100)}%`;
  el.meterR.style.width = `${Math.min(100, (app.speeds[1] / 12) * 100)}%`;
  el.counts.innerHTML = Object.entries(app.counts).map(([k, v]) => `<div><b>${v}</b>${k.toLowerCase()}</div>`).join('');
  el.lastPunch.textContent = `Last punch: ${app.lastText || 'none yet'}`;

  if (app.mode.angleGuide && on) {
    const c = app.angle.coach(now);
    el.gaugeDot.style.display = c.angle === null ? 'none' : 'block';
    if (c.angle !== null) el.gaugeDot.style.left = `${Math.min(100, Math.max(0, (c.angle / 90) * 100))}%`;
  }
}

function setChip(node, level, text) {
  node.className = 'chip ' + level;
  node.textContent = text;
}

// ---------------------------------------------------------------------------------------------
// start up
// ---------------------------------------------------------------------------------------------

[...el.steps.children].forEach((li, i) => { li.dataset.n = String(i + 1); });
el.gauge.hidden = !app.mode.angleGuide;
el.gaugeZone.style.left = `${((app.angle.target - app.angle.tolerance) / 90) * 100}%`;
el.gaugeZone.style.width = `${((2 * app.angle.tolerance) / 90) * 100}%`;

el.startBtn.addEventListener('click', startCamera);
el.stopBtn.addEventListener('click', stopCamera);
el.calBtn.addEventListener('click', () => calibrateWithCountdown());
el.tabSpar.addEventListener('click', () => setStage('spar'));
el.tabBB.addEventListener('click', () => setStage('bb'));
el.openGameBtn.addEventListener('click', openGameWindow);
el.muteBtn.addEventListener('click', toggleMute);
el.copyCmd.addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(el.bridgeCmd.textContent); el.copyCmd.textContent = 'Copied'; }
  catch { el.copyCmd.textContent = 'Select and copy'; }
  setTimeout(() => { el.copyCmd.textContent = 'Copy'; }, 1500);
});
el.outputSwitch.addEventListener('change', () => {
  app.outputOn = el.outputSwitch.checked;
  bridge.setOutput(app.outputOn);
  renderBridge();
});
el.cameraSelect.addEventListener('change', async () => {
  if (!app.cameraOn) return;
  stopCamera();
  await startCamera();
});
el.leadSelect.addEventListener('change', async () => { app.lead = el.leadSelect.value; await applySettings(); });

try { if (localStorage.getItem('kw-muted') === '1') toggleMute(); } catch { /* storage may be off */ }
renderModes();
renderBridge();
await applySettings();
bridge.start();
setInterval(renderPanel, 100);
renderPanel();

// handy for testing from the console, and for the automated page check
window.kw = { app, game, bridge, onResult };
