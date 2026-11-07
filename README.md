# hack_trent_submission
Hack Trent Boxing Submission

A left-hand boxing controller. A motion sensor on the glove and a webcam watch each punch, a random forest decides whether it was a jab, hook or uppercut, and the laptop presses that punch's key in a boxing game. The team's plan and decisions are in [PROJECT.md](PROJECT.md).

## Main pipeline: glove and camera with a trained model

| Step | Command | What it does |
|---|---|---|
| 1. Check the glove | `python glove_check.py`, then `python glove_receiver.py` | Confirms data arrives. The receiver also shows whether the button reads `HELD` |
| 2. Pick the timeout | `python punch_tune.py` | Type a timeout in ms in the terminal and watch each punch's start and end on the camera window. `test 10 60 jab` runs a timed test and compares timeouts. Type `save` to keep the settings |
| 3. Record | `python punch_capture.py --person <name>` | The screen calls JAB, HOOK or UPPERCUT with a beep, in random order. Throw that punch. Records 100 of each, plus NONE (not a punch) examples from movement between prompts. `--target 20` makes a smaller set. `--button` uses the glove button instead. Data stays in `punch_data/` |
| 4. Train | `python punch_train.py` | Trains the random forest and reports glove-only, camera-only and combined accuracy. Saves `models/punch_rf.joblib` |
| 5. Play | `python punch_live.py --live` | J jab, H hook, U uppercut, plus W/S/A/D defence keys from the camera. F7 calibrate, F8 key output, F9 quit. **[** and **]** lower or raise the confidence cutoff while playing, with no retraining. `--confidence 0.7` and `--start-g 3` set the cutoff and the punch threshold at startup. `--popup` shows the camera as a small always-on-top window in a screen corner (`--popup-corner`, `--popup-size`) that never takes focus from the game and ignores clicks, so quit with F9. A game in exclusive full screen hides it |

**Two gloves (optional).** Everything above is the left arm, and it is the default. A second glove on UDP port 5006 is the right arm: add `--arm right` to `punch_tune.py`, `punch_capture.py`, `punch_train.py` and `punch_live.py`. The right arm has its own recordings (`punch_data_right/`), model (`models/punch_rf_right.joblib`) and punches (CROSS = K, HOOK = H, UPPERCUT = U; its hook goes right, forward, left). Its camera features are mirrored, so a hook looks the same to both models. `python punch_live_both.py --live` runs both models side by side with one camera, and an arm with no model yet is left off. `glove_check.py --port 5006` checks the right glove. A glove with a magnetometer also gets magnetometer features. This is tested on simulated data only.

The camera tools open full screen. **F11** switches to a window and back, so you can reach the terminal, and `--windowed` starts in a window. Timed sessions never start on their own: calibrate with **C** (a 5-second countdown into guard), then press **.** (full stop, from any window) to start. Press **.** again to pause or resume. This covers the tuner's `test` and the capture session.

To change a setting without opening a tool, run `python punch_settings.py timeout 300` (any value from 100 to 1500 ms), `python punch_settings.py confidence 0.7`, or `python punch_settings.py` to show them all. Retrain after changing the timeout.

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
| `c` | Calibrate: a 5-second countdown starts, so you can get into guard, then it records your stance. Needed for duck and slip detection |
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
| `.gitignore` | Keeps `.venv/`, `models/`, `__pycache__/`, `config.h` and local Claude settings out of git. |
| `CLAUDE.md`, `AGENTS.md` | Point coding agents to the Entire guide below. |
| `.entire/agent-guide.md` | Tells coding agents how to use Entire's Graph and Brain tools in this repo. |
| `.entire/settings.json` | Turns on Entire and sets where it stores session checkpoints. |
| `.entire/.gitignore` | Keeps Entire's local logs, metadata and temporary files out of git. |
| `.claude/settings.json` | Claude Code hooks that connect sessions and edits to Entire. |
| `.brainignore` | Keeps local Claude settings out of Entire's Brain index. |
