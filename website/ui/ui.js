/* Control panel. Plain JavaScript, no libraries, no build step. Built for keyboards and screen readers first:
   real links, buttons, labels and tables; focus moves to the page heading when you change page; changes are
   announced in one polite live region; nothing is drawn with inline styles (the page's CSP forbids them). */
'use strict';

// ------------------------------------------------------------------ small helpers
const $ = (selector, root = document) => root.querySelector(selector);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value == null) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const store = {
  get(key, fallback = '') { try { const v = localStorage.getItem('boxing.' + key); return v == null ? fallback : v; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem('boxing.' + key, value); } catch { /* private window: the page still works */ } },
};

async function api(path, body) {
  const options = body === undefined
    ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('Cannot reach the control panel. Is python app_server.py still running in its terminal?'); }
  let data = {};
  try { data = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) throw new Error(data.error || `Something went wrong (${response.status}).`);
  return data;
}

let announceTimer;
function announce(text) {
  const box = $('#announcer');
  box.textContent = '';
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => { box.textContent = text; }, 60);  // clearing first makes a repeated message read again
}

function confirmDialog(title, text, okLabel = 'Continue') {
  const dialog = $('#confirm');
  $('#confirm-title').textContent = title;
  $('#confirm-text').textContent = text;
  $('#confirm-ok').textContent = okLabel;
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), { once: true });
    dialog.returnValue = 'cancel';
    dialog.showModal();
  });
}

function notice(kind, ...kids) {
  const role = kind === 'bad' ? 'alert' : 'status';
  const word = { ok: 'Done. ', warn: 'Note. ', bad: 'Problem. ' }[kind] || '';
  return h('div', { class: `notice ${kind}`, role }, h('p', {}, h('strong', { text: word }), kids));
}

function tag(kind, text) { return h('span', { class: `tag ${kind}`, text }); }

const ARMS = ['left', 'right'];
const WHAT = {
  JAB: 'Left arm: straight forward', HOOK: 'Left arm: left, forward, then right', UPPERCUT: 'Left arm: down, then up',
  CROSS: 'Right arm: straight forward', 'RIGHT HOOK': 'Right arm: right, forward, then left', 'RIGHT UPPERCUT': 'Right arm: down, then up',
  BLOCK: 'Guard up (camera)', DUCK: 'Duck (camera)', 'SLIP LEFT': 'Slip left (camera)', 'SLIP RIGHT': 'Slip right (camera)',
  'GUARD DOWN': 'Both hands dropped (camera)',
};

// ------------------------------------------------------------------ shared data
const state = { status: null, job: null };
let currentView = null;

let lastStatusKey = '';
async function refreshStatus() {
  let fresh;
  try { fresh = await api('/api/status'); }
  catch (e) { state.status = null; state.error = e.message; lastStatusKey = ''; return; }
  state.error = '';
  state.status = fresh;
  // Redraw only when something a page shows has changed. Redrawing every few seconds would throw a keyboard
  // user's focus off the button they are on.
  const key = JSON.stringify({ ...fresh, job: fresh.job.running });
  if (key !== lastStatusKey) {
    lastStatusKey = key;
    if (currentView && currentView.update) currentView.update();
  }
}

function totalRecordings(arm) { return Object.values(arm.recordings || {}).reduce((a, b) => a + b, 0); }

// A model can outlive its recordings (they were deleted): say so, or "Ready" would mislead.
function modelTag(arm) {
  if (!arm.model.exists) return tag('warn', 'Not trained');
  if (totalRecordings(arm) === 0) return tag('warn', 'Old model');
  return tag('ok', 'Ready');
}

function modelText(model) {
  if (!model || !model.exists) return 'Not trained';
  const when = model.modified ? new Date(model.modified * 1000).toLocaleString() : 'unknown time';
  const acc = typeof model.cv_accuracy === 'number' ? `, ${Math.round(model.cv_accuracy * 100)}% on practice test` : '';
  return `Trained ${when}${acc}`;
}

function apiNeeded() {
  return state.status ? null : notice('bad', state.error || 'Loading the setup…');
}

// ------------------------------------------------------------------ running tool bar
const job = { cursor: 0, lines: [], started: 0, wasRunning: false, toolTitle: '' };

function formatSeconds(s) { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }

function renderJob(snap) {
  state.job = snap;
  const bar = $('#jobbar');
  if (!snap.tool) { bar.hidden = true; return; }
  bar.hidden = false;
  if (snap.started !== job.started || snap.total < job.cursor) { job.cursor = 0; job.lines = []; job.started = snap.started; }
  job.lines.push(...snap.lines.slice(Math.max(0, job.cursor - snap.from)));
  job.cursor = snap.total;
  job.lines = job.lines.slice(-600);
  const log = $('#job-log');
  const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  log.textContent = job.lines.join('\n');
  if (atEnd) log.scrollTop = log.scrollHeight;
  $('#job-title').textContent = snap.title;
  let stateText;
  if (snap.running) stateText = `Running for ${formatSeconds(Date.now() / 1000 - snap.started)}. Its camera window may be behind this one: check the taskbar.`;
  else if (snap.exit_code === 0) stateText = 'Finished.';
  else stateText = `Stopped (exit code ${snap.exit_code}). Open the output to see why.`;
  $('#job-state').textContent = stateText;
  $('#job-stop').disabled = !snap.running;
  $('#job-send').hidden = !(snap.running && snap.typed);
  if (job.wasRunning && !snap.running) {
    announce(`${snap.title}: ${snap.exit_code === 0 ? 'finished' : 'stopped'}.`);
    refreshStatus();
    if (currentView && currentView.jobDone) currentView.jobDone();
  }
  job.wasRunning = snap.running;
}

async function pollJob() {
  try { renderJob(await api(`/api/job?since=${job.cursor}`)); } catch { /* the page shows connection problems elsewhere */ }
  setTimeout(pollJob, state.job && state.job.running ? 1000 : 3000);
}

function openOutput() {
  $('#job-log-wrap').hidden = false;
  $('#job-toggle').setAttribute('aria-expanded', 'true');
  $('#job-toggle').textContent = 'Hide output';
}

async function runTool(body, started) {
  try {
    const result = await api('/api/run', body);
    announce(`${result.title} started. ${started || ''}`);
    openOutput();
    await pollNow();
    return true;
  } catch (e) {
    announce(`Could not start: ${e.message}`);
    return false;
  }
}

async function pollNow() { renderJob(await api(`/api/job?since=${job.cursor}`)); }

function wireJobBar() {
  $('#job-toggle').addEventListener('click', () => {
    const wrap = $('#job-log-wrap');
    const open = wrap.hidden;
    wrap.hidden = !open;
    $('#job-toggle').setAttribute('aria-expanded', String(open));
    $('#job-toggle').textContent = open ? 'Hide output' : 'Show output';
  });
  $('#job-stop').addEventListener('click', async () => {
    const snap = state.job;
    if (snap && snap.tool === 'capture') {
      const ok = await confirmDialog('Stop recording now?', 'Stopping ends the recording at once and does not train. To finish properly and train, cancel and type q in the box under the output instead.', 'Stop now');
      if (!ok) return;
    }
    try { await api('/api/stop', {}); announce('Stopping.'); await pollNow(); } catch (e) { announce(e.message); }
  });
  $('#job-send').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('#job-line');
    const line = input.value.trim();
    if (!line) return;
    try { await api('/api/input', { line }); input.value = ''; await pollNow(); }
    catch (e) { announce(e.message); }
  });
}

// ------------------------------------------------------------------ pages
function pageHeading(text) { return h('h1', { tabindex: '-1', id: 'page-title', text }); }

function viewHome(root) {
  const summary = h('div');
  const next = h('div');
  const addresses = h('p');
  root.append(
    pageHeading('Boxing controller'),
    h('p', { class: 'lead', text: 'Punch with a glove on each arm and a webcam watching. A trained model decides whether each punch was a jab, hook or uppercut, and the laptop presses the matching key in your game.' }),
    next,
    h('h2', { text: 'Where things stand' }), summary,
    h('h2', { text: 'Go to' }),
    h('ul', { class: 'cards' }, [
      ['#/setup', 'Setup guide', 'Hotspot, gloves, phone and camera, step by step.'],
      ['#/calibrate', 'Calibrate', 'Check the camera and how punches are found.'],
      ['#/record', 'Record', 'Capture training punches for one or both arms.'],
      ['#/train', 'Train', 'Build the model from your recordings.'],
      ['#/recordings', 'Recordings', 'See, delete or restore recording sessions.'],
      ['#/play', 'Play', 'Pick a game and start the controller.'],
      ['#/controls', 'Controls', 'Choose which key each punch presses.'],
      ['#/help', 'Help', 'Fixes for common problems, and accessibility.'],
    ].map(([href, name, text]) => h('li', {}, h('a', { class: 'card', href }, h('strong', { text: name }), h('span', { text }))))),
    h('h2', { text: 'This laptop’s address' }), addresses);

  function update() {
    const st = state.status;
    summary.replaceChildren(apiNeeded() || h('div', { class: 'table-wrap' }, h('table', {},
      h('caption', { text: 'Recordings and trained models for each arm' }),
      h('thead', {}, h('tr', {}, ['Arm', 'Recordings', 'Model'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', {}, ARMS.map((name) => {
        const arm = st.arms[name];
        return h('tr', {}, h('th', { scope: 'row', text: name === 'left' ? 'Left arm' : 'Right arm' }),
          h('td', { text: `${totalRecordings(arm)} punches` }),
          h('td', {}, modelTag(arm), ' ', modelText(arm.model)));
      })))));
    if (!st) return;
    const left = st.arms.left, right = st.arms.right;
    let step;
    if (st.job.running) step = ['A tool is running: ', st.job.title, '. Its output is in the bar above.'];
    else if (!left.model.exists && totalRecordings(left) === 0) step = ['Start by ', h('a', { href: '#/setup', text: 'setting up' }), ' the left glove, then ', h('a', { href: '#/record', text: 'record the left arm' }), '.'];
    else if (!left.model.exists) step = ['The left arm has recordings but no model. ', h('a', { href: '#/train', text: 'Train it' }), '.'];
    else if (!right.model.exists || totalRecordings(right) === 0) step = ['The left arm is ready. For two arms, ', h('a', { href: '#/record', text: 'record the right arm' }), ' (phone or glove). Or ', h('a', { href: '#/play', text: 'play with the left arm' }), ' now.'];
    else step = ['Both arms are trained. ', h('a', { href: '#/play', text: 'Go to Play' }), '.'];
    next.replaceChildren(h('div', { class: 'notice' }, h('h2', { text: 'Next step' }), h('p', {}, step)));
    addresses.replaceChildren(st.addresses.length
      ? h('span', {}, 'Put one of these in the glove’s config.h as LAPTOP_IP (the hotspot’s is usually 192.168.137.1): ', st.addresses.map((a, i) => [i ? ', ' : '', h('code', { text: a })]), '.')
      : 'No network address found. Turn the hotspot on, then reload this page.');
  }
  update();
  return { update };
}

function viewSetup(root) {
  const result = h('div');
  const addr = h('span');
  const waitField = h('input', { id: 'wait', type: 'number', min: '5', max: '120', value: '45' });
  const step = (title, ...body) => h('details', {}, h('summary', { text: title }), body);
  root.append(
    pageHeading('Setup guide'),
    h('p', { class: 'lead', text: 'Do these once, in order. Each step opens to show what to do. When something is not working, jump to the check at the end.' }),
    step('1. Install and start this panel',
      h('p', { text: 'In the project folder, in a terminal:' }),
      h('ol', { class: 'steps' },
        h('li', {}, 'Create a virtual environment: ', h('code', { text: 'python -m venv .venv' }), ', then activate it.'),
        h('li', {}, 'Install the requirements: ', h('code', { text: 'pip install -r requirements.txt' }), '.'),
        h('li', {}, 'Start this panel: ', h('code', { text: 'python app_server.py' }), '. Leave that terminal open.'))),
    step('2. Turn on the phone or laptop hotspot',
      h('ul', {},
        h('li', { text: 'The gloves only join a 2.4 GHz network. Choose 2.4 GHz for the hotspot.' }),
        h('li', { text: 'Choose WPA2 security. The glove cannot join a WPA3 or WPA2/WPA3 mixed network.' }),
        h('li', { text: 'On a Windows laptop hotspot, turn off “turn off automatically when no devices are connected”, or it switches itself off.' }),
        h('li', { text: 'Keep the laptop and every device on this same hotspot.' }))),
    step('3. Left glove (ESP32-CAM with the motion sensor)',
      h('ol', { class: 'steps' },
        h('li', {}, 'Copy ', h('code', { text: 'firmware/glove_imu/config.example.h' }), ' to ', h('code', { text: 'config.h' }), '.'),
        h('li', {}, 'Fill in the hotspot name and password, and set LAPTOP_IP to this laptop’s address: ', addr, '.'),
        h('li', {}, 'Flash it with ', h('code', { text: 'python flash.py cam --ip auto' }), ' while the board is in upload mode. ', h('code', { text: '--ip auto' }), ' puts this laptop’s address in for you. Every option is listed in COMMANDS.md.'),
        h('li', { text: 'Power the glove from its battery or USB. Never connect GPIO12 to 3V3.' }),
        h('li', { text: 'Run the glove check below. It waits for the glove, then checks the data and asks you to throw a few punches.' }))),
    step('4. Right arm (phone with the phyphox app)',
      h('ol', { class: 'steps' },
        h('li', { text: 'Install phyphox and join the same hotspot as the laptop.' }),
        h('li', { text: 'Open the experiment called IMU (it must show accelerometer and gyroscope). Start measuring.' }),
        h('li', { text: 'Open the three-dot menu and turn on Allow remote access. Note the address it shows, such as 192.168.137.252.' }),
        h('li', { text: 'Keep the phone in your hand or taped to your glove, screen on and phyphox open. Type its address on the Record and Play pages.' }),
        h('li', { text: 'If you restart phyphox, start measuring again. The laptop picks the data up again by itself.' }))),
    step('5. Let Python through the firewall',
      h('ul', {},
        h('li', { text: 'When Windows asks, allow Python on private networks.' }),
        h('li', { text: 'Antivirus firewalls (Norton and similar) can block the glove’s data. If the glove check hears nothing, try turning the firewall off for a minute to test, and allow Python instead.' }))),
    step('6. Place the camera',
      h('ul', {},
        h('li', { text: 'Put the webcam straight in front of you, about 1.5 to 2 metres away, at chest height.' }),
        h('li', { text: 'Your whole upper body, both arms and your head, should be in view. Use even light, and a plain wall behind you if you can.' }),
        h('li', { text: 'A camera straight on barely sees a straight punch, so the glove does most of the work for jabs.' }))),
    h('h2', { text: 'Check the left glove' }),
    h('form', { class: 'panel', onsubmit: async (event) => {
      event.preventDefault();
      if (await runTool({ tool: 'glove_check', wait: waitField.value }, 'It is waiting for the glove.')) {
        result.replaceChildren(notice('ok', 'The glove check is running. Its steps appear in the output bar at the top.'));
      } else result.replaceChildren(notice('bad', 'Could not start the check. Another tool may be running: stop it first.'));
    } },
      h('div', { class: 'field' }, h('label', { for: 'wait', text: 'Seconds to wait for the glove' }), waitField),
      h('button', { type: 'submit', class: 'btn primary', text: 'Check the left glove' }), result));
  function update() { addr.replaceChildren(...((state.status && state.status.addresses.length) ? state.status.addresses.map((a, i) => [i ? ', ' : '', h('code', { text: a })]).flat() : ['(turn the hotspot on, then reload)'])); }
  update();
  return { update };
}

function cameraOptions() {
  const windowed = h('input', { id: 'windowed', type: 'checkbox' });
  const camera = h('input', { id: 'camera', type: 'number', min: '0', max: '9', value: store.get('camera', '0') });
  const node = h('details', {}, h('summary', { text: 'Camera options' }),
    h('div', { class: 'choice' }, windowed, h('div', {}, h('label', { for: 'windowed', text: 'Use a window instead of full screen' }), h('p', { class: 'help', text: 'Easier to put next to this page.' }))),
    h('div', { class: 'field' }, h('label', { for: 'camera', text: 'Camera number' }), camera, h('p', { class: 'help', text: '0 is the first camera. Try 1 if the wrong camera opens.' })));
  return { node, values: () => { store.set('camera', camera.value); return { windowed: windowed.checked, camera: camera.value }; } };
}

function viewCalibrate(root) {
  const cam = cameraOptions();
  const result = h('div');
  root.append(
    pageHeading('Calibrate'),
    h('p', { class: 'lead', text: 'Calibrating teaches the camera your stance, so it can tell your guard from a block or a duck. Do it each time you sit down to play, and whenever you move.' }),
    h('h2', { text: 'Steps' }),
    h('ol', { class: 'steps' },
      h('li', { text: 'Stand about 1.5 to 2 metres from the webcam, facing it, in your fighting guard.' }),
      h('li', {}, 'Press ', h('strong', { text: 'Start the camera check' }), '. A camera window opens. If you cannot see it, check the taskbar or press Alt and Tab.'),
      h('li', {}, 'In the camera window press ', h('kbd', { text: 'C' }), '. A 5 second countdown starts. Get into your guard and hold still until it ends.'),
      h('li', { text: 'Throw a few punches. Each punch’s start is marked green, and its end blue (it stopped) or orange (it timed out).' }),
      h('li', {}, 'To change the timeout (how long a punch may last), type a number of milliseconds such as ', h('kbd', { text: '350' }), ' in the command box that appears in the output bar, then ', h('kbd', { text: 'save' }), '. The timeout always needs the model retrained.'),
      h('li', {}, 'Finish with ', h('kbd', { text: 'q' }), ' in the command box, or the Stop button.')),
    h('form', { class: 'panel', onsubmit: async (event) => {
      event.preventDefault();
      const ok = await runTool({ tool: 'calibrate', ...cam.values() }, 'Look for its camera window.');
      result.replaceChildren(ok ? notice('ok', 'The camera check started. Press C in its window to calibrate.') : notice('bad', 'Could not start. Another tool may be running: stop it first.'));
    } }, cam.node, h('div', { class: 'actions' }, h('button', { type: 'submit', class: 'btn primary', text: 'Start the camera check' })), result),
    notice('warn', 'Only one program can use the webcam at a time. Stop this before you record or play, and close other apps that use the camera.'));
  return {};
}

function viewRecord(root) {
  const result = h('div');
  const cam = cameraOptions();
  const person = h('input', { id: 'person', type: 'text', required: true, autocomplete: 'name', value: store.get('person') });
  const target = h('input', { id: 'target', type: 'number', min: '1', max: '500', value: store.get('target', '25') });
  const phone = h('input', { id: 'phone', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: store.get('phone'), placeholder: 'for example 192.168.137.252' });
  const phoneField = h('div', { class: 'field' }, h('label', { for: 'phone', text: 'Phone address for the right arm (optional)' }), phone,
    h('p', { class: 'help', text: 'The address phyphox shows when remote access is on. Leave empty to use a right glove instead.' }));
  const arms = ['left', 'right', 'both'].map((value) => ({ value, input: h('input', { type: 'radio', name: 'arm', id: 'arm-' + value, value, checked: value === store.get('recArm', 'left') }) }));
  const how = h('div');
  const counts = h('div');
  const labelFor = { left: 'Left arm only', right: 'Right arm only', both: 'Both arms (combos, one punch per arm)' };
  const chosen = () => arms.find((a) => a.input.checked).value;
  const sync = () => { phoneField.hidden = chosen() === 'left'; };
  arms.forEach((a) => a.input.addEventListener('change', () => { sync(); update(); }));
  sync();

  root.append(
    pageHeading('Record training punches'),
    h('p', { class: 'lead', text: 'The screen calls each punch with a sound, in random order. Throw it once, then return to guard. Training starts by itself when you finish, and the old model is kept as a backup.' }),
    h('form', { class: 'panel', onsubmit: async (event) => {
      event.preventDefault();
      store.set('person', person.value); store.set('target', target.value); store.set('phone', phone.value); store.set('recArm', chosen());
      const ok = await runTool({ tool: 'capture', arm: chosen(), person: person.value, target: target.value, phone: phone.value.trim(), ...cam.values() }, 'Calibrate with C, then press the full stop key to start.');
      result.replaceChildren(ok ? notice('ok', 'Recording opened. In its window press C to calibrate, get into guard during the countdown, then press the full stop (.) key to start.') : notice('bad', 'Could not start. Check your name is filled in and that no other tool is running.'));
    } },
      h('fieldset', {}, h('legend', { text: 'Which arm?' }),
        arms.map((a) => h('div', { class: 'choice' }, a.input, h('label', { for: 'arm-' + a.value, text: labelFor[a.value] })))),
      h('div', { class: 'field' }, h('label', { for: 'person', text: 'Your name' }), person, h('p', { class: 'help', text: 'Recordings are filed under this name.' })),
      h('div', { class: 'field' }, h('label', { for: 'target', text: 'Punches of each kind' }), target, h('p', { class: 'help', text: '25 is a quick session. 100 of each is the plan for the final model.' })),
      phoneField, cam.node,
      h('div', { class: 'actions' }, h('button', { type: 'submit', class: 'btn primary', text: 'Start recording' })), result),
    h('h2', { text: 'How to throw each punch' }), how,
    h('h2', { text: 'What you have so far' }), counts,
    h('h2', { text: 'During a recording' }),
    h('ul', {},
      h('li', {}, h('kbd', { text: 'C' }), ' in the camera window calibrates (5 second countdown). The full stop key ', h('kbd', { text: '.' }), ' starts, pauses and resumes from any window.'),
      h('li', { text: 'Every 15 prompts is a “MOVE, DON’T PUNCH” round: shuffle, adjust your guard, feint. These teach the model to press nothing for movement that is not a punch.' }),
      h('li', {}, 'Made a mistake? Type ', h('kbd', { text: 'x' }), ' in the command box to undo the last punch. ', h('kbd', { text: 'q' }), ' finishes and trains. ', h('kbd', { text: 'discard' }), ' throws the whole session away.'),
      h('li', { text: 'Do not move the glove on your hand between sessions. The model learns how the glove sits.' })));

  function update() {
    const st = state.status;
    if (!st) { how.replaceChildren(apiNeeded()); return; }
    const picked = chosen() === 'both' ? ARMS : [chosen()];
    how.replaceChildren(h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Punches to record' }),
      h('thead', {}, h('tr', {}, ['Arm', 'Punch', 'Motion'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', {}, picked.flatMap((name) => st.arms[name].labels.map((label) =>
        h('tr', {}, h('th', { scope: 'row', text: name === 'left' ? 'Left' : 'Right' }), h('td', { text: label }), h('td', { text: st.arms[name].how[label] || '' }))))))));
    counts.replaceChildren(h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Recordings on disk' }),
      h('thead', {}, h('tr', {}, ['Arm', 'Punch', 'Recordings'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', {}, ARMS.flatMap((name) => [...st.arms[name].labels, 'NONE'].map((label) =>
        h('tr', {}, h('th', { scope: 'row', text: name === 'left' ? 'Left' : 'Right' }), h('td', { text: label === 'NONE' ? 'NONE (not a punch)' : label }), h('td', { text: String(st.arms[name].recordings[label] || 0) }))))))));
  }
  update();
  return { update };
}

function viewTrain(root) {
  const body = h('div');
  const result = h('div');
  root.append(
    pageHeading('Train the model'),
    h('p', { class: 'lead', text: 'Training builds the model that names each punch. It runs by itself when a recording ends, so use this page to retrain after you delete recordings or change settings.' }),
    result, body,
    h('h2', { text: 'Reading the accuracy' }),
    h('p', { text: 'The practice test hides each recording from the model, then checks its guess. It is optimistic when all recordings come from one session. The “new session” figure in the training output, which trains on some sessions and tests on another, is the one closest to real play.' }));
  function update() {
    const st = state.status;
    if (!st) { body.replaceChildren(apiNeeded()); return; }
    body.replaceChildren(...ARMS.map((name) => {
      const arm = st.arms[name];
      const total = totalRecordings(arm);
      const button = h('button', { type: 'button', class: 'btn primary', disabled: total === 0 || st.job.running, text: `Train the ${name} arm`, onclick: async () => {
        const ok = await runTool({ tool: 'train', arm: name }, 'It prints the accuracy when done.');
        result.replaceChildren(ok ? notice('ok', `Training the ${name} arm. Watch the output bar.`) : notice('bad', 'Could not start. Another tool may be running.'));
      } });
      return h('section', { 'aria-labelledby': 'train-' + name },
        h('h2', { id: 'train-' + name, text: name === 'left' ? 'Left arm' : 'Right arm' }),
        h('p', {}, modelTag(arm), ' ', modelText(arm.model)),
        arm.model.exists && total === 0 ? h('p', { class: 'help', text: 'This model was trained from recordings that are no longer here (deleted or moved). It still works, but record this arm again for a model you can trust.' }) : h('span'),
        arm.model.error ? notice('bad', 'The saved model cannot be read: ' + arm.model.error) : null,
        h('p', { text: `${total} recordings: ` + (Object.entries(arm.recordings).map(([k, v]) => `${k} ${v}`).join(', ') || 'none yet') + '.' }),
        h('div', { class: 'actions' }, button),
        total === 0 ? h('p', { class: 'help' }, 'Nothing to train on. ', h('a', { href: '#/record', text: 'Record this arm first' }), '.') : null);
    }));
  }
  update();
  return { update, jobDone: update };
}

function viewRecordings(root) {
  const live = h('div');
  const removed = h('div');
  const result = h('div');
  const retrain = h('input', { type: 'checkbox', id: 'retrain', checked: true });
  let data = { sessions: [], deleted: [] };
  const checks = { sessions: new Set(), deleted: new Set() };

  async function load() {
    try { data = await api('/api/sessions'); } catch (e) { live.replaceChildren(notice('bad', e.message)); return; }
    checks.sessions.clear(); checks.deleted.clear();
    draw();
  }

  function table(list, which, caption) {
    if (!list.length) return h('p', { text: which === 'sessions' ? 'No recordings yet.' : 'Nothing deleted.' });
    const all = h('input', { type: 'checkbox', 'aria-label': `Select all in “${caption}”`, onchange: (e) => {
      list.forEach((s) => (e.target.checked ? checks[which].add(s.id) : checks[which].delete(s.id)));
      draw();
    }, checked: list.every((s) => checks[which].has(s.id)) });
    return h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: caption }),
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, all), ['Session', 'Person', 'Left arm', 'Right arm'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', {}, list.map((s) => h('tr', {},
        h('td', {}, h('input', { type: 'checkbox', 'aria-label': `Select session ${s.id}`, checked: checks[which].has(s.id), onchange: (e) => { (e.target.checked ? checks[which].add(s.id) : checks[which].delete(s.id)); } })),
        h('th', { scope: 'row', text: s.id }), h('td', { text: s.person || '—' }),
        ...ARMS.map((a) => h('td', { text: s.arms[a] ? Object.entries(s.arms[a]).map(([k, v]) => `${k} ${v}`).join(', ') : '—' })))))));
  }

  function draw() {
    live.replaceChildren(table(data.sessions, 'sessions', 'Sessions used for training'),
      h('div', { class: 'actions' }, h('button', { type: 'button', class: 'btn danger', text: 'Delete selected sessions…', onclick: () => act('delete') })));
    removed.replaceChildren(table(data.deleted, 'deleted', 'Deleted sessions (recoverable)'),
      data.deleted.length ? h('div', { class: 'actions' }, h('button', { type: 'button', class: 'btn', text: 'Restore selected sessions', onclick: () => act('restore') })) : h('span'));
  }

  async function act(action) {
    const ids = [...checks[action === 'delete' ? 'sessions' : 'deleted']];
    if (!ids.length) { result.replaceChildren(notice('warn', 'Tick at least one session first.')); announce('Tick at least one session first.'); return; }
    if (action === 'delete') {
      const ok = await confirmDialog(`Delete ${ids.length} session(s)?`, 'The recordings move to a deleted folder and can be restored from this page. The model is retrained without them if the box below is ticked.', 'Delete');
      if (!ok) return;
    }
    const started = await runTool({ tool: 'sessions', action, sessions: ids, retrain: retrain.checked }, 'The list refreshes when it finishes.');
    result.replaceChildren(started ? notice('ok', `${action === 'delete' ? 'Deleting' : 'Restoring'} ${ids.length} session(s). The list refreshes when it finishes.`) : notice('bad', 'Could not start. Another tool may be running.'));
  }

  root.append(pageHeading('Recordings'),
    h('p', { class: 'lead', text: 'Each time you record is one session. Delete a session that has mistakes, such as punches that were mislabelled or recorded with the glove in a different place. Deleting is recoverable.' }),
    h('div', { class: 'choice' }, retrain, h('div', {}, h('label', { for: 'retrain', text: 'Retrain the models afterwards' }), h('p', { class: 'help', text: 'Recommended, so the saved model matches your recordings.' }))),
    result, live, h('h2', { text: 'Deleted' }), removed);
  load();
  return { jobDone: load };
}

async function viewPlay(root) {
  let games = [];
  try { games = (await (await fetch('ui/games.json', { cache: 'no-store' })).json()).games; } catch { /* shown below */ }
  let controls = null;
  try { controls = await api('/api/controls'); } catch { /* shown below */ }
  if (!games.length) { root.append(pageHeading('Play'), notice('bad', 'The games list could not be loaded.')); return {}; }

  const result = h('div');
  const cam = cameraOptions();
  const detail = h('div');
  const keys = h('div');
  const radios = games.map((g) => ({ game: g, input: h('input', { type: 'radio', name: 'game', id: 'game-' + g.id, value: g.id, checked: g.id === store.get('game', games[0].id) }) }));
  const gameNow = () => (radios.find((r) => r.input.checked) || radios[0]).game;
  const arms = ['left', 'right', 'both'].map((value) => ({ value, input: h('input', { type: 'radio', name: 'playarm', id: 'play-' + value, value, checked: value === store.get('playArm', 'both') }) }));
  const armNow = () => (arms.find((a) => a.input.checked) || arms[2]).value;
  const phone = h('input', { id: 'play-phone', type: 'text', inputmode: 'decimal', autocomplete: 'off', value: store.get('phone'), placeholder: 'for example 192.168.137.252' });
  const phoneField = h('div', { class: 'field' }, h('label', { for: 'play-phone', text: 'Phone address for the right arm (optional)' }), phone);
  const confidence = h('input', { id: 'confidence', type: 'number', min: '0.05', max: '0.99', step: '0.05', value: store.get('confidence') });
  const press = h('input', { id: 'press', type: 'checkbox' });
  const startButton = h('button', { type: 'submit', class: 'btn primary' });
  const form = h('form', { class: 'panel' });

  function syncForm() {
    const g = gameNow();
    phoneField.hidden = armNow() === 'left';
    startButton.textContent = press.checked ? 'Start and press keys' : 'Start practice (no keys pressed)';
    form.hidden = g.arm === 'none';
  }

  function drawDetail() {
    const g = gameNow();
    store.set('game', g.id);
    const rows = [];
    if (controls) {
      for (const [action, spec] of Object.entries(controls.normal)) rows.push([action, WHAT[action] || '', spec.bind, (g.suggest || {})[action] || '']);
    }
    const showSuggest = rows.some((r) => r[3]);
    detail.replaceChildren(
      h('h2', { text: g.name }), h('p', { class: 'lead', text: g.summary }),
      h('ol', { class: 'steps' }, g.steps.map((s) => h('li', { text: s }))),
      g.link ? h('p', {}, h('a', { class: 'btn primary', href: g.link, target: '_blank', rel: 'noopener', text: 'Open the sparring game (new tab)' })) : h('span'));
    keys.replaceChildren();
    if (g.arm !== 'none' && controls) {
      keys.append(h('h2', { text: 'Keys the controller presses' }),
        h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Punches: bind each key in the game’s controls' }),
          h('thead', {}, h('tr', {}, ['Punch', 'Motion', 'Key', showSuggest ? 'Suggested in game' : null].filter(Boolean).map((t) => h('th', { scope: 'col', text: t })))),
          h('tbody', {}, rows.map((r) => h('tr', {}, h('th', { scope: 'row', text: r[0] }), h('td', { text: r[1] }), h('td', {}, h('kbd', { text: r[2].toUpperCase() })), showSuggest ? h('td', { text: r[3] }) : null))))),
        h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Defence: held while your body does it' }),
          h('thead', {}, h('tr', {}, ['Move', 'Key'].map((t) => h('th', { scope: 'col', text: t })))),
          h('tbody', {}, Object.entries(controls.defence).map(([a, k]) => h('tr', {}, h('th', { scope: 'row', text: a }), h('td', {}, h('kbd', { text: k.toUpperCase() }))))))),
        h('p', {}, 'Change any of these on the ', h('a', { href: '#/controls', text: 'Controls page' }), '.'));
    }
    if (g.arm !== 'none') arms.find((a) => a.value === (g.arm === 'both' ? armNow() : g.arm)).input.checked = true;
    syncForm();
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    store.set('playArm', armNow()); store.set('phone', phone.value); store.set('confidence', confidence.value);
    if (press.checked) {
      const ok = await confirmDialog('Press keys for real?', 'The controller will press real keys in whichever window has focus. After it starts, click on your game window. Stop it any time with the Stop button.', 'Start with keys on');
      if (!ok) return;
    }
    const started = await runTool({ tool: 'live', arm: armNow(), phone: phone.value.trim(), confidence: confidence.value, live: press.checked, ...cam.values() }, press.checked ? 'Click your game window now.' : 'Keys are not being pressed.');
    result.replaceChildren(started
      ? notice('ok', press.checked ? 'Running with keys on. Click your game window now so it has focus.' : 'Practice started. Nothing is pressed; the output shows what each punch would press.')
      : notice('bad', 'Could not start. A tool may already be running, or a model may be missing: train it first.'));
  });
  press.addEventListener('change', syncForm);
  arms.forEach((a) => a.input.addEventListener('change', syncForm));
  radios.forEach((r) => r.input.addEventListener('change', drawDetail));

  form.append(
    h('h2', { text: 'Start the controller' }),
    h('fieldset', {}, h('legend', { text: 'Which arms?' }),
      arms.map((a) => h('div', { class: 'choice' }, a.input, h('label', { for: 'play-' + a.value, text: { left: 'Left arm only', right: 'Right arm only', both: 'Both arms' }[a.value] })))),
    phoneField,
    h('div', { class: 'field' }, h('label', { for: 'confidence', text: 'Confidence needed to press a key (optional)' }), confidence,
      h('p', { class: 'help', text: 'From 0.05 to 0.99. Lower presses more often, higher presses only when sure. Leave empty for the saved setting. While playing, [ and ] change it.' })),
    h('div', { class: 'choice' }, press, h('div', {}, h('label', { for: 'press', text: 'Press keys for real' }), h('p', { class: 'help', text: 'Off is practice: it only shows what it would press.' }))),
    cam.node, h('div', { class: 'actions' }, startButton), result);

  root.append(pageHeading('Play'),
    h('p', { class: 'lead', text: 'Choose a game. You open the game yourself; this starts the controller that watches you and presses its keys.' }),
    h('fieldset', {}, h('legend', { text: 'Game' }), radios.map((r) => h('div', { class: 'game' }, h('div', { class: 'choice' }, r.input, h('div', {}, h('label', { for: 'game-' + r.game.id, text: r.game.name }), h('p', { class: 'help', text: r.game.kind })))))),
    detail, keys, form,
    notice('warn', 'The camera is used by the controller while it runs. Close calibration and recording first, and any other app using the webcam.'));
  drawDetail();
  return {};
}

function viewControls(root) {
  const result = h('div');
  const body = h('div');
  const inputs = {};
  let data = null;
  root.append(pageHeading('Controls'),
    h('p', { class: 'lead', text: 'Choose the key each punch and defence move presses. Each punch needs its own key, so each of the six can be bound to its own button in a game. Changes apply to a running controller within a second.' }),
    result, body);

  async function load() {
    try { data = await api('/api/controls'); } catch (e) { body.replaceChildren(notice('bad', e.message)); return; }
    draw();
  }

  function keyInput(id, value, label) {
    const input = h('input', { type: 'text', id, value, autocomplete: 'off', spellcheck: 'false', 'aria-label': label, maxlength: '20' });
    return input;
  }

  function draw() {
    inputs.normal = {}; inputs.hold = {}; inputs.defence = {};
    const punchRows = Object.entries(data.normal).map(([action, spec]) => {
      inputs.normal[action] = keyInput('k-' + action.replace(/\W/g, '-'), spec.bind, `Key for ${action}`);
      inputs.hold[action] = h('input', { type: 'number', id: 'h-' + action.replace(/\W/g, '-'), min: '10', max: '1000', value: String(spec.hold_ms), 'aria-label': `Hold time in milliseconds for ${action}` });
      return h('tr', {}, h('th', { scope: 'row', text: action }), h('td', { text: WHAT[action] || '' }), h('td', {}, inputs.normal[action]), h('td', {}, inputs.hold[action]));
    });
    const defRows = Object.entries(data.defence).map(([action, key]) => {
      inputs.defence[action] = keyInput('d-' + action.replace(/\W/g, '-'), key, `Key for ${action}`);
      return h('tr', {}, h('th', { scope: 'row', text: action }), h('td', { text: WHAT[action] || '' }), h('td', {}, inputs.defence[action]));
    });
    body.replaceChildren(h('form', { onsubmit: save },
      h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Punches' }),
        h('thead', {}, h('tr', {}, ['Punch', 'Motion', 'Key', 'Hold (ms)'].map((t) => h('th', { scope: 'col', text: t })))), h('tbody', {}, punchRows))),
      h('div', { class: 'table-wrap' }, h('table', {}, h('caption', { text: 'Defence (from the camera)' }),
        h('thead', {}, h('tr', {}, ['Move', 'Meaning', 'Key'].map((t) => h('th', { scope: 'col', text: t })))), h('tbody', {}, defRows))),
      h('p', { class: 'help' }, 'A key is a letter or number, or a name such as ', h('kbd', { text: 'space' }), ', ', h('kbd', { text: 'up' }), ', ', h('kbd', { text: 'enter' }), ' or a mouse button like ', h('kbd', { text: 'mouse_left' }), '. Hold is how long the key stays down.'),
      h('div', { class: 'actions' },
        h('button', { type: 'submit', class: 'btn primary', text: 'Save controls' }),
        h('button', { type: 'button', class: 'btn', text: 'Open the desktop key editor', onclick: () => runTool({ tool: 'controls_editor' }, 'It opens in its own window.') }))),
      h('p', { class: 'help', text: 'Tracking mode (the glove button turns the right hand into a mouse) is set in the desktop key editor.' }));
  }

  async function save(event) {
    event.preventDefault();
    for (const el of body.querySelectorAll('input')) el.removeAttribute('aria-invalid');
    const normal = {}, defence = {};
    for (const action of Object.keys(inputs.normal)) normal[action] = { bind: inputs.normal[action].value.trim(), hold_ms: Number(inputs.hold[action].value) };
    for (const action of Object.keys(inputs.defence)) defence[action] = inputs.defence[action].value.trim();
    const seen = {};
    for (const [action, spec] of Object.entries(normal)) (seen[spec.bind.toLowerCase()] = seen[spec.bind.toLowerCase()] || []).push(action);
    const dupes = Object.entries(seen).filter(([, list]) => list.length > 1);
    if (dupes.length) {
      dupes.forEach(([, list]) => list.forEach((a) => inputs.normal[a].setAttribute('aria-invalid', 'true')));
      const text = dupes.map(([k, list]) => `${k.toUpperCase()} is used for ${list.join(' and ')}`).join('; ');
      result.replaceChildren(notice('bad', `Each punch needs its own key. ${text}.`));
      announce(`Not saved. ${text}.`);
      (body.querySelector('[aria-invalid="true"]') || body).focus();
      return;
    }
    try {
      data = await api('/api/controls', { normal, defence });
      result.replaceChildren(notice('ok', 'Controls saved.'));
      announce('Controls saved.');
      draw();
    } catch (e) {
      result.replaceChildren(notice('bad', e.message));
      announce(`Not saved. ${e.message}`);
    }
  }
  load();
  return {};
}

function viewHelp(root) {
  const q = (question, ...answer) => h('details', {}, h('summary', { text: question }), answer);
  root.append(pageHeading('Help'),
    h('h2', { text: 'When something is not working' }),
    q('The glove check hears nothing', h('ul', {},
      h('li', { text: 'Is the glove powered, and on the same 2.4 GHz WPA2 hotspot as this laptop?' }),
      h('li', { text: 'Is LAPTOP_IP in the glove’s config.h this laptop’s address on the hotspot? Check the Home page. Re-flash after changing it.' }),
      h('li', { text: 'Allow Python through the Windows firewall. Antivirus firewalls can block the data: test with the firewall off for a minute.' }),
      h('li', { text: 'Windows turns the laptop hotspot off by itself when idle. Turn that setting off and the hotspot back on.' }))),
    q('Recording says MISSED or “too little glove data”', h('p', { text: 'The glove’s data had gaps, usually from weak WiFi. Move the glove and laptop closer, and check the hotspot is 2.4 GHz. For a phone arm the rate is lower (about 80 to 100 per second); that is normal.' })),
    q('The phone stopped sending after I restarted phyphox', h('p', { text: 'Start measuring again in phyphox. The laptop reconnects by itself within a couple of seconds.' })),
    q('The camera does not open, or opens the wrong one', h('p', { text: 'Only one program can use the webcam. Stop the running tool and close other apps that use it. Choose another camera number in Camera options.' })),
    q('Hooks and uppercuts get mixed up', h('p', { text: 'Record more of both, with the glove mounted exactly as when you play, then retrain. Delete any recordings that were mislabelled on the Recordings page.' })),
    q('Keys are pressed but the game does nothing', h('p', { text: 'Keys go to the window that has focus. Click your game window after starting. Check the game’s controls use the same keys as the Controls page, and that the game is set to the keyboard.' })),
    q('The control panel says it cannot reach the server', h('p', {}, 'The terminal running ', h('code', { text: 'python app_server.py' }), ' was closed. Start it again and reload this page.')),
    h('h2', { text: 'Accessibility' }),
    h('p', { text: 'This panel is built to work with a keyboard alone, with screen readers, and at large text sizes.' }),
    h('ul', {},
      h('li', { text: 'Every control is a real link, button, field or table with a label. Use Tab and Shift+Tab to move, Enter or Space to activate, and the arrow keys to choose between options.' }),
      h('li', { text: 'The first Tab on any page offers “Skip to main content”. When you change page, focus moves to the page heading and the page title changes.' }),
      h('li', { text: 'Progress and results are announced through a polite live region. The output of a running tool is in a log you can read with the keyboard.' }),
      h('li', { text: 'Use “Display options” at the top for larger text, light, dark or high contrast colours, and less motion. The page also follows your device settings and works in Windows high contrast mode.' }),
      h('li', { text: 'Meaning is never shown by colour alone: statuses always have words.' })),
    h('p', {}, 'The camera tools that open in their own windows (calibration, recording, play) are visual and keyboard-driven. Everything they do can be started, typed to and stopped from this page. If you find a barrier, tell the team, and we will fix it.'));
  return {};
}

// ------------------------------------------------------------------ router
const ROUTES = {
  home: ['Home', viewHome], setup: ['Setup guide', viewSetup], calibrate: ['Calibrate', viewCalibrate], record: ['Record', viewRecord],
  train: ['Train', viewTrain], recordings: ['Recordings', viewRecordings], play: ['Play', viewPlay], controls: ['Controls', viewControls], help: ['Help', viewHelp],
};

let routeToken = 0;
async function route(first) {
  const name = (location.hash.match(/^#\/([a-z]+)/) || [])[1];
  const key = ROUTES[name] ? name : 'home';
  const token = ++routeToken;
  const main = $('#main');
  const root = document.createDocumentFragment();
  const view = (await ROUTES[key][1](root)) || {};
  if (token !== routeToken) return;  // the person moved on while it loaded
  main.replaceChildren(root);
  currentView = view;
  document.title = `${ROUTES[key][0]} · Boxing controller`;
  for (const link of document.querySelectorAll('#nav a')) {
    if (link.getAttribute('href') === `#/${key}`) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  if (view.update) view.update();
  if (!first) { const title = $('#page-title'); (title || main).focus(); window.scrollTo(0, 0); }
}

// ------------------------------------------------------------------ display options
function applyDisplay() {
  const root = document.documentElement;
  root.dataset.size = store.get('size', 'normal');
  const contrast = store.get('contrast', 'auto');
  root.dataset.contrast = contrast;
  root.dataset.motion = store.get('motion', 'auto');
  $('#opt-size').value = root.dataset.size;
  $('#opt-contrast').value = contrast;
  $('#opt-motion').value = root.dataset.motion;
}

function wireDisplay() {
  for (const [id, key] of [['opt-size', 'size'], ['opt-contrast', 'contrast'], ['opt-motion', 'motion']]) {
    $('#' + id).addEventListener('change', (e) => { store.set(key, e.target.value); applyDisplay(); announce('Display option changed.'); });
  }
  applyDisplay();
}

async function start() {
  wireDisplay();
  wireJobBar();
  // Pages are #/name. The skip link's #main is not a page, so it must not trigger the router: it just moves focus.
  $('.skip').addEventListener('click', (event) => { event.preventDefault(); $('#main').focus(); });
  window.addEventListener('hashchange', () => { if (!location.hash || location.hash.startsWith('#/')) route(false); });
  await refreshStatus();
  await route(true);
  pollJob();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 5000);
}

start();
