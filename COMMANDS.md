# Command cheat sheet

## Before you start

Run everything from the project folder, in PowerShell:

```powershell
cd C:\Users\Andre\code\hack_trent_submission
```

Every command starts with `.\.venv\Scripts\python.exe`, the project's Python. This sheet shortens that to `py`. To type `py` for real, run this once in each new terminal:

```powershell
Set-Alias py .\.venv\Scripts\python.exe
```

**How to read an option:** `--until N` means you type `--until` and then a value in place of `N`, for example `--until 2`. Each table says what the value is, its units, and what happens if you leave the option out (the **default**). An option with no value, such as `--live`, is a switch: writing it turns it on. Options can go in any order.

**Run one command at a time.** The camera tools and the controls editor keep the terminal busy until you close them. A second pasted line waits until then, or sits at the prompt until you press Enter. To run two at once, open a second terminal (the **+** in VS Code's terminal panel), or start the editor in the background:

```powershell
Start-Process .\.venv\Scripts\python.exe controls_editor.py
```

Only one tool can listen to a glove at a time. Close one (`q`, F9 or the window) before starting the next.

## The usual session

| Step | Command |
|---|---|
| 1. Flash the glove (only if its WiFi or IP changed) | `py flash.py cam --ip auto` |
| 2. Check the data arrives | `py glove_check.py --until 2` |
| 3. Record punches | `py punch_capture.py --target 40 --person andre` |
| 4. Train | `py punch_train.py` |
| 5. Set up keybinds (optional) | `py controls_editor.py` |
| 6. Play | `py punch_live.py --live --windowed` |

---

## `flash.py`: flash a board

`py flash.py BOARD [options]`

| Part | Value | Default | What it does |
|---|---|---|---|
| `BOARD` (required, no `--`) | `cam`, `s3`, `cam-check` or `s3-check` | none | Which board and sketch. **cam** = ESP32-CAM glove (`firmware/glove_imu`). **s3** = ESP32-S3 glove (`firmware/glove_imu_s3`). **cam-check** / **s3-check** = sensor checks that use USB only, with no WiFi |
| `--ip ADDRESS` | An IP address such as `172.20.10.5`, or `auto` for this laptop's current WiFi address, or `""` to broadcast | Leaves `config.h` as it is | Which computer the glove sends its data to. Saved into the board's `config.h` before building. Broadcasting loses about a third of the data, so give one address |
| `--ssid NAME` | The WiFi network's name, in quotes if it has spaces or an apostrophe: `--ssid "Andre's iPhone"` | Leaves `config.h` as it is | Which WiFi network the glove joins. The password is not set here: edit `config.h` for that, so it never ends up in your terminal history |
| `--port COMn` | A serial port name such as `COM6` | Found automatically | Which USB port the board is on. Only needed if it picks the wrong one; `--list` shows them |
| `--seconds N` | Seconds (decimals allowed) | `12` | How long to show the board's own messages after flashing. `0` skips this |
| `--watch` | switch | off | Don't flash; just show the board's messages (for `--seconds` seconds) |
| `--list` | switch | off | List the USB serial ports and stop |

Examples: `py flash.py cam --ip auto`, `py flash.py cam --ip 172.20.10.5 --ssid "Andre's iPhone"`, `py flash.py cam --watch --seconds 30`.

**What you should see:**
- The ESP32-CAM flashes on its programmer board, where the sensor isn't connected, so "Sensor 0 Hz" there is normal. Move it to the breadboard afterwards.
- Look for `Sending UDP to <this laptop's IP>`.
- If the ESP32-S3 won't connect, hold BOOT, tap RST, let go of BOOT, and run the command again.

**cam-check result**, on the red LED once on the breadboard:
- a short blink each second: the sensor is alive
- a slow blink: it was found, but its data is bad
- a fast flicker: no sensor found

---

## `glove_check.py`: is the glove sending good data?

It runs four steps in order and tells you what to fix:
1. Hear the glove: any data arrives at all.
2. Data quality: frames arrive intact, at the right rate, with few gaps.
3. At rest: hold the glove still. Gravity should read 1 g, and rotation near 0.
4. Punches: throw a few, and each is counted with how hard it hit.

| Option | Value | Default | What it does |
|---|---|---|---|
| `--until N` | Step number: `1`, `2`, `3` or `4` | `4` (all steps) | Stop after step N. `--until 2` = just "is data arriving, and how fast" |
| `--wait N` | Seconds | `45` | How long step 1 waits for the glove before giving up |
| `--port N` | UDP port number | `5005` | `5005` = left glove, `5006` = right glove |
| `--hz N` | Samples per second | `500` | The rate the firmware should send. Step 2 compares against it |
| `--punches N` | Number of punches | `5` | How many punches step 4 asks for |
| `--punch-seconds N` | Seconds | `30` | How long step 4 waits for those punches |
| `--simulate` | switch | off | Use a fake glove, to try the tool |

Example: `py glove_check.py --until 2 --wait 20`

`py glove_receiver.py` shows the glove's live numbers, including whether the button reads `HELD`. Its only option is `--simulate`.

---

## `punch_capture.py`: record training punches

**Controls:**
- In the camera window, **C** calibrates: a 5 s countdown, then stand in guard.
- **.** (full stop) starts, and pauses or resumes. It works from any window.
- In the terminal, `x` + Enter undoes the last saved punch, and `q` + Enter quits.

| Option | Value | Default | What it does |
|---|---|---|---|
| `--target N` | Number of each punch | `100` | Stop when every punch type has N recordings. **Recordings you already have count**: with 20 saved, `--target 40` records 20 more |
| `--person NAME` | Any word, such as `andre` | empty | Who is punching. Saved with each recording, for your own records |
| `--arm SIDE` | `left` or `right` | `left` | Which glove. `right` uses port 5006 and saves to `punch_data_right/` |
| `--button` | switch | off | Old mode: hold the glove button around each punch instead of following beeps. Records no NONE examples |
| `--data FOLDER` | A folder path | `punch_data` (left), `punch_data_right` (right) | Where to save the recordings |
| `--windowed` | switch | off (full screen) | Start in a normal window. F11 switches while running |
| `--camera N` | Webcam number | `0` (the built-in one) | Use another webcam: `1`, `2`, ... |
| `--port N` | UDP port number | The arm's: `5005` or `5006` | Listen for the glove on another port |
| `--max-seconds N` | Seconds | none (runs until done) | Stop by itself after N seconds (for testing) |
| `--simulate` | switch | off | Fake glove, no hardware |

**NONE examples:** in "MOVE, DON'T PUNCH" rounds, move sharply (snap your guard, feint, bob). Each one saved shows `saved NONE n` in the terminal.

**Where recordings go:** `punch_data/<PUNCH>/`. Folders starting with `_` are ignored: `_rejected` holds failed attempts, and `_old_mpu6050` the old sensor's data.

---

## `punch_train.py`: train the model

| Option | Value | Default | What it does |
|---|---|---|---|
| `--features WHICH` | `both`, `imu` or `pose` | `both` | What the model learns from: glove and camera (`both`), glove only (`imu`), or camera only (`pose`). Accuracy for all three is printed either way |
| `--arm SIDE` | `left` or `right` | `left` | Which glove's recordings and model |
| `--data FOLDER` | A folder path | The arm's folder | Train on other recordings |
| `--out FILE` | A file path ending in `.joblib` | `models/punch_rf.joblib` (left) | Where to save the model. `punch_live.py --model FILE` plays with it |
| `--config FILE` | A settings file | `punch_config.json` | Which punch settings to cut the recordings with |

---

## `punch_live.py`: play

| Option | Value | Default | What it does |
|---|---|---|---|
| `--live` | switch | off (dry run) | Really press keys. Without it, it only prints them. With it, key output starts OFF: press **F8** |
| `--windowed` | switch | off (full screen) | Camera in a normal window. F11 switches |
| `--popup` | switch | off | Camera as a small always-on-top window in a corner, so the game stays visible |
| `--popup-corner WHERE` | `bottom-right`, `bottom-left`, `top-right` or `top-left` | `bottom-right` | Which corner the popup sits in |
| `--popup-size N` | Pixels | `360` | Popup width |
| `--confidence N` | A number from 0 to 1 (`0.7` = 70%) | `0.6`, from `punch_config.json` | How sure the model must be before it presses a key. Higher = fewer wrong keys but more missed punches. **[** and **]** change it while playing |
| `--start-g N` | g (1 g = gravity) | What the model was trained with (2.5) | How hard a movement must be to count as a punch. Change it by 1 or less, or retrain |
| `--no-tracking` | switch | off | Ignore the glove button: no mouse steering, and the normal keys always |
| `--mapping FILE` | A controls `.json` file | `mappings/controls.json` | Which keybinds to use |
| `--arm SIDE` | `left` or `right` | `left` | Which glove and model |
| `--model FILE` | A `.joblib` model | The arm's model | Play with another model |
| `--camera N` | Webcam number | `0` | Use another webcam |
| `--port N` | UDP port number | `5005` / `5006` | Listen on another port |
| `--max-seconds N` | Seconds | none | Stop by itself (for testing) |
| `--simulate` | switch | off | Fake glove, no hardware |

**Hotkeys** (from any window):

| Key | What it does |
|---|---|
| **F7** | Calibrate (or **C** in the camera window) |
| **F8** | Key output on or off |
| **F9** | Quit |
| **[** / **]** | Confidence cutoff down or up |
| **F11** | Full screen on or off |

**Tracking mode:** hold the glove button, and your right hand steers the mouse from where it was when you pressed. Punches then use the tracking binds (default: jab = left click, hook = right click, uppercut = middle click). The camera's block is off while you hold the button.

`py punch_live_both.py` runs both gloves' models at once. It takes `--live`, `--windowed`, `--confidence N`, `--camera N`, `--mapping FILE`, `--max-seconds N` and `--simulate`, which work as above.

---

## `controls_editor.py`: keybinds

`py controls_editor.py`

| Option | Value | Default | What it does |
|---|---|---|---|
| `--mapping FILE` | A controls `.json` file | `mappings/controls.json` | Which file to edit. A new name creates it from the defaults |

**Binding a key:** click a bind, then press a key or click a mouse button. Esc cancels, and **x** clears the bind.

**Hold (ms)** is how long a punch keeps its key or mouse button down, in milliseconds.

**Steering settings:**

| Setting | Meaning | Default |
|---|---|---|
| Speed / Position | Speed acts like a thumbstick: holding the hand out keeps turning. Position acts like a trackpad: the view follows the hand | Speed |
| Speed (px/s) | Pixels per second, with the hand half a shoulder-width from where it started | 1200 |
| Curve | 1 = even; higher = finer control near the centre, faster at the edge | 1.5 |
| Dead zone | How far the hand can drift without moving the mouse, in shoulder widths | 0.06, about 2 cm |
| Sensitivity (position mode) | Pixels per shoulder-width of hand movement | 1500 |
| Vertical vs horizontal | Up/down speed compared with left/right. 0.5 = half as fast | 1.0 |
| Invert up/down | Flips the up/down direction | Off |
| Camera BLOCK works while tracking | Lets block work while you steer | Off |

Changes save straight away, and a running `punch_live.py` picks them up within a second.

---

## `punch_settings.py`: punch settings, no camera needed

`py punch_settings.py NAME VALUE`

| Command | Value | Default | What it does |
|---|---|---|---|
| `py punch_settings.py` | none | none | Show every setting |
| `timeout N` | Milliseconds, 100 to 1500 | `350` | The longest a punch can last before it is cut off. The key is pressed when the punch ends, so shorter = less delay. **Retrain afterwards** |
| `confidence N` | 0 to 1 | `0.6` | The default confidence cutoff for `punch_live.py`. No retraining needed |
| `start_g N` | g | `2.5` | How hard a movement must be to start a punch. Retrain afterwards |
| `decel_g N` | g | `3` | How hard a stop must be to end a punch early |
| `pre_s N` | Seconds | `0.25` | How much movement before the start is included, such as an uppercut's dip |
| `lag N` | Seconds | `0` | How far the camera runs behind the glove |

Other names are listed by `py punch_settings.py`. Every tool reads these settings when it starts, so restart any tool that is open.

---

## `punch_tune.py`: watch punches being found, and pick a timeout

`py punch_tune.py`. It takes the same `--arm`, `--windowed`, `--camera N`, `--port N`, `--max-seconds N` and `--simulate` options as above.

Type these in its terminal, each followed by Enter:

| Command | Value | What it does |
|---|---|---|
| `N` (just a number, such as `300`) | Milliseconds | Try that timeout. You see each punch's start and end on screen |
| `test N S PUNCH` (such as `test 10 60 jab`) | N = how many punches, S = seconds to throw them in, PUNCH = `jab`, `hook` or `uppercut` | A timed test. Press **.** to start. At the end it shows how many punches it found and how they were classified, at this timeout and several others |
| `show` | none | Show the current settings |
| `save` | none | Save the settings to `punch_config.json` |
| `q` | none | Quit |

---

## Other

| Command | What it does |
|---|---|
| `py -m unittest discover tests` | Run every test, on fake data; no hardware needed |
| `py controller.py --live` | Kadir's older camera-only controller. `--glove` only accepts punches the glove also felt; `--simulate-glove` uses a fake glove; `--builtin-punches` uses the original punch rules; `--mapping FILE` |
| `py game_input.py --live` | Presses every mapped key once after a 5 s countdown, to test that the game receives them |
