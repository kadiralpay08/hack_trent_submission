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

Record **100 jabs, 100 left hooks and 100 left uppercuts**. For each one: press and hold the button on the glove, punch, then release. One press is one labelled block.

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
| Low-confidence predictions press no key | The model only knows three classes, so other movement must not be forced into one of them |
| Separate live script, `controller.py` left unchanged | Keeps Kadir's rule-based controller as a demo fallback |

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
| `tests/test_punch_pipeline.py` | Run with `python -m unittest discover tests` |

**Status (2026-11-07):** all of the above is built and tested on simulated data. The firmware compiles but hasn't been flashed. Nothing has been tested on real punches yet.

**Shared parts:** `glove_receiver.py`, `pose_tracker.py`, `game_input.py`, `firmware/glove_imu/`.

**Earlier work, kept and not the main path:** `controller.py`, `glove_punch.py`, `punch_detector.py`, `tune_punches.py`, `replay_punches.py` (Kadir's camera-rules controller), `glove_check.py` (still the first thing to run if the glove seems broken), `punch_tracker.py`, `firmware/punch_stream/`, `firmware/sketch_nov6a/` (wired Uno experiments).

## Setup notes

- **Python:** use a virtual environment: `python -m venv .venv`, then `pip install -r requirements.txt`. On Andre's laptop, Python 3.12 works. A team memory says another dev machine needs Python 3.14, because its default Python (an MSYS2 build) can't install OpenCV or MediaPipe.
- **Firmware:** board "AI Thinker ESP32-CAM" with ESP32 core 3.x. Copy `config.example.h` to `config.h` and fill in the hotspot details.

## Limits to keep in mind

- The model learns one person's punches and one sensor mounting. Changing either probably means collecting more data.
- A key press can't happen before the punch ends, so a longer timeout means more delay in the game.
- A front camera is the weak view for jabs, as noted above.
