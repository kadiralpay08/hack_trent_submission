# Commands

Run from the project folder with `py` (or `.venv\Scripts\python.exe`). The phone's address is the one phyphox shows, such as `172.20.10.1`.

| Command | What it does | Options |
|---|---|---|
| `py flash.py cam --ip auto` | Builds and flashes a board, then shows its messages. | Board: `cam` (left glove), `s3`, `cam-check` / `s3-check` (sensor test over USB). `--ip auto` sends to this laptop, `--ip X` to address X. `--ssid NAME` sets the WiFi. `--port COMn` picks the USB port. `--seconds N` shows messages for N s (default 12). `--watch` only shows messages, `--list` lists ports |
| `py glove_check.py --until 2` | Checks the glove's WiFi data, step by step, and says what to fix. | `--until 1-4` stops after that step (1 heard, 2 data quality, 3 still, 4 punches). `--port 5005` left / `5006` right. `--wait S` seconds to wait for the glove |
| `py punch_capture.py --both --right-phone ADDR --target 40` | Records training punches for both arms in combos, then trains both arms. | `--target N` of each punch per arm (recordings you have count). `--person NAME` (typed in the camera window if left out). `--right-phone ADDR` uses a phone as the right arm. `--no-train` skips training |
| `py punch_capture.py --arm left --target 40` | Records one arm only, then trains it. | `--arm left/right`, `--phone ADDR` (right arm only), `--target N`, `--person NAME`, `--button` (hold the glove button per punch), `--no-train` |
| `py punch_train.py --arm left` | Retrains one arm's model from all its recordings. | `--arm left/right`. `--features both/imu/pose`: glove and camera, glove only, or camera only |
| `py punch_sessions.py` | Lists recording sessions, and deletes or restores whole sessions. | `--delete SESSION` or `--delete last`. `--person NAME` deletes everything by one person. `--deleted` lists deleted ones, `--restore SESSION` brings them back. `--yes` skips the check, `--no-train` skips retraining |
| `py punch_live_both.py --right-phone ADDR --live` | **The final program**: both arms play the game, with the keybind window open beside it. | `--live` really presses keys (without it, only prints them). `--confidence 0-1` how sure before pressing (default 0.6). `--no-editor`, `--no-tracking` |
| `py punch_live.py --live` | Plays with one arm. | `--arm left/right`, `--phone ADDR`, `--live`, `--confidence 0-1`, `--popup` (small corner camera), `--no-tracking` |
| `py controls_editor.py` | Opens the keybind window on its own. | `--mapping FILE` (default `mappings/controls.json`) |
| `py punch_tune.py` | Shows punches being found live, to pick the timeout and threshold. | `--arm left/right`, `--phone ADDR` |
| `py phyphox_imu.py --phone ADDR` | Prints the phone's sensor readings, to check it is connected. | `--discover` lists what phyphox sends |

All camera tools also take `--windowed` (not full screen), `--camera N` (another webcam), `--simulate` (fake sensors) and `--max-seconds N` (stop by itself).

**Keys:** in the camera window, **C** calibrates (5 s countdown into guard). **.** starts and pauses recording. **F8** turns key output on/off, **F9** quits, **[ ]** lower/raise confidence, **F11** full screen. Capture won't start until every sensor is sending, and pauses by itself if one stops for 1 s. In the capture terminal: `x` undoes the last punch, `discard` throws away the session, and `q` quits (and trains).
