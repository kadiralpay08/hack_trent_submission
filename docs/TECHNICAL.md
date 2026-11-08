# Keyboard Warrior: technical description

This document explains how the boxing controller works, from the sensor on the glove to the key press in the game. It is written for engineers reading the code: teammates and reviewers. `PROJECT.md` holds the agreed plan, the decision log and the code map; this document describes the mechanisms. Figures marked "measured" come from tests on 2026-11-07 and 2026-11-08.

## 1. What the system does

The boxer wears a motion sensor on each wrist and stands in front of a webcam. When a punch is thrown, the laptop finds it in the glove's motion data, classifies it (for the left arm: jab, hook, uppercut or "not a punch"), and presses the key bound to it. The camera adds body movements that are held as keys while they last: duck, slip left, slip right, block and guard down.

```
 glove (ESP32-C3 + MPU-6500)                     laptop
 ─────────────────────────────                   ──────────────────────────────────────────────────────────
 500 Hz accel + gyro ──UDP over WiFi──▶ glove_receiver ─▶ Segmenter ─▶ punch window ─┐
                       (port 5005 left,  (timestamps)     (start/end)                 │
                        port 5006 right)                                              ▼
 webcam ──▶ pose_tracker (MediaPipe) ──▶ pose buffer ───────────────────▶ features ─▶ random forest
                 │                                                                    │ label + confidence
                 └─▶ body states (duck, slip, block, guard down) ─┐                   ▼
                                                                  └──────────▶ game_input ─▶ SendInput keys
```

Each arm is independent: its own glove, UDP port, recordings, model and labels. Both arms share one camera.

## 2. Hardware

| Part | Detail |
|---|---|
| Glove board | ESP32-C3 (single core, 160 MHz, native USB). Two boards: left and right. |
| Sensor | MPU-6500 (sold as an MPU-9250; `WHO_AM_I` = 0x70, no magnetometer). I2C at 400 kHz. |
| Wiring | VCC to 3V3, GND to GND, SDA to GPIO1, SCL to GPIO3. GPIO2, 8 and 9 (boot pins) and 18/19 (USB) are avoided. |
| Power | USB power bank, or a battery pack on the **5V** pin. Three NiMH cells (3.6 V nominal) are marginal: boards dropped off WiFi on them. Four cells (4.8 V) or a power bank are recommended. Never more than 3.6 V on the 3V3 pin. |
| Camera | Any webcam, straight in front of the boxer. |
| Laptop | Windows (key output uses the Win32 `SendInput` call; the corner popup uses Win32 window styles). |

Earlier hardware is still supported: an ESP32-CAM glove (`firmware/glove_imu/`, training button on GPIO12) and a phone running phyphox as the right arm (`phyphox_imu.py`, polled over HTTP at 80 to 100 Hz).

## 3. Glove firmware (`firmware/glove_imu_c3/`)

**Sensor setup.** The MPU is set to accel ±16 g (2048 LSB/g), gyro ±2000 °/s (16.4 LSB per °/s), 184 Hz low-pass filter and a 500 Hz sample rate. Every 250 ms the firmware reads two registers back; if they changed (the sensor reset after a power dip), it sets the sensor up again.

**Sampling.** A FreeRTOS task wakes every 2 ms, reads the 14 data bytes in one I2C transaction, stamps the frame with `micros()` and puts it on a 100-frame queue. A lower-priority task takes frames off the queue and sends them. Because the C3 has one core, the sampling task has the higher priority so WiFi work cannot delay it.

**Frame format** (19 bytes, the same on every glove):

```
A5 5A | timestamp µs (uint32, little-endian) | ax ay az gx gy gz (int16, big-endian) | XOR of bytes 2-17
```

The second sync byte is `5B` instead of `5A` while a glove's training button is held (ESP32-CAM glove only). The ESP32-S3 glove sends a 26-byte variant (`A5 5C`) with magnetometer readings.

**Packets.** 25 frames go in one UDP packet: 20 packets/s per glove, 50 ms of batching. The earlier 10 frames (50 packets/s) caused dropouts with two gloves on (section 4). Packets go to one address (`LAPTOP_IP` in `config.h`), never broadcast: unicast gets WiFi's automatic retransmissions, while broadcast lost about a third of the samples (measured on a phone hotspot).

**Radio.** Transmit power is 15 dBm. At 8.5 dBm the signal from a moving arm dropped for up to 0.7 s at a time (measured).

**Arm selection.** One sketch serves both arms. It sends to port 5005 (left) by default; building with `--build-property "compiler.cpp.extra_flags=-DGLOVE_UDP_PORT=5006"` makes it the right arm.

**Status.** Every 5 s the board prints its sample rate, read errors, sensor resets, packets sent, samples dropped and WiFi signal strength (RSSI) on its USB serial port.

`firmware/glove_imu_c3_usb/` sends the same frames over USB instead of WiFi. `usb_glove_check.py` decodes them and reports rate, loss, gravity at rest and gyro drift, which isolates sensor faults from WiFi faults.

## 4. Network

The laptop hosts a Windows Mobile Hotspot (2.4 GHz). On it the laptop is always `192.168.137.1`, so the gloves are flashed with that address. The hotspot's "power saving" setting turns it off when no device has been connected for a few minutes; it must be switched off.

**Two gloves on one channel.** Both gloves share one WiFi channel. With 50 packets/s each, the moving glove went silent 32 times for longer than 0.5 s in 45 s of boxing (33 s silent in total), while the same glove alone lost nothing. Moving the arm weakens the signal, so each glove retries more, and the two compete for airtime. At 20 packets/s per glove, the same test gave 2 gaps in 60 s (measured). The cost is about 30 ms more delay per punch.

**Receiving** (`glove_receiver.py`). A thread per port blocks on `recvfrom`, parses every valid frame (sync bytes and checksum; junk bytes are skipped one at a time), and converts counts to g and °/s.

**Clock.** The glove's microsecond counter is put on the laptop clock by `Clock`: the counter is unwrapped (it rolls over every 71 min), and the offset is the smallest (arrival time − glove time) seen over the last 500 samples. The packet with the least network delay therefore sets the offset, which keeps the timestamps smooth despite packets arriving in bursts. This is what lets glove samples and camera frames be matched in time.

`LiveSensors` (`punch_session.py`) keeps the last 30 s of samples, runs the segmenter on every sample, logs the live gravity estimate, and reports "no glove data" when no sample has arrived for 0.5 s.

## 5. Camera and body states (`pose_tracker.py`)

OpenCV reads webcam frames (about 27 fps measured); MediaPipe's pose landmarker returns 33 body landmarks per frame, both in image coordinates and as 3D "world" coordinates in metres (origin between the hips, y pointing down). Both are smoothed with exponential moving averages (α = 0.6 image, 0.7 world). MediaPipe takes about 29 ms per frame (measured). The picture is mirrored for display.

**Calibration.** Pressing C (camera window or the page) or F7 (anywhere) starts a 5 s countdown; at the end the nose position and shoulder width are stored as the boxer's guard. The delay is a design rule (`CALIBRATE_DELAY_S`) so the boxer can get into guard or click back into the game.

**Body states**, computed every frame:

| State | Rule |
|---|---|
| DUCK | Nose more than 0.45 shoulder widths below the calibrated position |
| SLIP LEFT / RIGHT | Nose more than 0.5 shoulder widths sideways from the calibrated position |
| BLOCK | Both wrists within 1.0 shoulder width of the nose |
| GUARD DOWN | Neither wrist near the nose **and** both wrists at least 0.15 shoulder widths below their elbows (arms hanging). The second condition stops a punch being loaded up from counting. |

Duck and slips need calibration; block and guard down do not. Each active state holds its key down until the state ends (`game_input.GameInput.set_states`).

## 6. Finding a punch (`punch_segment.py`)

The same `Segmenter` runs live and on recordings, so the model trains on exactly the windows it sees in play.

**Gravity.** Acceleration includes gravity, which must be removed to see the glove's own motion. While the glove is still (|a| within 0.1 g of 1 g and rotation below 30 °/s) gravity is averaged in; while it moves, the stored gravity vector is rotated by the gyroscope reading. Linear acceleration is `accel − gravity`.

**Start.** A punch starts when linear acceleration reaches `start_g`, provided the segmenter is ready (it has seen the glove still) and the rearm time after the previous punch has passed.

**End.** Whichever comes first:
- **Timeout:** `timeout_s` after the start.
- **Deceleration:** the arm braked (linear acceleration of at least `decel_g` against the direction of travel, once the estimated speed has reached `min_peak_speed`) and then settled (acceleration below `settle_g` for `settle_s`).

The settle condition exists because hooks and uppercuts reverse direction mid-punch, and a reversal also looks like hard deceleration. A reversal keeps accelerating, so it never settles; a real stop does. Speed is estimated by integrating acceleration (rotated with the glove), but it drifts by about 1 to 1.5 m/s after a jab (measured), so it is used only for direction and early peak, never to decide that the arm stopped. A gap in the data longer than `max_gap_s` ends a punch with reason "gap", which is discarded.

**Settings** (`punch_config.json`; a trained model stores the settings it was trained with and uses those):

| Setting | Value | Meaning |
|---|---|---|
| `start_g` | 1.5 g | Linear acceleration that starts a punch |
| `timeout_s` | 0.35 s | Longest punch |
| `decel_g` | 3.0 g | Braking that can end a punch |
| `settle_g`, `settle_s` | 1.5 g, 0.02 s | How quiet, for how long, after braking |
| `min_peak_speed` | 1.5 m/s | Speed reached before braking counts |
| `rearm_s` | 0.4 s | Dead time after a punch |
| `pre_s` | 0.25 s | Data kept before the start, for features |
| `still_g`, `still_dps` | 0.1 g, 30 °/s | What counts as still |
| `max_gap_s` | 0.1 s | Longest data gap inside a punch |
| `min_confidence` | 0.6 | How sure the model must be to press a key (not stored in the model) |

Changing `start_g` by a small amount works without retraining; the timeout always needs a retrain.

## 7. Features (`punch_features.py`)

Each punch becomes 132 numbers: 89 from the glove and 43 from the camera.

**Glove (89).** For each axis: linear acceleration and rotation rate, summarised as statistics (mean, extremes and spread) over the whole punch and over its first, middle and last thirds; mean acceleration and rotation in the 0.25 s before the start; degrees turned; and the glove's tilt before the punch (the gravity direction). Plus: duration, whether it ended by deceleration, peak g, peak speed, vertical movement before the start, the dip before an upward move (uppercut), horizontal and vertical movement measured against gravity, overall size of motion and rotation, and the fraction of samples at the sensor's range limit. Gravity is turned with the glove through the punch, so "vertical" stays vertical while the wrist rotates.

**Camera (43).** From the world landmarks of the punching arm, for frames in the punch window: the wrist's position relative to the shoulder along the body's right, up and forward directions (in shoulder widths), the elbow angle and the shoulder angle. Each of these five series gives first, last, minimum, maximum, change, and when the minimum and maximum occurred. Plus shape features for each punch type (out-to-the-left and back-across for a hook; dip then rise for an uppercut), peak wrist speed, whether the wrist started below the elbow, wrist visibility and the number of frames. For the right arm the sideways direction is mirrored so "right" means "toward the body's middle" for both arms. A front camera barely sees a straight punch, so jabs rely mostly on the glove.

The ESP32-S3 glove adds magnetometer features (`imu_mag_*`); the C3 gloves have no magnetometer.

## 8. Recording training data (`punch_capture.py`)

**Prompted mode** (default). The screen calls a punch with a beep, in random order. The strongest punch found in the next 1.6 s gets that label. Every 15 prompts a 15 s "MOVE, DON'T PUNCH" round runs; anything the segmenter catches outside a prompt is saved as **NONE**, so the model learns to press nothing for guard adjustments and feints. When the punches are done and NONE is still short of the target, up to six extra MOVE rounds run. A session does not start until a name is entered, the sensors are sending and calibration is done; "." starts and pauses it, and it pauses itself if a sensor goes quiet for 1 s.

**Both arms** (`--both`). Prompts are combos, one punch per arm ("LEFT HOOK, then RIGHT UPPERCUT"). Exactly half the combos lead with each arm, shuffled, so neither model learns "my punch comes first". Each punch is labelled from its own arm's sensor and saved to its own arm's folder.

**Storage.** One `.npz` file per example: the raw glove samples and pose frames around the punch (from 0.25 s before to after the end), plus metadata (label, arm, session, person, the segment settings, and the live gravity estimate at the start). Raw data is stored, not features, so features and settings can change without recording again. Rejected examples (no punch found, or too little data) go to `_rejected/`. The "enough data" check scales with the sensor's measured rate, so a slower sensor is not mistaken for packet loss.

**Sessions** (`punch_sessions.py`). Each run is a session named by its start time. Whole sessions, or one arm's part (`--arm`), can be deleted to `_deleted/` and restored. When a session ends, every arm it recorded is retrained, keeping the previous model as `.bak`.

## 9. Training (`punch_train.py`)

Each recording is segmented again with the current settings (starting from its stored gravity, because NONE examples often begin mid-movement), turned into features, and used to train a random forest (scikit-learn, 300 trees, balanced class weights). NONE is included once there are at least 5 examples.

Two accuracy figures are reported, for glove-only, camera-only and combined features:
- **Cross-validated** (stratified folds): optimistic, because examples from the same session are in both training and test folds.
- **New session** (leave one session out): closer to play, because the boxer straps the glove on again. It is misleading when one class exists in only one session.

It also reports how often the model is sure enough to press a key and how often it is right when it does.

**Current models** (measured, 2026-11-08, C3 gloves, combined features):

| Arm | Examples | Cross-validated | New session | Presses a key for | Right when it presses |
|---|---|---|---|---|---|
| Left | 20 each of JAB, HOOK, UPPERCUT; 21 NONE (one session) | 94% | not available (one session) | 83% of punches | 99% |
| Right | 40 each of CROSS, HOOK, UPPERCUT; 50 NONE (four sessions) | 94% | 84% | 84% of punches | 99% |

## 10. Live play (`punch_live.py`, `punch_live_both.py`)

For each punch the segmenter ends, the program waits for a camera frame from after the punch (or at most 0.1 s), extracts the 132 features from the stored buffers and asks the model for class probabilities. The decision:

- NONE: no key.
- Best class below `min_confidence`: "unsure", no key. This exists because forcing every movement into a punch class presses wrong keys.
- Otherwise: the punch's key is tapped (held for `hold_ms`, 50 ms by default).

The decision usually takes 8 to 50 ms after the punch ends, and occasionally up to about 175 ms (measured). A key press therefore comes about 0.4 s after a punch starts when the punch runs to the timeout, and sooner when the arm stops earlier.

**Keys** (`game_input.py`). Keys are sent with Win32 `SendInput` using scan codes, so games that read hardware scan codes receive them. All bindings live in one file, `mappings/controls.json`:
- `normal`: six punch keys, one per punch per arm (left JAB, HOOK, UPPERCUT; right CROSS, RIGHT HOOK, RIGHT UPPERCUT), so a game such as Street Fighter can bind each to its own button.
- `defence`: one key per body state; an empty key means the state presses nothing (in Street Fighter, BLOCK is left empty because the game blocks by holding back).
- `tracking`: punch bindings used while the glove button is held (mouse clicks by default).

The file is checked every second and reloaded when it changes, so edits apply during play. A game's ready-made layout (`mappings/presets/<game>.json`) is copied into it on request, never used in its place, so what the Controls page shows is what gets pressed.

**Hotkeys** are read with `GetAsyncKeyState`, so they work while the game has focus: F7 calibrate, F8 key output on/off, F9 quit, [ and ] confidence cutoff down/up. Started from a terminal with `--live`, key output starts off until F8; started from the page with "Press keys for real", it starts on (`--keys-on`), because the page has already asked.

**Tracking mode** (`hand_aim.py`). Holding the left glove's button turns the right wrist (from the camera) into a mouse joystick and switches punches to the tracking bindings. The camera's BLOCK is off while tracking, because the steering hand sits near the face.

**Corner popup** (`popup_window.py`, `--popup`). The camera window is restyled with Win32 calls into a 360 × 270 px window in a screen corner: always on top, never takes keyboard focus, click-through, not in the taskbar. It shows each arm's newest punch in large letters, NO DATA for a silent glove, and a border colour (green keys on, white keys off, red no glove data). Games must run windowed or borderless, because an exclusive full-screen game covers it.

## 11. Control panel (`app_server.py`, `website/`)

`python app_server.py` serves the Keyboard Warrior page at `http://127.0.0.1:8770/` and is the only program a demo needs. The page cannot start programs itself, so the server does, from a fixed list (camera check, glove check, record, train, play, sessions, key editor), each with checked options. One tool runs at a time, because the webcam and the glove ports can only be used by one program. The tool's output is shown in the page; typed commands (such as `x` to undo a recording) are written to its standard input.

**Camera in the page** (`web_view.py`). For camera tools the server sets `BOXING_WEB_VIEW` to a free local port. `pose_tracker.main` then, instead of opening an OpenCV window, renders each finished frame (with all its text) at 960 × 720, encodes it as JPEG (quality 75) and serves it on that port as a multipart stream; it skips encoding when nobody has watched for 2 s. Keys pressed on the picture in the page are posted back and queued as if pressed in the window. The server relays both (`/api/camera`, `/api/camera/key`), so the page only ever talks to its own origin. The tool still owns the webcam. With the corner popup on, the tool also keeps its window (`keep_window`); `overlay.finish_layer(..., keep=True)` lets one frame's text be drawn at two sizes.

**Safety.** The server listens on 127.0.0.1 only; it refuses requests whose Host or Origin header is not the page itself (blocking other websites and DNS rebinding), accepts only JSON POSTs, limits request size, validates every option, and serves the page with a Content-Security-Policy. Real key presses require the "Press keys for real" tick and a confirmation. Stopping a tool ends its whole process tree (`taskkill /T`), so windows it opened are closed too.

**Text rendering** (`overlay.py`, `hud.py`). Text is not drawn on the small camera picture and then enlarged (that blurred it). It is held as a list and drawn after the picture is scaled to its real display size, using a mask widened with `cv2.dilate` for thickness and a black outline, because OpenCV 5's `putText` caps stroke width at about 2 px.

## 12. Testing

`python -m unittest discover tests` runs 185 tests in about 75 s, with no hardware: `punch_sim.py` generates glove data that follows the punch definitions, servers run on free local ports, stand-in programs replace the camera tools, and temporary files replace the controls file. The tests cover frame parsing, the clock, segmentation, features, training end to end, capture (prompts, combos, NONE, pausing, undo), sessions, key output and controls, body states, the popup's drawing, the control panel's API and its camera relay. Simulated data shows the code runs; it does not show the model works on real punches.

Hardware checks:
- `glove_check.py --port 5005 --until 2`: hears the glove, then reports rate, loss and gaps.
- `usb_glove_check.py --port COMn`: the same over USB, plus gravity at rest and gyro drift.
- Each board's USB serial status line (section 3).

## 13. Known limits

- Each model learns one person and one sensor mounting. A board that sits at a different angle produces different numbers on the same axes, so moving it means recording again. The left arm currently has one session, so its 94% is likely optimistic.
- A key can only be pressed after the punch ends: up to the 0.35 s timeout plus the decision time.
- The front camera sees straight punches poorly.
- Two gloves still share one WiFi channel. Weak batteries, distance and the boxer's body all reduce the margin; the laptop's single radio also serves its own internet connection.
- The corner popup and key output are Windows only.
- On many laptops F7 to F9 need the Fn key.
