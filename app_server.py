"""The product's control panel: a web page for setup, calibration, recording, training and play.

  python app_server.py            serve the page at http://127.0.0.1:8770/ and open it in your browser
  python app_server.py --no-browser --port 8770

The page can't start Python programs or edit your files by itself, so this small server does it for
it. It runs the SAME tools you would type (punch_capture.py, punch_train.py, punch_live.py ...): nothing
here changes how punches are found or classified. One tool runs at a time, because the camera and the
glove's network port can only be used by one program.

Safety: it listens on this computer only (127.0.0.1), refuses requests whose Host or Origin is not this
page (so another website you have open cannot drive it), and can only start the tools listed in TOOLS,
with every option checked. Real key presses to a game need the "Press keys for real" tick.
"""
import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import game_input as gi
import game_launcher
import punch_session as ps
import web_view

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "website")
CONTROLS_PATH = os.path.join(ROOT, "mappings", "controls.json")
PRESETS_DIR = os.path.join(ROOT, "mappings", "presets")
LAUNCHERS_PATH = game_launcher.CONFIG_PATH
GAMES_PATH = os.path.join(SITE, "ui", "games.json")
DEFAULT_PORT = 8770
LOG_LINES = 600
MAX_BODY = 64 * 1024

NAME = re.compile(r"[\w][\w .'-]{0,39}")           # a person's name
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-:]{0,63}")  # an address such as 192.168.137.252 or 192.168.137.252:8080
COMMAND = re.compile(r"[\w .=\-]{1,60}")          # a line typed into capture or tune
TYPED_TOOLS = ("capture", "calibrate")             # the tools that read typed commands
CAMERA_TOOLS = ("calibrate", "capture", "live")    # the tools with a camera picture, shown in the page (web_view.py)
PRESET = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")      # a game preset, mappings/presets/<name>.json
SESSION = re.compile(r"[0-9A-Za-z_\-]{1,40}")


class BadRequest(Exception):
    """The page asked for something that is not allowed or makes no sense. The message is shown to the user."""


# ----------------------------------------------------------------------------------------------------
# Tools the page may start. Each builder returns (title, command-line) from already-validated options.
# ----------------------------------------------------------------------------------------------------
def _arm(p):
    arm = p.get("arm", "left")
    if arm not in ps.ARMS:
        raise BadRequest("Choose the left or the right arm.")
    return arm


def _int(p, key, low, high, default):
    value = p.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be a whole number.")
    if not low <= value <= high:
        raise BadRequest(f"{key} must be between {low} and {high}.")
    return value


def _host(p, key="phone"):
    value = str(p.get(key) or "").strip()
    if value and not HOST.fullmatch(value):
        raise BadRequest("The phone address looks wrong. Use the address phyphox shows, such as 192.168.137.252.")
    return value


def _common_window(p, cmd):
    if p.get("windowed"):
        cmd.append("--windowed")
    camera = _int(p, "camera", 0, 9, 0)
    if camera:
        cmd += ["--camera", str(camera)]


def tool_calibrate(p):
    cmd = ["punch_tune.py"]
    _common_window(p, cmd)
    return "Camera and punch-finding check", cmd


def tool_glove_check(p):
    return "Glove check", ["glove_check.py", "--wait", str(_int(p, "wait", 5, 120, 45))]


def tool_capture(p):
    target = str(_int(p, "target", 1, 500, 25))
    person = str(p.get("person") or "").strip()
    if not NAME.fullmatch(person):
        raise BadRequest("Enter your name (letters, numbers, spaces) so recordings are filed under you.")
    cmd = ["punch_capture.py", "--target", target, "--person", person]
    arm = p.get("arm", "left")
    phone = _host(p)
    if arm == "both":
        cmd.append("--both")
        if phone:
            cmd += ["--right-phone", phone]
        title = "Record both arms"
    else:
        arm = _arm(p)
        cmd += ["--arm", arm]
        if phone and arm == "right":
            cmd += ["--phone", phone]
        title = f"Record the {arm} arm"
    _common_window(p, cmd)
    return title, cmd


def tool_train(p):
    arm = _arm(p)
    return f"Train the {arm} arm", ["punch_train.py", "--arm", arm]


def tool_live(p):
    arm = p.get("arm", "left")
    live = bool(p.get("live"))
    confidence = p.get("confidence")
    cmd = []
    phone = _host(p)
    if arm == "both":
        cmd = ["punch_live_both.py"]
        if phone:
            cmd += ["--right-phone", phone]
        title = "Play with both arms"
    else:
        arm = _arm(p)
        cmd = ["punch_live.py", "--arm", arm]
        if phone and arm == "right":
            cmd += ["--phone", phone]
        title = f"Play with the {arm} arm"
    if confidence not in (None, ""):
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            raise BadRequest("Confidence must be a number between 0.05 and 0.99.")
        if not 0.05 <= value <= 0.99:
            raise BadRequest("Confidence must be between 0.05 and 0.99.")
        cmd += ["--confidence", str(value)]
    # One set of keys for everything: play always uses mappings/controls.json, the file the Controls page, the desktop
    # key editor and punch_live.py from a terminal use too. A game's ready-made layout is copied into it with
    # /api/controls/preset, so what the page shows is always what gets pressed.
    if live:
        cmd += ["--live", "--keys-on"]  # the page already asked "Press keys for real?", so don't also wait for F8
    else:
        title += " (practice: no keys pressed)"
    if p.get("popup"):
        cmd.append("--popup")  # a small window in the screen corner, over the game, as well as the page
    _common_window(p, cmd)
    return title, cmd


def tool_sessions(p):
    action = p.get("action")
    ids = p.get("sessions")
    if action not in ("delete", "restore"):
        raise BadRequest("Choose delete or restore.")
    if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(isinstance(i, str) and SESSION.fullmatch(i) for i in ids):
        raise BadRequest("Choose at least one session.")
    import punch_sessions
    known = punch_sessions.scan(deleted=(action == "restore"))
    missing = [i for i in ids if i not in known]
    if missing:
        raise BadRequest(f"No such session: {', '.join(missing)}. Refresh the list.")
    cmd = ["punch_sessions.py", "--delete" if action == "delete" else "--restore", *ids, "--yes"]
    if not p.get("retrain", True):
        cmd.append("--no-train")
    return ("Delete" if action == "delete" else "Restore") + f" {len(ids)} session(s)", cmd


def tool_controls_editor(p):
    return "Desktop key editor", ["controls_editor.py"]


TOOLS = {
    "calibrate": tool_calibrate, "glove_check": tool_glove_check, "capture": tool_capture, "train": tool_train,
    "live": tool_live, "sessions": tool_sessions, "controls_editor": tool_controls_editor,
}


# ----------------------------------------------------------------------------------------------------
# Running one tool at a time
# ----------------------------------------------------------------------------------------------------
class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.title = ""
        self.tool = ""
        self.started = 0.0
        self.exit_code = None
        self.lines = deque(maxlen=LOG_LINES)
        self.count = 0          # lines ever written, so the page can ask for only the new ones
        self.stopped_by_user = False
        self.view_port = None   # where the running camera tool serves its picture (web_view.py), or None

    def _write(self, text):
        with self.lock:
            self.lines.append(text)
            self.count += 1

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, tool, title, cmd):
        if self.running():
            raise BadRequest(f"“{self.title}” is still running. Stop it first, or finish it in its own window.")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        env.pop(web_view.ENV, None)
        self.view_port = free_port() if tool in CAMERA_TOOLS else None
        if self.view_port:
            env[web_view.ENV] = str(self.view_port)  # the camera picture comes to this page, not to its own window
        with self.lock:
            self.lines.clear()
            self.count = 0
            self.exit_code = None
            self.stopped_by_user = False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no extra console; a tool's camera window still opens
        self.proc = subprocess.Popen([sys.executable, "-u", *cmd], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
                                     text=True, encoding="utf-8", errors="replace")
        self.title, self.tool, self.started = title, tool, time.time()
        self._write("> " + " ".join(["python", *cmd]))
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            self._write(line.rstrip("\r\n"))
        code = proc.wait()
        for pipe in (proc.stdout, proc.stdin):  # let go of both ends once the tool has ended
            try:
                pipe.close()
            except OSError:
                pass
        with self.lock:
            self.exit_code = code
        self._write(f"[finished: {'stopped by you' if self.stopped_by_user else 'exit code ' + str(code)}]")

    def send(self, line):
        """Type a line into the running tool, as if at its terminal (capture's x, discard and q; tune's 350 and test ...)."""
        if not self.running() or self.tool not in TYPED_TOOLS:
            raise BadRequest("Nothing is running that takes typed commands.")
        if not COMMAND.fullmatch(line):
            raise BadRequest("Commands are short: letters, numbers, spaces, dots and equals only.")
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except OSError:
            raise BadRequest("That tool has just closed.")
        self._write("< " + line)

    def stop(self):
        if not self.running():
            return False
        self.stopped_by_user = True
        if os.name == "nt":
            # Stop the tool and anything it started (punch_live_both's keybind window). Plain terminate() kills only
            # the tool, which then never gets to close the windows it opened.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            self.proc.terminate()
        return True

    def snapshot(self, since=0):
        with self.lock:
            lines = list(self.lines)
            first = self.count - len(lines)
            since = max(since, first)
            return {"running": self.running(), "tool": self.tool, "title": self.title, "started": self.started,
                    "exit_code": self.exit_code, "total": self.count, "typed": self.tool in TYPED_TOOLS,
                    "camera": bool(self.view_port and self.running()), "from": since, "lines": lines[since - first:]}

    def camera_port(self):
        if not (self.view_port and self.running()):
            raise BadRequest("No camera tool is running.")
        return self.view_port


JOBS = Jobs()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def send_key(name):
    """Pass a key pressed in the page's camera view to the running camera tool, as if pressed in its window."""
    if not isinstance(name, str) or web_view.key_code(name) is None:
        raise BadRequest("That key does nothing in the camera view.")
    port = JOBS.camera_port()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("POST", "/key", body=name.encode("utf-8"), headers={"Content-Type": "text/plain"})
        if conn.getresponse().status != 204:
            raise BadRequest("The camera tool did not take that key.")
    except OSError:
        raise BadRequest("The camera tool is still starting. Try again in a moment.")
    finally:
        conn.close()


# ----------------------------------------------------------------------------------------------------
# What the dashboard shows
# ----------------------------------------------------------------------------------------------------
_model_cache = {}


def model_info(arm):
    path = arm.model_path
    if not os.path.exists(path):
        return {"exists": False}
    stamp = os.path.getmtime(path)
    cached = _model_cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    info = {"exists": True, "modified": stamp}
    try:
        import joblib
        bundle = joblib.load(path)
        info.update({"trained_at": bundle.get("trained_at"), "counts": bundle.get("counts"),
                     "cv_accuracy": bundle.get("cv_accuracy"), "feature_set": bundle.get("feature_set"),
                     "labels": [str(x) for x in bundle.get("labels", [])]})
    except Exception as e:  # a damaged or incompatible model must not break the whole page
        info["error"] = f"{type(e).__name__}: {e}"
    _model_cache[path] = (stamp, info)
    return info


def recording_counts(arm):
    counts = Counter()
    for path in ps.block_paths(arm.data_dir):
        counts[os.path.basename(os.path.dirname(path))] += 1
    return dict(counts)


def laptop_addresses():
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:  # the address used to reach the internet; connecting a UDP socket sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("203.0.113.1", 9))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def status():
    arms = {}
    for name, arm in ps.ARMS.items():
        arms[name] = {"labels": list(arm.labels), "how": arm.how, "port": arm.port, "recordings": recording_counts(arm),
                      "model": model_info(arm)}
    return {"arms": arms, "addresses": laptop_addresses(), "job": JOBS.snapshot(10 ** 9),
            "controls_exists": os.path.exists(CONTROLS_PATH)}


def sessions(deleted=False):
    import punch_sessions
    out = []
    for sid, s in punch_sessions.scan(deleted=deleted).items():
        out.append({"id": sid, "person": s["person"], "arms": {a: dict(c) for a, c in s["arms"].items()}})
    return out


# ----------------------------------------------------------------------------------------------------
# Key mapping: the page edits the normal punches and the defence keys. Everything else in the file
# (tracking binds, mouse aim, notes) is kept exactly as it is.
# ----------------------------------------------------------------------------------------------------
def read_controls():
    with open(CONTROLS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    defence = data.setdefault("defence", {})
    for action in gi.DEFENCE:  # every defence move is listed, so one with no key can be given one again
        defence.setdefault(action, "")
    return data


def clean_bind(value, allow_none=False):
    if not isinstance(value, str):
        raise BadRequest("A key must be text, such as j or space.")
    if allow_none and not value.strip():
        return ""  # no key: the move presses nothing (such as BLOCK in Street Fighter, which blocks by holding back)
    try:
        return gi.check_key(value.strip())
    except ValueError as e:
        raise BadRequest(str(e))


def write_controls(data):
    tmp = CONTROLS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONTROLS_PATH)  # so punch_live never reads a half-written file


def apply_preset(body):
    """Copy a game's ready-made key layout (mappings/presets/<name>.json) into the one controls file."""
    name = body.get("preset")
    path = os.path.join(PRESETS_DIR, f"{name}.json") if isinstance(name, str) and PRESET.fullmatch(name) else None
    if not path or not os.path.isfile(path):
        raise BadRequest("That game's key layout was not found.")
    with open(path, encoding="utf-8") as f:
        preset = json.load(f)
    data = read_controls()
    for action, spec in preset.get("normal", {}).items():
        if action in data["normal"]:
            spec = spec if isinstance(spec, dict) else {"bind": spec}
            data["normal"][action]["bind"] = clean_bind(spec.get("bind", ""))
            data["normal"][action]["hold_ms"] = spec.get("hold_ms", data["normal"][action].get("hold_ms", 50))
    data["defence"] = {a: clean_bind(preset.get("defence", {}).get(a, ""), allow_none=True) for a in gi.DEFENCE}
    write_controls(data)
    return data


def save_controls(body):
    data = read_controls()
    normal, defence = body.get("normal"), body.get("defence")
    if not isinstance(normal, dict) or not isinstance(defence, dict):
        raise BadRequest("Send the normal punches and the defence keys.")
    for action, spec in normal.items():
        if action not in data.get("normal", {}):
            raise BadRequest(f"Unknown punch {action!r}.")
        if not isinstance(spec, dict):
            raise BadRequest(f"{action}: send a key and a hold time.")
        data["normal"][action]["bind"] = clean_bind(spec.get("bind"))
        hold = spec.get("hold_ms", data["normal"][action].get("hold_ms", 50))
        if not isinstance(hold, int) or isinstance(hold, bool) or not 10 <= hold <= 1000:
            raise BadRequest(f"{action}: hold time must be a whole number of milliseconds from 10 to 1000.")
        data["normal"][action]["hold_ms"] = hold
    for action, key in defence.items():
        if action not in data["defence"]:
            raise BadRequest(f"Unknown defence action {action!r}.")
        data["defence"][action] = clean_bind(key, allow_none=True)
    write_controls(data)
    # Two punches on one key is allowed (Punch-Out!! has only two punch buttons), but usually a mistake, so say so.
    punches = [v["bind"] for v in data["normal"].values()]
    repeated = sorted({k for k in punches if punches.count(k) > 1})
    warning = (f"{', '.join(k.upper() for k in repeated)} is used by more than one punch, so the game can't tell "
               "those punches apart. That's fine for a game with fewer punch buttons.") if repeated else ""
    return dict(data, warning=warning)


# ----------------------------------------------------------------------------------------------------
# Games: key layouts for particular games, and shortcuts that open them
# ----------------------------------------------------------------------------------------------------
def read_presets():
    out = {}
    for path in sorted(glob_presets()):
        name = os.path.splitext(os.path.basename(path))[0]
        if not PRESET.fullmatch(name):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        out[name] = {"game": data.get("game", name), "normal": data.get("normal", {}), "defence": data.get("defence", {}),
                     "notes": data.get("notes", [])}
    return out


def glob_presets():
    import glob
    return glob.glob(os.path.join(PRESETS_DIR, "*.json"))


def launchable_games():
    """{game id: its launch settings} for the games on the page that are opened by a program on this computer."""
    try:
        with open(GAMES_PATH, encoding="utf-8") as f:
            games = json.load(f)["games"]
    except (OSError, ValueError, KeyError):
        return {}
    return {g["id"]: g["launch"] for g in games if isinstance(g.get("launch"), dict) and g.get("id")}


def need_game(body_or_query):
    game = body_or_query
    games = launchable_games()
    if game not in games:
        raise BadRequest("That game does not have a shortcut.")
    return games[game]


def launchers_status():
    saved = game_launcher.load(LAUNCHERS_PATH)
    return {"games": {g: saved.get(g) for g in launchable_games()}}


def save_launcher(body):
    need_game(body.get("game"))
    config = game_launcher.load(LAUNCHERS_PATH)
    if body.get("clear"):
        config.pop(body["game"], None)
    else:
        try:
            config[body["game"]] = game_launcher.clean_entry(body.get("kind"), body.get("value"), body.get("label"))
        except ValueError as e:
            raise BadRequest(str(e))
    game_launcher.save(config, LAUNCHERS_PATH)
    return launchers_status()


def open_game(body):
    need_game(body.get("game"))
    entry = game_launcher.load(LAUNCHERS_PATH).get(body["game"])
    if not entry:
        raise BadRequest("Set up this game's shortcut first.")
    try:
        game_launcher.launch(entry)
    except OSError as e:
        raise BadRequest(str(e))
    return {"ok": True, "label": entry.get("label", "")}


# ----------------------------------------------------------------------------------------------------
# The web server
# ----------------------------------------------------------------------------------------------------
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")


class Handler(SimpleHTTPRequestHandler):
    server_version = "BoxingPanel"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE, **kwargs)

    def log_message(self, fmt, *args):
        pass  # the panel is not a log of every poll

    # --- who may ask
    def _local_names(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _allowed(self):
        if (self.headers.get("Host") or "").lower() not in self._local_names():
            return False  # a name pointing at 127.0.0.1 (DNS rebinding) is refused
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc.lower() in self._local_names()

    def _refuse(self, status=403, message="This page only answers requests from itself."):
        self._json({"error": message}, status=status)

    def _json(self, value, status=200):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        path = urlparse(self.path).path
        if path == "/" or path.startswith("/ui/") or path == "/index.html":
            self.send_header("Content-Security-Policy", CSP)   # the sparring game needs more, so only the panel gets it
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # --- GET
    def do_GET(self):
        if not self._allowed():
            return self._refuse()
        parts = urlparse(self.path)
        path = parts.path
        try:
            if path == "/api/status":
                return self._json(status())
            if path == "/api/camera":
                return self._camera_stream()
            if path == "/api/job":
                since = int(re.search(r"since=(\d+)", parts.query).group(1)) if re.search(r"since=(\d+)", parts.query) else 0
                return self._json(JOBS.snapshot(since))
            if path == "/api/sessions":
                return self._json({"sessions": sessions(), "deleted": sessions(deleted=True)})
            if path == "/api/controls":
                return self._json(read_controls())
            if path == "/api/presets":
                return self._json(read_presets())
            if path == "/api/launchers":
                return self._json(launchers_status())
            if path == "/api/launchers/find":
                match = re.search(r"game=([a-z0-9-]+)", parts.query)
                launch = need_game(match.group(1) if match else None)
                return self._json({"candidates": game_launcher.find(launch.get("search", []))})
            if path.startswith("/api/"):
                return self._refuse(404, "No such page.")
        except BadRequest as e:
            return self._refuse(400, str(e))
        except Exception as e:
            return self._refuse(500, f"{type(e).__name__}: {e}")
        super().do_GET()

    def _camera_stream(self):
        """Relay the running camera tool's picture stream (web_view.py) to the page's <img>."""
        port = JOBS.camera_port()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/stream")
            upstream = conn.getresponse()
        except OSError:
            conn.close()
            raise BadRequest("The camera tool is still starting.")
        try:
            if upstream.status != 200:
                raise BadRequest("The camera tool is not showing a picture yet.")
            self.send_response(200)
            self.send_header("Content-Type", upstream.getheader("Content-Type", "application/octet-stream"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break  # the tool ended
                self.wfile.write(chunk)
        except (ConnectionError, OSError):
            pass  # the page went away, or the tool ended mid-picture
        finally:
            conn.close()

    # --- POST
    def do_POST(self):
        if not self._allowed():
            return self._refuse()
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self._refuse(415, "Send JSON.")  # a plain form or image request from another site cannot do this
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._refuse(413, "Too much data.")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise BadRequest("Send a JSON object.")
            path = urlparse(self.path).path
            if path == "/api/run":
                tool = body.get("tool")
                if tool not in TOOLS:
                    raise BadRequest("Unknown tool.")
                title, cmd = TOOLS[tool](body)
                JOBS.start(tool, title, cmd)
                return self._json({"ok": True, "title": title})
            if path == "/api/input":
                JOBS.send(str(body.get("line", "")).strip())
                return self._json({"ok": True})
            if path == "/api/camera/key":
                send_key(body.get("key"))
                return self._json({"ok": True})
            if path == "/api/launchers":
                return self._json(save_launcher(body))
            if path == "/api/launch":
                return self._json(open_game(body))
            if path == "/api/stop":
                return self._json({"ok": True, "stopped": JOBS.stop()})
            if path == "/api/controls":
                return self._json(save_controls(body))
            if path == "/api/controls/preset":
                return self._json(apply_preset(body))
            return self._refuse(404, "No such page.")
        except BadRequest as e:
            return self._refuse(400, str(e))
        except ValueError:
            return self._refuse(400, "That was not valid JSON.")
        except Exception as e:
            return self._refuse(500, f"{type(e).__name__}: {e}")


def make_server(port=DEFAULT_PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    args = parser.parse_args()
    try:
        server = make_server(args.port)
    except OSError as e:
        raise SystemExit(f"Could not use port {args.port}: {e}. Try --port {args.port + 1}.")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Control panel at {url}  (press Ctrl+C to stop; tools it started are stopped too)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        JOBS.stop()
        server.server_close()


if __name__ == "__main__":
    main()
