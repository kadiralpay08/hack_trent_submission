# Keyboard Warrior website

A static site (plain HTML, CSS and JavaScript, no build step) that tracks you with the webcam, draws the skeleton on you, finds your punches, and plays a boxing game with them.

## Run it on this computer

```powershell
.\.venv\Scripts\python.exe keybridge.py            # dry run: serves the site, prints keys, presses nothing
.\.venv\Scripts\python.exe keybridge.py --live     # really presses keys (for the real Browser Boxing)
.\.venv\Scripts\python.exe keybridge.py --live --glove
```

It opens `http://127.0.0.1:8765/`. The built-in sparring game needs no bridge at all, only a webcam. Without a camera, J K H U throw punches and W S A D block, duck and slip.

To serve the site without the bridge: `python -m http.server 8000` inside this folder, then open `http://localhost:8000`. A camera needs `localhost` or HTTPS, so opening `index.html` as a file does not work.

## What is where

| Path | What it does |
|---|---|
| `index.html`, `css/style.css` | The page: the stage on the left, the camera and controls on the right. |
| `js/main.js` | Wires everything together: camera loop, calibration, modes, tabs, the panel. |
| `js/detect.js` | The punch detection, ported from `pose_tracker.py` and `punch_detector.py`. See "Keeping it in step" below. |
| `js/pose.js` | Camera, the MediaPipe pose model, and the picture with the skeleton drawn on it. |
| `js/game.js` | The built-in sparring game and its sounds. |
| `js/modes.js` | The ways to play. Front camera + glove is the default. The 45 degree camera is listed as shelved. Two cameras is not built. |
| `js/bridge.js` | Talks to `keybridge.py`. |
| `settings/angled_camera.json` | A copy of `archive/angled_camera/settings.json`, used only if the 45 degree mode is switched back on. |
| `vendor/` | MediaPipe and the pose model (23 MB), so the site works offline. If it is missing, the site downloads them from a CDN. |
| `../keybridge.py` | The program that presses real keys. |

## Why there is a bridge

Browser Boxing sends `frame-ancestors 'none'`, so it cannot be shown inside another page, and a web page cannot press keys in another window. So on the **Browser Boxing** tab the game opens in its own window, placed over the stage, and the page tells `keybridge.py` what you did. The bridge presses the keys from `mappings/browser_boxing.json` using `game_input.py`, the same code `controller.py` uses.

The bridge only accepts requests from this computer and from `keyboardwarrior.club` (more with `--allow-origin`), only for the actions in the mapping, starts with keys OFF, and releases held keys if the page stops talking to it. F7 calibrates, F8 turns keys on or off, F9 turns them off, from any window.

## Keeping it in step with the Python code

`js/detect.js` is a line-by-line port, checked against the Python on your recorded tuning sessions (456 punch events across 3 settings) and on 5,400 synthetic frames, with identical results. It does not update itself:

- **Settings:** the default mode (front camera + glove) uses the detector's built-in defaults, so there is nothing to copy. A mode with its own settings file, such as the shelved 45 degree one, reads `variants/<mode>/settings.json` live when the bridge runs, and falls back to its copy in `settings/` on a hosted site.
- **Detector code:** if the logic in `punch_detector.py` or the frame handling in `pose_tracker.py` changes (not only its settings), the same change has to be made in `js/detect.js`.

## Publishing on keyboardwarrior.club

1. Publish this folder as it is (Netlify, Cloudflare Pages or GitHub Pages: "publish directory" is `website`). Test it on the host's free address first. The camera needs HTTPS, which these hosts provide.
2. Once `keyboardwarrior.club` is registered, add it as a custom domain in the host's settings, then add the DNS records the host shows you in GoDaddy (usually a CNAME for `www` and A records or an ALIAS for the bare domain). It can take minutes to a few hours.
3. The hosted site finds the bridge at `http://127.0.0.1:8765`. Chrome and Edge allow this from an HTTPS page and may ask once for permission to reach devices on your network. Safari blocks it. **This hosted-to-local connection has not been tested from a real HTTPS page yet.** It has been tested between two local addresses.

## Known limits

- Tracking pauses if this window is hidden or fully covered, because browsers stop animation in covered windows. Keep the camera panel visible, which is why the game opens beside it and not over it.
- The "Front + side camera" mode is listed but not built, as in `variants/README.md`.
- Glove mode follows `controller.py --glove`: the glove confirms a camera punch. It is not the trained random forest model that `play.bat` (`punch_live.py`) uses, so the website and the Python game classify punches differently. Straight punches are not detected from a front-on camera. The glove has not been tested with real hardware here, only with the simulated glove.
- The real Browser Boxing has not been played with this yet. The key mapping notes in `mappings/browser_boxing.json` still apply (whether guard is held or toggled, and which way a slip moves the boxer).
- Use casual play or bots in Browser Boxing, never ranked.
