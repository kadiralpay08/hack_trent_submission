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
2. **Network:** the ESP32 and laptop share a phone hotspot (2.4 GHz). Set `LAPTOP_IP` in `config.h` to the laptop's address on the hotspot, and allow Python through the Windows firewall.
3. **Camera and sync:** the laptop runs pose estimation on webcam frames (OpenCV captures, MediaPipe estimates the pose). IMU samples and frames are both timestamped on the laptop clock so they can be matched up.
4. **Finding a punch:** a punch starts on high acceleration. It ends at a selectable timeout or on high deceleration, whichever comes first. `punch_tune.py` sets the timeout and shows starts and ends live on the pose display. Its timed test (`test 10 60 jab`) counts and classifies the punches thrown in one minute, at the current timeout and at several others for comparison.
5. **Classifying:** a random forest uses features from the IMU and from joint angles. Only if that isn't good enough, try a small neural network on the raw sensor data (Google Colab or Gemini tools are options, undecided).
6. **Output:** each punch becomes a key press. Defence keys from the camera stay on.

## What each punch looks like

Left hand only. Features and data collection depend on these definitions:

| Punch | Motion | Key |
|---|---|---|
| Jab | Straight forward | `J` |
| Left hook | Left, then forward, then right | `H` |
| Uppercut | Arm moves down, then up | `U` |

Defence keys: block `W`, duck `S`, slip left `A`, slip right `D`. These are in `mappings/browser_boxing.json`.

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
| Camera text is drawn at the window's real size (2026-11-07) | Text drawn on the small camera picture and then stretched to full screen was blurry. `overlay.py` holds it back and draws it after the picture is enlarged. Text is white or coloured with a black outline, never grey, which was hard to read |
| Separate live script, `controller.py` left unchanged | Keeps Kadir's rule-based controller as a demo fallback |
| A phone can be the right arm's sensor instead of a glove (2026-11-07) | The phyphox app serves the phone's accelerometer and gyroscope and the laptop polls it (`phyphox_imu.py`). It is chosen with `--phone ADDRESS` together with `--arm right` in `punch_capture.py`, `punch_tune.py` and `punch_live.py`, or `--right-phone` in `punch_live_both.py`, and is refused for the left arm so the left glove's recordings and model can't be mixed with it. Polling means nothing has to connect into the laptop: in testing, a phone's connection into the laptop never arrived (from a teammate's hotspot or an iPhone's own), while the laptop reaching the phone worked. The right arm's recordings, model and punches stay as for the right glove. A phone has no magnetometer and reports about 80 to 100 Hz against a glove's 500 Hz. Capture's "enough data" check used to assume 500 Hz and rejected every phone recording as dropped packets (shown as MISSED); it now scales with the sensor's measured rate (`LiveSensors.sample_rate_hz`). A browser-page alternative (`phone_imu.py`) is kept but did not work in that test |

## Code map

**New pipeline, the main path:**

| File | Purpose |
|---|---|
| `punch_segment.py` | Finds where a punch starts and ends in IMU data |
| `punch_features.py` | Turns one punch into random forest features |
| `punch_session.py` | Shared live IMU and pose buffers, settings, and saving and loading blocks |
| `punch_tune.py` | Type a timeout and watch punch starts and ends on the pose display |
| `punch_capture.py` | Records labelled training blocks using the glove button |
| `punch_train.py` | Trains and evaluates the random forest |
| `punch_live.py` | Classifies punches live and presses keys |
| `punch_config.json` | Shared punch-finding settings |
| `punch_sim.py` | Fake glove data that follows the punch definitions. Used by `--simulate` and the tests. It shows the code runs, not that the model works on real punches |
| `hand_aim.py` | Tracking mode: right-hand steering of the mouse while the glove button is held |
| `controls_editor.py` | Keybind window for `mappings/controls.json` |
| `punch_live_both.py` | Runs the left and right models side by side, with one camera |
| `tests/test_punch_pipeline.py` | Run with `python -m unittest discover tests` |

**Status (2026-11-07):** all of the above is built and tested on simulated data. The firmware compiles but hasn't been flashed. Nothing has been tested on real punches yet.

**Right arm from a phone:** `phyphox_imu.py` polls the phyphox app and plugs into `LiveSensors` through `--phone` (with `--arm right`). The receiver has run on a real phone (about 80 to 100 Hz). 60 right-arm punches (20 each of CROSS, HOOK and UPPERCUT) were recorded with it, and `models/punch_rf_right.joblib` was trained on them: 98% cross-validated, but from one session, so optimistic. It has no NONE (not a punch) examples yet and has not been tried live. `phone_imu.py` with `phone/index.html` is a browser-page alternative that did not work (see the decision above).

**Shared parts:** `glove_receiver.py`, `pose_tracker.py`, `game_input.py`, `firmware/glove_imu/` (the left glove, ESP32-CAM). `firmware/glove_imu_s3/` is an ESP32-S3 glove for either arm, and `firmware/mpu6050_usb_check/` and `firmware/mpu9250_usb_check/` check whether a sensor is alive over USB.

**Kadir's game and launcher:** `website/` is a webcam sparring game with arcade sound (`website/js/audio.js`: a punch sound per move, a beat, READY and FIGHT!), and `keybridge.py` serves it and turns its punches into real key presses through `game_input.py`. `play.py` and `variants/` select product versions; the 45 degree camera is shelved in `archive/angled_camera/`.

**Earlier work, kept and not the main path:** `controller.py`, `glove_punch.py`, `punch_detector.py`, `tune_punches.py`, `replay_punches.py` (Kadir's camera-rules controller), `glove_check.py` (still the first thing to run if the glove seems broken), `punch_tracker.py`, `firmware/punch_stream/`, `firmware/sketch_nov6a/` (wired Uno experiments).

## Setup notes

- **Python:** use a virtual environment: `python -m venv .venv`, then `pip install -r requirements.txt`. On Andre's laptop, Python 3.12 works. A team memory says another dev machine needs Python 3.14, because its default Python (an MSYS2 build) can't install OpenCV or MediaPipe.
- **Firmware:** board "AI Thinker ESP32-CAM" with ESP32 core 3.x. Copy `config.example.h` to `config.h` and fill in the hotspot details.

## Limits to keep in mind

- The model learns one person's punches and one sensor mounting. Changing either probably means collecting more data.
- A key press can't happen before the punch ends, so a longer timeout means more delay in the game.
- A front camera is the weak view for jabs, as noted above.
