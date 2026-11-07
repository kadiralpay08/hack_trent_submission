# hack_trent_submission
Hack Trent Boxing Submission

A boxing motion tracker with two independent inputs: an MPU6050 motion sensor on a glove, and a webcam. The Python scripts don't import each other, so each one runs on its own and the glove and webcam data aren't combined yet.

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
| `c` | Calibrate while standing in guard, which is needed for duck and slip detection |
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

### `play.py`
Plays a boxing game with your body, using one of the product variants we are testing. `python play.py` lists them. Each variant has its own settings and its own recorded tuning sessions, so choosing one never changes another. Adding a variant means adding a folder, with no other file edited. See `variants/README.md` for the three versions, how to try the 45 degree camera, and how to add a variant.
```powershell
python play.py                        # list the variants
python play.py angled_camera          # dry run: prints the keys it would press
python play.py angled_camera --live   # presses keys in whichever window has focus
```
| Key | Action |
|---|---|
| `F7` | Calibrate or recalibrate, from any window. Stand in guard first. |
| `F8` | Turn key output on or off. A live run starts off. |
| `F9` | Quit |

The tuning tools take the same `--variant` option: `python tune_punches.py --variant angled_camera` records a guided punching session, and `python replay_punches.py --variant angled_camera` scores the variant's settings on it, with no camera needed.
## Firmware

The Uno sketches use only the built-in `Wire` library. `glove_imu.ino` needs the ESP32 board package (core 3.x). Each sketch lists its wiring at the top of the file.

| File | Board | What it does |
|---|---|---|
| `firmware/punch_stream/punch_stream.ino` | Arduino Uno | Streams sensor data over USB to `punch_tracker.py`. Close the Serial Monitor first, because only one program can use the port. |
| `firmware/glove_imu/glove_imu.ino` | AI Thinker ESP32-CAM | Sends sensor data over WiFi to `glove_receiver.py`. The camera isn't used. |
| `firmware/glove_imu/config.example.h` | ESP32-CAM | Template for your WiFi details. Copy it to `config.h` in the same folder and fill it in, or `glove_imu.ino` won't compile. Git ignores `config.h`, so your password stays on your computer. |
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
