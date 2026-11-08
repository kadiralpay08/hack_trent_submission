# Project brief: read this before changing anything

The team's agreed plan. Every agent and teammate works from it. If your task conflicts with it, stop and ask rather than quietly changing direction. If the team changes a decision, update this file in the same pull request.

## Before you change anything

1. **Check for new work:** run `git fetch origin`, then `git log --oneline HEAD..origin/main` and `git branch -r`. Read anything that touches your files. Teammates merge often, so a file list from earlier in a session can be out of date.
2. **Check the Brain:** run `entire brain brief "<task>" --json` and `entire brain recall "<question>" --json` for earlier decisions and known problems.
3. **Check the code:** follow `.entire/agent-guide.md` and use Graph to find what depends on the code you'll change.
4. **Branch from an up-to-date `main`:** name it `yourname/what-youre-building`. Never commit straight to `main`. It is protected, so work reaches it through a pull request, and Kadir must explicitly approve a merge to `main`. Before any push or merge, say what the source and target are.
5. **Agree who owns a file** before editing a teammate's file.
6. **Record what you learn:** use `entire brain remember "<memory>" --path <category.subcategory.type> --kind <decision|gotcha|...> --json` for anything durable the code doesn't state, and update this file if the plan changes.

Claude Code also gets an automatic notice of new `main` commits and open branches with each prompt, from `.claude/hooks/sync_check.sh`. Other agents must run step 1 themselves.

## What we're building

A left-hand boxing controller. A motion sensor on the glove and a webcam watch a punch, a model decides whether it was a jab, hook or uppercut, and the laptop presses a key to control a boxing game.

## The pipeline

1. **Glove:** an ESP32-CAM reads an MPU6050 and streams raw data over WiFi using UDP, at 500 Hz.
2. **Network:** the gloves and laptop share a 2.4 GHz hotspot. Since 2026-11-08 it is the laptop's own Windows Mobile Hotspot, where the laptop is always `192.168.137.1`: set that as `LAPTOP_IP` in `config.h`, and allow Python through the Windows firewall. Turn off the hotspot's **Power saving** setting, or Windows switches it off whenever no glove has been connected for a few minutes.
3. **Camera and sync:** the laptop runs pose estimation on webcam frames (OpenCV captures, MediaPipe estimates the pose). IMU samples and frames are both timestamped on the laptop clock so they can be matched up.
4. **Finding a punch:** a punch starts on high acceleration. It ends at a selectable timeout or on high deceleration, whichever comes first. `punch_tune.py` sets the timeout and shows starts and ends live on the pose display. Its timed test (`test 10 60 jab`) counts and classifies the punches thrown in one minute, at the current timeout and at several others for comparison.
5. **Classifying:** a random forest uses features from the IMU and from joint angles. Only if that isn't good enough, try a small neural network on the raw sensor data (Google Colab or Gemini tools are options, undecided).
6. **Output:** each punch becomes a key press. Defence keys from the camera stay on.

## What each punch looks like

Left hand only. Features and data collection depend on these definitions:

| Punch | Motion | Key |
|---|---|---|
| Jab (left) | Straight forward | `J` |
| Left hook | Left, then forward, then right | `K` |
| Uppercut (left) | Arm moves down, then up | `L` |
| Cross (right) | Straight forward | `U` |
| Right hook | Right, then forward, then left | `I` |
| Uppercut (right) | Arm moves down, then up | `O` |

The six punches have six different keys, one for each of a fighting game's three punch and three kick buttons, so each arm's punches can be mapped in the game (the left arm's three, and the right arm's three). The right arm's hook and uppercut are `RIGHT HOOK` and `RIGHT UPPERCUT` in the controls file.

Defence keys: block `W`, duck `S`, slip left `A`, slip right `D`, guard down (both hands dropped) `G`. These are in `mappings/controls.json`, editable with `controls_editor.py`.

## Training data

Record **100 jabs, 100 left hooks and 100 left uppercuts** with `punch_capture.py`. By default the screen calls each punch with a beep, in random order. The strongest punch in the next 1.6 s gets that label. Movement caught between prompts, plus a "MOVE, DON'T PUNCH" round every 15 prompts, is saved as **NONE** (not a punch), so the model learns to press nothing for guard adjustments and feints. The original button mode is still available as `--button`: hold, punch, release.

**Both arms in one session (2026-11-07):** `punch_capture.py --both` (with `--right-phone ADDRESS` for a phone) calls combos, one punch per arm, such as "LEFT HOOK, then RIGHT UPPERCUT". Half the combos start with each arm, shuffled. Each punch is saved to its own arm's folder from its own sensor, so the arms still train separately. NONE is collected for both arms, with extra MOVE rounds at the end if an arm is short. Every capture session now retrains each arm it recorded when it ends (the old model kept as `.bak`; `--no-train` skips it), so one session goes from nothing to trained models.

## Decisions

| Decision | Why |
|---|---|
| Random forest first, neural network only if needed | Works with 300 examples and is quick to test |
| Always store raw data, then compute features afterwards | Lets us change the timeout and features without recording again |
| The same punch-finding code runs in training and live | The button only sets the label and time range, because no button exists during play |
| Webcam straight in front of the boxer | Team choice, and easy to set up. A front camera barely sees a straight punch, so jabs rely mostly on the glove. Training reports glove-only, camera-only and combined accuracy |
| Button on GPIO12, wired to GND | GND wiring can't pull this boot-sensitive pin high. **Never connect GPIO12 to 3V3**, or the board won't boot |
| Button state travels in the frame's second sync byte: `A5 5A` released, `A5 5B` pressed | Every frame carries it, so a lost packet can't lose a press, and firmware without a button still works |
| Deceleration only ends a punch once the arm settles | Uppercuts and hooks reverse direction mid-punch, and a reversal also looks like high deceleration |
| Calibration waits 5 seconds after C or F7, with an on-screen countdown | Design rule: gives the boxer time to get into guard, or to click back into the game, before the stance is recorded. Applies to every tool built on `pose_tracker.py`, through `CALIBRATE_DELAY_S` |
| Training is prompted by a timer, not the button (2026-11-07) | Holding a button changes your grip and fist compared with play, and the timer naturally collects not-a-punch examples. The button stays as `--button` |
| A fourth class, NONE, for movement that isn't a punch | Anything the punch finder catches reaches the model in play. Without NONE, every guard adjustment would become a jab, hook or uppercut |
| Sensitivity is adjustable without retraining | The confidence cutoff comes from `punch_config.json` or `--confidence`, not the model, and the [ and ] keys change it while playing (not F5/F6, which reload a browser game). `--start-g` moves the punch threshold, but only small changes are safe; big ones need a retrain. The timeout always needs a retrain |
| Low-confidence predictions press no key | The model only knows three classes, so other movement must not be forced into one of them |
| A second arm is optional and fully separate (2026-11-07) | The right glove uses port 5006, its own data folder, model and punches (CROSS, HOOK, UPPERCUT), selected with `--arm right`. The left arm keeps its original data and model, and stays the default. Camera features are mirrored for the right arm |
| The glove sends to one laptop's address, not broadcast (2026-11-07) | Broadcast over the phone hotspot lost about a third of the samples, in gaps of up to about 350 ms, enough to swallow a whole punch. Sending to one address gets WiFi's automatic re-sends. To use another computer, flash its address |
| Tracking mode on the glove button (2026-11-07) | Holding the button turns the right hand into a mouse joystick (camera, right wrist relative to the right shoulder, from where it was at the press), and switches punches to a tracking set of binds (default: mouse clicks, held 150 ms). The camera's BLOCK is off while tracking, because the steering hand sits near the face; duck and slip stay on. Speed and position steering are both available. Binds live in `mappings/controls.json` (normal, tracking and defence sets) and are edited with `controls_editor.py`; `browser_boxing.json` keeps the older format, which still loads |
| Recordings save the live gravity estimate (2026-11-07) | NONE examples happen while moving, so the saved slice never shows the glove still, and a fresh punch finder (which waits for stillness) found nothing: every NONE was rejected. Capture now saves `start_gravity`, and capture and training start the punch finder from it |
| Two-arm training records combos, balanced on which arm goes first (2026-11-07) | In play the arms punch in sequence, and each arm's sensor feels the body turn from the other's punch. Recording combos teaches each model that movement, and catches it as NONE between prompts. Balancing which arm leads stops a model learning "my punch always comes first". Each arm is still labelled from its own sensor and trained on its own |
| Capture trains when it ends (2026-11-07) | One session takes you from nothing to working models. The previous model is kept as `.bak`, so a bad session can be undone by copying it back |
| The camera windows have one clean look, in `hud.py` (2026-11-08) | The trainer, the timeout tuner and the live recognition overlay used OpenCV's thin vector font and loose text lines. Text now uses a real font (Segoe UI, with fallbacks) through `overlay.py`, and every screen is built from the same soft panels: one main card (what is happening and what to do), a status row, a progress panel, a message and a key hint, in calm colours that each mean one thing (green good, amber wait, red problem, blue information). `hud.py` only draws. The recording, training and recognition logic did not change, and the old font is still the fallback if no font is found. Left arm is blue and right arm amber on the skeleton |
| Camera text is drawn at the window's real size (2026-11-07) | Text drawn on the small camera picture and then stretched to full screen was blurry. `overlay.py` holds it back and draws it after the picture is enlarged. Text is white or coloured with a black outline, never grey, which was hard to read |
| Recordings from the old sensors were set aside when the C3 gloves arrived (2026-11-08) | The model reads movement in the sensor's own axes, and the glove's tilt in guard, so a different board or mounting looks like different punches: the left model called almost every C3 punch an unsure JAB. Every session recorded with the ESP32-CAM glove or the phone was moved to `_deleted` (bring them back with `punch_sessions.py --restore`), and both arms train on C3 recordings only. Mount each board in the same place and rotation every session |
| The C3 gloves send 25 frames per packet, at 15 dBm (2026-11-08) | Measured while boxing: one glove alone delivered everything, but with both on, the moving glove went silent 32 times for over 0.5 s in 45 s (33 s silent in all), and the live programs showed NO GLOVE DATA most of the time. Two gloves share one WiFi channel, and a weak signal from a moving arm makes each resend more. 20 larger packets a second instead of 50 cut that to 2 gaps in 60 s, for about 30 ms more delay. 8.5 dBm also dropped up to 0.7 s at a time, hence 15 dBm |
| Camera tools show in the page when the panel starts them (2026-11-08) | For the demo, one program: `python app_server.py`. When it starts recording, the camera check or play, it sets `BOXING_WEB_VIEW` to a free port. `pose_tracker.main` then sends each finished picture (all its text and cards) to `web_view.py` as JPEGs instead of `cv2.imshow`, and takes keys from it instead of `cv2.waitKey`. The panel relays the picture to the page (`/api/camera`) and the page's keys back (`/api/camera/key`): click the picture, then C calibrates (still with the 5 s countdown), the name is typed there, and `.` starts and pauses. The tool still owns the webcam, so the one-program rule holds, and nothing about finding or classifying punches changed. Run from a terminal, every tool opens its own window as before. F7, F8, F9, `.`, [ and ] are global hotkeys, so they work while a game has focus. Play's "Corner popup over the game" (on by default) also keeps the small always-on-top corner window (`--popup`, `keep_window`), because the page is hidden behind the game while playing. Stop ends the tool and everything it opened (taskkill /T), so no keybind window is left behind |
| GUARD DOWN needs both arms really dropped (2026-11-08) | Bound to jump in Street Fighter, it fired whenever both hands left the face, such as while loading up a punch. It now also needs each wrist below its elbow (`GUARD_DROP` in `pose_tracker.py`) |
| Street Fighter has its own controls file (2026-11-08) | `mappings/street_fighter.json`: guard down = W (jump), block unbound (the game blocks by holding back, and W in guard would keep jumping). Use `--mapping mappings/street_fighter.json`; the shared `controls.json` keeps the team defaults |
| `punch_live_both.py --popup` (2026-11-08) | A small always-on-top corner window shows each arm's newest punch in big letters, NO DATA, and a border colour (green keys on, white off, red a glove is silent), so the game stays in front. Calibrate with F7 there, since the popup can't be clicked; on a laptop F7 may need Fn |
| Separate live script, `controller.py` left unchanged | Keeps Kadir's rule-based controller as a demo fallback |
| A control panel web page that only launches the existing tools (2026-11-08) | `python app_server.py` serves `website/index.html`, called Keyboard Warrior, with five pages: Play (the front page: pick Street Fighter or another game and start), Train (record, train and manage recordings), Setup (guide, glove check, camera calibration), Controls (key mapping) and Help. Old links like `#/record` still work. The look is calm and retro: a pixel-art city behind the page and in the header, two small boxers on the street, one amber accent, no flashing. All reading happens on a solid board so text never sits on artwork. Arcade (dusk), Light (day) and High contrast (no artwork) looks. It runs the same `punch_*.py` programs a person would type, so nothing about how punches are found or classified changes, and every tool still works from a terminal. One tool runs at a time because the webcam and the glove's port can only be used by one program. Since 2026-11-08 the camera tools show their picture inside the page instead of in an OpenCV window, so a demo needs only `python app_server.py`: see the next row. Accessibility and function come first: keyboard use, screen reader labels, large text, high contrast, no colour-only meaning |
| Games open from the page through a saved shortcut (2026-11-08) | A web page cannot start programs, so `app_server.py` does it with `game_launcher.py`. Each game on the page has ONE shortcut, saved in `mappings/launchers.json` (git-ignored, because paths differ per computer): a Steam game (opened as `steam://rungameid/<id>`), a launcher link (Epic), a program or shortcut file (.exe, .lnk, .url only, never scripts), or a web address. "Find it on this computer" looks through Steam libraries, Epic and Start Menu and Desktop shortcuts. Only a saved shortcut can be opened, and it is checked again when launched |
| Games with their own keys get a layout file, not a change to `controls.json` (2026-11-08) | `mappings/presets/<game>.json` is passed to `punch_live.py` with `--mapping`, so the six-key layout the team agreed stays as it is. Mike Tyson's Punch-Out!! (browser game on RetroVault) uses X and Z for the two punches, Enter for the star uppercut, the arrow keys to dodge and duck, and hands up holding Up for head shots. Its page names the keys but not "arrow", so those four are read as the arrow keys: check them in the game |
| The panel is local only and starts only a fixed list of tools (2026-11-08) | It can start programs and press keys, so it listens on 127.0.0.1, refuses a foreign Host or Origin, takes only JSON, and checks every option before building a command. Pressing real keys needs the "Press keys for real" tick and a confirmation |
| A phone can be the right arm's sensor instead of a glove (2026-11-07) | The phyphox app serves the phone's accelerometer and gyroscope and the laptop polls it (`phyphox_imu.py`). It is chosen with `--phone ADDRESS` together with `--arm right` in `punch_capture.py`, `punch_tune.py` and `punch_live.py`, or `--right-phone` in `punch_live_both.py`, and is refused for the left arm so the left glove's recordings and model can't be mixed with it. Polling means nothing has to connect into the laptop: in testing, a phone's connection into the laptop never arrived (from a teammate's hotspot or an iPhone's own), while the laptop reaching the phone worked. The right arm's recordings, model and punches stay as for the right glove. A phone has no magnetometer and reports about 80 to 100 Hz against a glove's 500 Hz. Capture's "enough data" check used to assume 500 Hz and rejected every phone recording as dropped packets (shown as MISSED); it now scales with the sensor's measured rate (`LiveSensors.sample_rate_hz`). A browser-page alternative (`phone_imu.py`) is kept but did not work in that test |

## Code map

**New pipeline, the main path:**

| File | Purpose |
|---|---|
| `punch_segment.py` | Finds where a punch starts and ends in IMU data |
| `punch_features.py` | Turns one punch into random forest features |
| `punch_session.py` | Shared live IMU and pose buffers, settings, and saving and loading blocks |
| `hud.py` | How the camera windows look: soft panels, pills, bars and the standard layouts used by the trainer, the tuner and the live overlay. Drawing only, no logic. `tests/test_hud.py` renders every screen (`render_all`) to look at when changing the design |
| `punch_tune.py` | Type a timeout and watch punch starts and ends on the pose display |
| `punch_capture.py` | Records labelled training blocks using the glove button |
| `punch_train.py` | Trains and evaluates the random forest |
| `punch_live.py` | Classifies punches live and presses keys |
| `punch_sessions.py` | Lists recording sessions by person and arm; deletes (recoverably) or restores whole sessions, then retrains |
| `punch_config.json` | Shared punch-finding settings |
| `punch_sim.py` | Fake glove data that follows the punch definitions. Used by `--simulate` and the tests. It shows the code runs, not that the model works on real punches |
| `hand_aim.py` | Tracking mode: right-hand steering of the mouse while the glove button is held |
| `controls_editor.py` | Keybind window for `mappings/controls.json` |
| `punch_live_both.py` | The final program: runs the left and right models side by side with one camera, opens the keybind window, and has tracking mode on the left glove's button |
| `app_server.py` | The server behind Keyboard Warrior, the control panel page: `python app_server.py`, then http://127.0.0.1:8770/. Starts and stops the tools above, shows the camera tools' picture in the page, lists and deletes recording sessions, saves `mappings/controls.json` |
| `web_view.py` | Lets a camera tool started by the panel show its picture in the page and take its keys from it, instead of an OpenCV window (switched on by `BOXING_WEB_VIEW`) |
| `website/index.html`, `website/ui/` | The control panel page (`ui.js`, `ui.css`) and its list of games (`games.json`, where a new game's setup steps go) |
| `tests/test_punch_pipeline.py` | Run with `python -m unittest discover tests` |
| `tests/test_app_server.py` | The panel's server: who may ask, what may run, key mapping rules |
| `game_launcher.py` | Finds, saves and opens a game's shortcut (Steam, Epic, program file, web address). `mappings/presets/` holds per-game key layouts (Punch-Out). `website/ui/art/` holds the pixel-art backgrounds, made by `make_art.py` there |
| `tests/test_game_launcher.py` | Shortcut checks, launching (with the operating system calls replaced), and the Punch-Out layout |

**Status (2026-11-07):** all of the above is built and tested on simulated data. The firmware compiles but hasn't been flashed. Nothing has been tested on real punches yet.

**Gloves from 2026-11-08:** two ESP32-C3 boards, each with an MPU-6500 (sold as an MPU-9250; it has no magnetometer) on SDA GPIO1 and SCL GPIO3, run `firmware/glove_imu_c3/`: the same frames, ranges and 500 Hz as the ESP32-CAM glove. One sketch serves both arms: left (port 5005) by default, right (port 5006) when built with `--build-property "compiler.cpp.extra_flags=-DGLOVE_UDP_PORT=5006"`. Both were flashed and checked over WiFi with `glove_check.py` (500 Hz, 0% lost). `firmware/glove_imu_c3_usb/` sends the same frames over USB instead, checked with `usb_glove_check.py`. On three NiMH cells a board sometimes dropped off WiFi, so use four cells or a USB power bank. The first C3 session (`20261108_033303`, 6 to 11 of each punch per arm) is recorded and both models are trained on it alone, so more sessions are needed.

**Right arm from a phone:** `phyphox_imu.py` polls the phyphox app and plugs into `LiveSensors` through `--phone` (with `--arm right`). The receiver has run on a real phone (about 80 to 100 Hz). 60 right-arm punches (20 each of CROSS, HOOK and UPPERCUT) were recorded with it, and `models/punch_rf_right.joblib` was trained on them: 98% cross-validated, but from one session, so optimistic. It has no NONE (not a punch) examples yet and has not been tried live. `phone_imu.py` with `phone/index.html` is a browser-page alternative that did not work (see the decision above).

**Shared parts:** `glove_receiver.py`, `pose_tracker.py`, `game_input.py`, `firmware/glove_imu/` (the left glove, ESP32-CAM). `firmware/glove_imu_s3/` is an ESP32-S3 glove for either arm, and `firmware/mpu6050_usb_check/` and `firmware/mpu9250_usb_check/` check whether a sensor is alive over USB.

**Kadir's game and launcher:** `website/sparring.html` (with `website/js/` and `website/css/`; it used to be `index.html`, now the control panel) is a webcam sparring game with arcade sound (`website/js/audio.js`: a punch sound per move, a beat, READY and FIGHT!), and `keybridge.py` serves it and turns its punches into real key presses through `game_input.py`. `play.py` and `variants/` select product versions; the 45 degree camera is shelved in `archive/angled_camera/`.

**Earlier work, kept and not the main path:** `controller.py`, `glove_punch.py`, `punch_detector.py`, `tune_punches.py`, `replay_punches.py` (Kadir's camera-rules controller), `glove_check.py` (still the first thing to run if the glove seems broken), `punch_tracker.py`, `firmware/punch_stream/`, `firmware/sketch_nov6a/` (wired Uno experiments).

## Setup notes

- **Python:** use a virtual environment: `python -m venv .venv`, then `pip install -r requirements.txt`. On Andre's laptop, Python 3.12 works. A team memory says another dev machine needs Python 3.14, because its default Python (an MSYS2 build) can't install OpenCV or MediaPipe.
- **Firmware:** board "AI Thinker ESP32-CAM" with ESP32 core 3.x. Copy `config.example.h` to `config.h` and fill in the hotspot details.

## Limits to keep in mind

- The model learns one person's punches and one sensor mounting. Changing either probably means collecting more data.
- A key press can't happen before the punch ends, so a longer timeout means more delay in the game.
- A front camera is the weak view for jabs, as noted above.
