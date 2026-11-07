"""Key bridge for the Keyboard Warrior website: turns the page's punches into real key presses.

  python keybridge.py                  dry run: serves the site and prints the keys it would press
  python keybridge.py --live           really presses keys in whichever window has focus (Windows)
  python keybridge.py --live --glove   also listen for the glove on UDP port 5005

Why it exists: a web page cannot press keys in another window, and Browser Boxing cannot be shown inside
a page. The page tracks you with the webcam and sends this program the punch and body-state names; this
program presses the keys the mapping file gives them (game_input.py, the same engine controller.py uses).
It also serves the website, so http://127.0.0.1:8765/ is all you need.

Hotkeys, from any window, like controller.py:  F7 calibrate, F8 keys on or off, F9 keys off.

Safety:
  - It listens on 127.0.0.1 only, and only answers pages from this computer or keyboardwarrior.club
    (add more with --allow-origin). Other websites you have open cannot make it press keys.
  - The page can only ask for the actions in the mapping file, never an arbitrary key.
  - Key output starts OFF. Held keys are released when the page goes quiet for 1.5 s, when output is
    turned off, and when this program ends.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from urllib.parse import urlparse

import controller
import game_input

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "website")
DEFAULT_PORT = 8765
LOOPBACK = {"127.0.0.1", "localhost"}
SITE_ORIGINS = {"https://keyboardwarrior.club", "https://www.keyboardwarrior.club"}
QUIET_SECONDS = 1.5  # the page sends its body states at least every 0.4 s; silence this long means it is gone


class Bridge:
    """The game input, plus everything the web page is allowed to ask for."""

    def __init__(self, game, live, glove=None):
        self.game, self.live, self.glove = game, live, glove
        self.lock = threading.RLock()
        self.listeners = []          # one Queue per open /bridge/events connection
        self.held = []               # body-state labels whose keys are held right now
        self.last_states_at = time.time()

    def status(self):
        gloves = sorted(self.glove.receiver.samples) if self.glove else []
        return {
            "app": "keybridge", "version": 1, "live": self.live, "output": self.game.enabled,
            "game": self.game.mapping.game, "taps": self.game.mapping.taps, "holds": self.game.mapping.holds,
            "glove_enabled": self.glove is not None, "gloves": gloves,
        }

    # --- what the page may ask for ---
    def punch(self, kind):
        with self.lock:
            return self.game.punch(kind)

    def set_states(self, states):
        holds = self.game.mapping.holds
        labels = [s.upper() for s in states if isinstance(s, str) and s.upper() in holds]
        with self.lock:
            self.last_states_at = time.time()
            self.held = labels
            self.game.set_states(labels)

    def set_output(self, enabled):
        with self.lock:
            self.game.set_enabled(bool(enabled))
            if not enabled:
                self.held = []
        self.broadcast({"type": "output", "enabled": self.game.enabled})

    def release_if_quiet(self):
        """The page crashed or its tab was closed: do not leave keys held."""
        with self.lock:
            if self.held and time.time() - self.last_states_at > QUIET_SECONDS:
                self.held = []
                self.game.set_states(())
                print("The page went quiet: keys released.")

    # --- events to the page ---
    def subscribe(self):
        queue = Queue()
        with self.lock:
            self.listeners.append(queue)
        return queue

    def unsubscribe(self, queue):
        with self.lock:
            if queue in self.listeners:
                self.listeners.remove(queue)

    def broadcast(self, event):
        with self.lock:
            for queue in self.listeners:
                queue.put(event)


def watch_hotkeys(bridge, stop):
    hotkeys = controller.Hotkeys()
    while not stop.is_set():
        if hotkeys.pressed(controller.VK_F7):
            print("F7: calibrate")
            bridge.broadcast({"type": "hotkey", "name": "calibrate"})
        if hotkeys.pressed(controller.VK_F8):
            bridge.set_output(not bridge.game.enabled)
            print("F8: keys " + ("ON" if bridge.game.enabled else "OFF"))
        if hotkeys.pressed(controller.VK_F9):
            bridge.set_output(False)
            print("F9: keys OFF")
        bridge.release_if_quiet()
        stop.wait(0.02)


def watch_glove(bridge, stop):
    """Tell the page when a glove is felt, using glove_punch.py exactly as controller.py does."""
    glove = bridge.glove
    active, present = {}, set()
    while not stop.is_set():
        try:
            found = glove.poll()
        except RuntimeError:  # a buffer changed while it was being copied; try again next tick
            stop.wait(0.005)
            continue
        now = time.time()
        for side, punch in found:
            bridge.broadcast({"type": "glove", "side": side, "punch": True, "age": max(0.0, now - punch.t),
                              "peak": round(punch.peak_g, 1)})
        for side, detector in list(glove.detectors.items()):
            if detector.active != active.get(side):
                active[side] = detector.active
                bridge.broadcast({"type": "glove", "side": side, "active": detector.active})
        heard = set(glove.receiver.samples)
        if heard != present:
            present = heard
            bridge.broadcast({"type": "glove", "gloves": sorted(present)})
        stop.wait(0.01)


class Handler(SimpleHTTPRequestHandler):
    bridge = None
    extra_origins = set()
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".js": "text/javascript",
                      ".wasm": "application/wasm", ".json": "application/json"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE, **kwargs)

    def log_message(self, format, *args):  # keep the console for key events
        pass

    # --- who may talk to us ---
    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in LOOPBACK  # a page on another name pointing at 127.0.0.1 (DNS rebinding) is refused

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # same-origin page, or a program on this computer
        if origin in SITE_ORIGINS or origin in self.extra_origins:
            return True
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in LOOPBACK

    def _allowed(self):
        if self._host_ok() and self._origin_ok():
            return True
        self.send_error(403, "Forbidden")
        return False

    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    # --- requests ---
    def _json(self, payload, status=200, headers=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        if self._allowed():
            self.send_response(204)
            self.end_headers()

    def do_HEAD(self):
        if self._allowed():
            super().do_HEAD()

    def do_GET(self):
        if not self._allowed():
            return
        path = urlparse(self.path).path
        if path == "/bridge/status":
            return self._json(self.bridge.status())
        if path == "/bridge/events":
            return self._events()
        if path.startswith("/bridge/"):
            return self.send_error(404)
        live = re.fullmatch(r"/settings/([a-z0-9_]+)\.json", path)
        if live:  # tuning in variants/<name>/settings.json is picked up on reload
            source = os.path.join(ROOT, "variants", live.group(1), "settings.json")
            if os.path.isfile(source):
                try:
                    with open(source, encoding="utf-8") as f:
                        values = json.load(f)
                except ValueError as e:
                    return self._json({"error": f"{source} is not valid JSON: {e}"}, status=500)
                return self._json(values, headers={"X-Settings-Source": f"variants/{live.group(1)}/settings.json (live)"})
        super().do_GET()

    def do_POST(self):
        if not self._allowed():
            return
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                return self.send_error(413)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_error(400)
        if not isinstance(body, dict):
            return self.send_error(400)
        if path == "/bridge/punch":
            return self._json({"ok": self.bridge.punch(str(body.get("kind", "")))})
        if path == "/bridge/states":
            states = body.get("states")
            if not isinstance(states, list):
                return self.send_error(400)
            self.bridge.set_states(states)
            return self._json({"ok": True})
        if path == "/bridge/output":
            self.bridge.set_output(bool(body.get("enabled")))
            return self._json({"ok": True, "output": self.bridge.game.enabled})
        self.send_error(404)

    def _events(self):
        """A stream of events for the page (Server-Sent Events). A status message every second is the heartbeat."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        queue = self.bridge.subscribe()
        try:
            self._send_event({"type": "status", "status": self.bridge.status()})
            while True:
                try:
                    event = queue.get(timeout=1.0)
                except Empty:
                    event = {"type": "status", "status": self.bridge.status()}
                self._send_event(event)
        except OSError:  # the page closed the connection
            pass
        finally:
            self.bridge.unsubscribe(queue)

    def _send_event(self, event):
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mapping", default=os.path.join(ROOT, "mappings", "browser_boxing.json"))
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--glove", action="store_true", help="listen for the glove on UDP port 5005")
    parser.add_argument("--simulate-glove", action="store_true", help="like --glove, with a fake glove that punches every 1.5 s")
    parser.add_argument("--allow-origin", action="append", default=[], metavar="URL",
                        help="also accept a page from this origin, such as https://my-preview.netlify.app")
    parser.add_argument("--no-browser", action="store_true", help="do not open the site in the browser")
    args = parser.parse_args()

    mapping = game_input.Mapping.load(args.mapping)
    try:
        keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
    except OSError as e:
        raise SystemExit(f"--live does not work here: {e}")
    game = game_input.GameInput(mapping, keyboard)
    game.set_enabled(False)  # always starts OFF; the page's switch or F8 turns it on

    glove = None
    if args.glove or args.simulate_glove:
        import glove_punch
        import glove_receiver
        try:
            receiver = glove_receiver.GloveReceiver()
        except OSError as e:
            raise SystemExit(f"Could not listen for the glove on UDP port {glove_receiver.PORT}: {e}")
        if args.simulate_glove:
            threading.Thread(target=glove_receiver.simulate, daemon=True).start()
        glove = glove_punch.GlovePunches(receiver)

    bridge = Bridge(game, args.live, glove)
    Handler.bridge = bridge
    Handler.extra_origins = set(args.allow_origin)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        raise SystemExit(f"Could not use port {args.port}: {e}. Is the bridge already running? Try --port.")
    server.daemon_threads = True

    stop = threading.Event()
    threading.Thread(target=watch_hotkeys, args=(bridge, stop), daemon=True).start()
    if glove:
        threading.Thread(target=watch_glove, args=(bridge, stop), daemon=True).start()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Game: {mapping.game}")
    print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed, keys are only printed.")
    print("Glove: listening on UDP port 5005." if glove else "No glove (add --glove if you have one).")
    print(f"Website: {url}   (keyboardwarrior.club can find this bridge too)")
    print("Keys start OFF. Turn them on in the page or with F8. F7 calibrates, F9 turns keys off. Ctrl+C quits.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        game.close()  # lets go of every key
        print("Bridge stopped, keys released.")


if __name__ == "__main__":
    main()
