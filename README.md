# hack_trent_submission
Hack Trent Boxing Submission

A left-hand boxing controller. A motion sensor on the glove and a webcam watch each punch, a random forest decides whether it was a jab, hook or uppercut, and the laptop presses that punch's key in a boxing game. The team's plan and decisions are in [PROJECT.md](PROJECT.md).

## Keyboard Warrior: the easiest way in

```powershell
python app_server.py        # then open http://127.0.0.1:8770/ (it opens by itself)
```

Five pages: Play (start a game, up front), Train (record, train, manage recordings), Setup (guide, glove check, calibration), Controls (each punch's key) and Help. It starts the same tools described below, so everything here still works from a terminal. It is built to work with a keyboard, a screen reader, large text and high contrast. Kadir's webcam sparring game moved to `website/sparring.html`.

## Main pipeline: glove and camera with a trained model

Every command and option is listed in [COMMANDS.md](COMMANDS.md). Flash the boards from the terminal with `python flash.py cam` (or `s3`, `cam-check`, `s3-check`).

| Step | Command | What it does |
|---|---|---|
| 1. Check the glove | `python glove_check.py`, then `python glove_receiver.py` | Confirms data arrives. The receiver also shows whether the button reads `HELD` |
| 2. Pick the timeout | `python punch_tune.py` | Type a timeout in ms in the terminal and watch each punch's start and end on the camera window. `test 10 60 jab` runs a timed test and compares timeouts. Type `save` to keep the settings |
| 3. Record | `python punch_capture.py --person <name>` | The screen calls JAB, HOOK or UPPERCUT with a beep, in random order. Throw that punch. Records 100 of each, plus NONE (not a punch) examples from movement between prompts. `--target 20` makes a smaller set. `--button` uses the glove button instead. Data stays in `punch_data/` |
| 4. Train | `python punch_train.py` | Trains the random forest and reports glove-only, camera-only and combined accuracy. Saves `models/punch_rf.joblib` |
| 5. Play | `python punch_live.py --live` | left arm J jab, K hook, L uppercut and right arm U cross, I hook, O uppercut (six different keys, editable in `controls_editor.py`), plus W/S/A/D defence keys from the camera. F7 calibrate, F8 key output, F9 quit. **[** and **]** lower or raise the confidence cutoff while playing, with no retraining. `--confidence 0.7` and `--start-g 3` set the cutoff and the punch threshold at startup. `--popup` shows the camera as a small always-on-top window in a screen corner (`--popup-corner`, `--popup-size`) that never takes focus from the game and ignores clicks, so quit with F9. A game in exclusive full screen hides it |

**Tracking mode and keybinds.** Hold the glove button (GPIO12 to GND) and your right hand steers the mouse like a joystick, from where it was when you pressed. Up and down move the view up and down; left and right move it left and right. While the button is held, punches use a second set of binds (by default jab = left click, hook = right click, uppercut = middle click, each held 150 ms), and the camera's block is off so a raised steering hand can't trigger it. Duck and slip still work. `python controls_editor.py` opens a keybind window with three sets: normal (button up), tracking (button held) and defence (camera). Click a bind, then press a key or click a mouse button. The window also chooses the steering style: speed (like a thumbstick) or position (like a trackpad), and how fast. Changes save to `mappings/controls.json` at once, and a running `punch_live.py` picks them up within a second. `--no-tracking` ignores the button.

**Two gloves (optional).** Everything above is the left arm, and it is the default. A second glove on UDP port 5006 is the right arm: add `--arm right` to `punch_tune.py`, `punch_capture.py`, `punch_train.py` and `punch_live.py`. The right arm has its own recordings (`punch_data_right/`), model (`models/punch_rf_right.joblib`) and punches (CROSS = K, HOOK = H, UPPERCUT = U; its hook goes right, forward, left). Its camera features are mirrored, so a hook looks the same to both models. `python punch_live_both.py --live` runs both models side by side with one camera, and an arm with no model yet is left off. `glove_check.py --port 5006` checks the right glove. A glove with a magnetometer also gets magnetometer features. This is tested on simulated data only.

The camera tools open full screen. **F11** switches to a window and back, so you can reach the terminal, and `--windowed` starts in a window. Timed sessions never start on their own: calibrate with **C** (a 5-second countdown into guard), then press **.** (full stop, from any window) to start. Press **.** again to pause or resume. This covers the tuner's `test` and the capture session.

To change a setting without opening a tool, run `python punch_settings.py timeout 300` (any value from 100 to 1500 ms), `python punch_settings.py confidence 0.7`, or `python punch_settings.py` to show them all. Retrain after changing the timeout.

On a machine where `python` is not the project's Python, you get `No module named cv2`. Put `.\run.bat` in front instead, for example `.\run.bat punch_capture.py` or `.\run.bat punch_train.py`. It runs the script with the project's `.venv` and needs no activation. For step 5, `.\play.bat` is the same as `punch_live.py` (dry run), and `.\play.bat --live` presses keys.

Every tool except training takes `--simulate`, which uses a fake glove so you can try it without hardware. Run the tests with `python -m unittest discover tests`. The supporting modules are `punch_segment.py` (finds where punches start and end), `punch_features.py` (turns a punch into model inputs), `punch_session.py` (shared buffers, settings and data files) and `punch_sim.py` (fake glove data, for testing only). Shared settings live in `punch_config.json`.

The other scripts below came earlier and still work: Kadir's rule-based camera controller, and the wired Uno tracker.

## How the pieces connect

| Input | Runs on the board | Link | Runs on the laptop |
|---|---|---|---|
| Glove sensor, wired | `punch_stream.ino` on an Arduino Uno | USB serial at 500000 baud | `punch_tracker.py` |
| Glove sensor, wireless | `glove_imu.ino` on an ESP32-CAM | WiFi UDP on port 5005 | `glove_receiver.py` |
| Webcam | None | Camera 0 | `pose_tracker.py` |

The wired and wireless firmware send the same 19-byte frame, at 500 Hz:

```
A5 5A | timestamp in µs (uint32, little-endian) | ax ay az gx gy gz (int16, big-endian) | XOR of the timestamp and sensor bytes
```

Accelerometer range is ±16 g and gyro range is ±2000 deg/s. If you change these in the firmware, also change the matching constants in the Python script that reads it.

## Setup

Requires Python. These commands are for Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If you see `ModuleNotFoundError`, the virtual environment isn't active. In VS Code, run **Python: Select Interpreter** and choose `.venv`.

## Python scripts

### `punch_tracker.py`

Reads the wired glove over USB serial, finds punches and estimates their paths. Replace `COM7` with your port.

```powershell
python punch_tracker.py capture --port COM7 --seconds 30 --out punches.csv
python punch_tracker.py analyze punches.csv
python punch_tracker.py analyze punches.csv --save punches.png
python punch_tracker.py live --port COM7 --dry-run
python punch_tracker.py live --port COM7
```

- **`capture`** records the raw sensor data to a CSV file.
- **`analyze`** finds punches in a recording, prints a table of each punch's speed and reach, and plots the 3D paths.
- **`live`** classifies each punch as it happens and presses a key: `w` for a straight punch, or `s` for one that travels up or down. This is Windows only.

Hold still for the first 2 seconds, and rest briefly between punches, because the script uses those rests to limit drift. Back-to-back combinations can't be reconstructed. Treat paths as rough estimates: compare shape and direction rather than exact distances.

> **Warning:** without `--dry-run`, `live` sends real keystrokes to whichever window has focus.

### `pose_tracker.py`

Tracks your body through the webcam with MediaPipe and shows a skeleton overlay plus a rotatable 3D view. It detects jabs, crosses, hooks and uppercuts, plus duck, slip, block and guard-down. On first run, it downloads its pose model to `models/`, so it needs internet access once.

```powershell
python pose_tracker.py
```

| Key | Action |
|---|---|
| `c` | Calibrate: a 5-second countdown starts, so you can get into guard, then it records your stance. Needed for duck and slip detection. Pressing again during the countdown restarts it. The length is `CALIBRATE_DELAY_S` at the top of the file. |
| `a` / `d` | Rotate the 3D view |
| `q` | Quit |

The lead hand defaults to left (orthodox). To change it, set `LEAD` at the top of the file.

### `glove_receiver.py`

Receives the wireless glove's data over UDP, and puts each sample on the laptop's clock. Run directly, it prints the sample rate, peak acceleration and gyro readings. Other code can use its `GloveReceiver` class to read samples, but no script in this repo does that yet.

```powershell
python glove_receiver.py             # listen for a real glove
python glove_receiver.py --simulate  # fake a glove, with no hardware needed
```

To tell two gloves apart, add their IP addresses to `GLOVE_BY_IP`. Any glove not listed is treated as `L`. If the sensor is mounted at an angle, set `AXIS_MAP` and `AXIS_SIGN`.

### `phyphox_imu.py`

Receives a phone's accelerometer and gyroscope as the right-hand sensor, by polling the free phyphox app. The laptop opens the connection to the phone, so nothing has to get through the laptop's firewall. On the phone, open a phyphox experiment with the accelerometer **with gravity** and the gyroscope, turn on remote access in its menu (it shows an address), and press play. Then:

```powershell
python phyphox_imu.py --phone 172.20.10.1 --discover   # check it finds the accelerometer and gyroscope buffers
python phyphox_imu.py --phone 172.20.10.1              # print the readings as they arrive
```

An iPhone's address has no port (80); an Android phone shows one, as in `--phone 192.168.1.20:8080`. On an iPhone's own hotspot the phone is usually `172.20.10.1`. iOS may also need phyphox's Local Network permission. `--start` starts the measurement from the laptop, `--accel-only` is for an experiment with no gyroscope (rotation becomes zeros), and `--acc` / `--gyro` name the buffers if `--discover` can't find them. Units are converted from m/s² and rad/s to g and deg/s, and samples use the glove id `R`, so the left glove's pipeline does not see them. It has run on a real phone (about 80 Hz at rest), and the rest is tested against a fake phone built from phyphox's published API.

**As the right arm's sensor.** Add `--phone ADDRESS` to `--arm right` instead of a glove on port 5006, in `punch_capture.py`, `punch_tune.py` and `punch_live.py` (or `--right-phone ADDRESS` in `punch_live_both.py`). `--phone` is refused for the left arm, so the left glove's recordings and model can't be mixed with it.

```powershell
python punch_capture.py --arm right --phone 172.20.10.1 --person andre   # record CROSS, HOOK, UPPERCUT and NONE
python punch_train.py --arm right                                        # trains models/punch_rf_right.joblib
python punch_live.py --arm right --phone 172.20.10.1 --live
```

A phone has no magnetometer, so those features are left out, and it samples at about 80 Hz against a glove's 500 Hz. The punch-finding settings are shared by both arms (`punch_config.json`), so check them with `punch_tune.py --arm right --phone ...` before recording.

### `phone_imu.py`

The browser-page alternative to the above, which did not work in our test because the phone's connection never reached the laptop. Receives a phone's accelerometer and gyroscope as the right-hand sensor. The laptop serves a small web page, and the phone's browser sends its motion data back. There is nothing to install on the phone, but browsers only share motion data with https pages, so the laptop makes a certificate for itself (it needs OpenSSL, which comes with Git for Windows) and the phone asks you to accept it once.

```powershell
python phone_imu.py              # prints the address to open on the phone, then the data as it arrives
python phone_imu.py --no-tls     # plain http, to test on this computer only
```

On the phone, open the printed `https://<laptop address>:8443` in Safari or Chrome, accept the certificate warning, tap Start and allow motion access. Keep the page open and the screen on. Windows Firewall (and Norton, if it is on) must allow inbound TCP on port 8443. If the phone lying flat with the screen up reads -1 g on z, run with `--flip-accel`. If rotation looks about 57 times too small, add `--gyro-rad`. Set `AXIS_MAP` and `AXIS_SIGN` for how the phone sits on the hand. Samples use the glove id `R` and the same units as `glove_receiver.py`, and the left glove's pipeline does not see them. Nothing finds or classifies right-hand punches yet.

### `play.py`, `tune_punches.py` and `replay_punches.py` (the variant framework)

An earlier way of trying different product versions: each one is a folder in `variants/` with its own settings and its own recorded tuning sessions, found automatically. **No variant is active now.** The 45 degree camera was one, and it is shelved in `archive/angled_camera/` (its README explains why and how to bring it back). The main path is the glove pipeline above, so `python play.py` currently reports that it found no variants. The framework stays for trying a future version; `variants/README.md` explains how to add one.

`tune_punches.py` records a guided camera-only punching session and `replay_punches.py` scores the camera rules on it with no camera needed. Both work without `--variant` for the front camera alone.

The hotkeys below are shared by `punch_live.py`, `controller.py` and `play.py`:

| Key | Action |
|---|---|
| `F7` | Calibrate or recalibrate, from any window. Press it, then get in guard: it counts down 5 seconds, then beeps when it worked. |
| `F8` | Turn key output on or off. A live run starts off. |
| `F9` | Quit |

### `keybridge.py` and `website/`

A web page that tracks you with the webcam and plays a built-in sparring game, plus a small local program that turns the page's punches into real key presses for Browser Boxing. See `website/README.md`. Its glove mode is the earlier rule-based one (the glove confirms a camera punch), not the trained model.

```powershell
.\run.bat keybridge.py            # dry run: serves the site, prints keys, presses nothing
.\run.bat keybridge.py --live     # really presses keys
```

## Firmware

The Uno sketches use only the built-in `Wire` library. `glove_imu.ino` needs the ESP32 board package (core 3.x). Each sketch lists its wiring at the top of the file.

| File | Board | What it does |
|---|---|---|
| `firmware/punch_stream/punch_stream.ino` | Arduino Uno | Streams sensor data over USB to `punch_tracker.py`. Close the Serial Monitor first, because only one program can use the port. |
| `firmware/glove_imu/glove_imu.ino` | AI Thinker ESP32-CAM | Sends sensor data over WiFi to `glove_receiver.py`, with the training button's state in every frame. The camera isn't used. Wire the button between GPIO12 and GND, and never to 3V3, or the board won't boot. |
| `firmware/glove_imu/config.example.h` | ESP32-CAM | Template for your WiFi details. Copy it to `config.h` in the same folder and fill it in, or `glove_imu.ino` won't compile. Git ignores `config.h`, so your password stays on your computer. |
| `firmware/glove_imu_s3/glove_imu_s3.ino` | ESP32-S3 + MPU-9250, 9255 or 6500 | The same stream as `glove_imu.ino`, plus the magnetometer when the chip has one, in a 26-byte frame. SDA GPIO2, SCL GPIO1, button GPIO5 to GND. `UDP_PORT` picks the arm: 5005 left, 5006 right. Needs its own `config.h`. Build with USB CDC On Boot enabled to see its messages on the S3's own USB port. |
| `firmware/mpu6050_usb_check/mpu6050_usb_check.ino` | ESP32-CAM | Is the sensor alive? USB only, no WiFi. Prints a verdict at 115200 baud, and the red LED shows it on the breadboard: a short blink each second means alive, a slow blink means bad data, a fast flicker means not found. |
| `firmware/mpu9250_usb_check/mpu9250_usb_check.ino` | ESP32-S3 | The same check for the S3 glove's sensor, including the magnetometer. |
| `firmware/sketch_nov6a/sketch_nov6a.ino` | Arduino Uno | Sensor test that shows roll, pitch and yaw on the Serial Plotter at 115200 baud. Its output doesn't work with the Python scripts. Yaw drifts over time. |

## Project files

| File | What it does |
|---|---|
| `requirements.txt` | Python packages for all three scripts. |
| `play.bat` | Plays with the glove and camera (`punch_live.py`): dry run, or `--live` to press keys. Uses `.venv` without activating it. |
| `run.bat` | Runs any script with the project's `.venv`, for example `.\run.bat punch_capture.py`. Use it when `python` reports `No module named cv2`. |
| `overlay.py` | Drawing helpers for the large on-screen text used by the recorder and the calibration countdown. |
| `archive/` | Work that is set aside but kept. `archive/angled_camera/` is the shelved 45 degree camera. |
| `keybridge.py`, `website/` | The web page and the local program that presses keys for it (see above). |
| `.gitignore` | Keeps `.venv/`, the root `models/` folder, `__pycache__/`, `config.h`, recorded data and local Claude settings out of git. |
| `CLAUDE.md`, `AGENTS.md` | Point coding agents to the Entire guide below. |
| `.entire/agent-guide.md` | Tells coding agents how to use Entire's Graph and Brain tools in this repo. |
| `.entire/settings.json` | Turns on Entire and sets where it stores session checkpoints. |
| `.entire/.gitignore` | Keeps Entire's local logs, metadata and temporary files out of git. |
| `.claude/settings.json` | Claude Code hooks that connect sessions and edits to Entire. |
| `.brainignore` | Keeps local Claude settings out of Entire's Brain index. |

## How we use Entire

| Part | How we use it |
|---|---|
| Sessions | Entire captures Claude Code sessions only (`entire status` lists that one agent). Kiro is not a supported agent, so Kiro sessions are not captured. At the end of a Kiro session we save what was decided and what is left to do as a Brain memory instead, for example the handoff for `kadir/variants`. |
| Graph | Agents find code with `entire graph query` instead of searching files. Measurements are below. |
| Brain | Decisions, gotchas and team rules are saved with `entire brain remember`, and agents read them with `entire brain brief` and `entire brain recall` before they change anything. The memories are shared across the team. |
| Trails | Not used yet. Changes go in through GitHub pull requests. |

### Graph output size

Each row is one question asked with `entire graph query --repo . --profile full --head --format agent --max-context-bytes 4096`. The last two columns show what an agent would read without Graph: the top-ranked file, or every file Graph points to. Measured on 7 November 2026 with Entire CLI 0.13.1 on commit `a7bc7f9`. File sizes are from the working tree.

| Question | Graph output | Top-ranked file | All files Graph points to |
|---|---|---|---|
| Where are punch detection settings loaded for a variant | 3,973 B | `variants/base.py` 4,968 B | 5 files, 56,080 B |
| How does the glove confirm a camera punch | 4,036 B | `glove_punch.py` 4,309 B | 6 files, 60,961 B |
| Where are keyboard keys pressed for a punch | 4,068 B | `game_input.py` 9,743 B | 6 files, 56,000 B |
| How is the body angle measured for the angled camera | 4,137 B | `variants/angled_camera/__init__.py` 8,745 B | 6 files, 74,321 B |
| Where is the recalibrate hotkey handled | 3,880 B | `glove_check.py` 12,852 B | 5 files, 49,033 B |

A Graph answer is about 4 KB, which is roughly 1,000 tokens at 4 bytes per token (an estimate, not a billed count). For the last question, a plain search for "recalibrat" finds `controller.py` and `pose_tracker.py`, which are 21,470 B to read in full.

These numbers measure output size only. They don't check that the answer is right: for the hotkey question the correct file, `controller.py`, was ranked third. We have not yet compared the tokens an agent uses over a whole task with and without Graph.
