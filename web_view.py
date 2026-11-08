"""Show a camera tool inside the control panel page (app_server.py) instead of in an OpenCV window.

When app_server.py starts a camera tool it sets BOXING_WEB_VIEW to a free port. pose_tracker.main() then sends
each finished camera picture (with all its text) here instead of to cv2.imshow, and takes its key presses from
here instead of cv2.waitKey. Only the page's own server talks to this: it listens on 127.0.0.1, answers only
Host 127.0.0.1:<port>, and app_server.py relays the picture to the page and the keys from it.

  GET  /stream     the picture as a stream of JPEGs (multipart/x-mixed-replace), for an <img>
  GET  /frame.jpg  the newest picture once
  POST /key        body: one key name (see KEYS); queued for the tool, like a key pressed in its window

Run without BOXING_WEB_VIEW, every tool still opens its own window as before.
"""
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENV = "BOXING_WEB_VIEW"
SIZE = (960, 720)          # the picture is drawn at this size, so its text is sharp in the page
JPEG_QUALITY = 75
IDLE_S = 2.0               # no viewer for this long: stop encoding pictures nobody sees
BOUNDARY = "frame"
# Key names the page may send, and the 8-bit codes cv2.waitKey would give for them.
NAMED = {"Enter": 13, "Backspace": 8, "Escape": 27, " ": 32}


def key_code(name):
    """The cv2.waitKey code for a key name from the page, or None if it is not one the tools use."""
    if name in NAMED:
        return NAMED[name]
    if isinstance(name, str) and len(name) == 1 and (name.isalnum() or name in "._-"):
        return ord(name.lower())
    return None


class WebView:
    def __init__(self, port):
        self.keys = queue.Queue()
        self.cond = threading.Condition()
        self.jpeg, self.seq, self.watched = None, 0, 0.0
        self.closed = False
        view = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _local(self):
                return (self.headers.get("Host") or "") == f"127.0.0.1:{self.server.server_address[1]}"

            def do_GET(self):
                if not self._local():
                    return self.send_error(403)
                if self.path == "/frame.jpg":
                    view.watched = time.time()
                    jpeg = view.wait_frame(-1, 2.0)[1]
                    if jpeg is None:
                        return self.send_error(503)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(jpeg)
                    return
                if self.path != "/stream":
                    return self.send_error(404)
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seq = -1
                try:
                    while not view.closed:
                        view.watched = time.time()
                        seq, jpeg = view.wait_frame(seq, 1.0)
                        if jpeg is None:
                            continue
                        self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                                         .encode() + jpeg + b"\r\n")
                except (ConnectionError, OSError):
                    pass  # the page closed or reloaded

            def do_POST(self):
                if not self._local() or self.path != "/key":
                    return self.send_error(403)
                length = min(int(self.headers.get("Content-Length") or 0), 32)
                code = key_code(self.rfile.read(length).decode("utf-8", "replace"))
                if code is None:
                    return self.send_error(400)
                view.keys.put(code)
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait_frame(self, seen, timeout):
        """(seq, jpeg) of a picture newer than seq `seen`, waiting up to timeout; jpeg None if none came."""
        with self.cond:
            if self.seq == seen or self.jpeg is None:
                self.cond.wait(timeout)
            if self.seq == seen:
                return seen, None
            return self.seq, self.jpeg

    def show(self, frame):
        """Publish a finished picture (BGR). Skipped while nobody is watching, to save the work."""
        if time.time() - self.watched > IDLE_S and self.jpeg is not None:
            return
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            with self.cond:
                self.jpeg, self.seq = buf.tobytes(), self.seq + 1
                self.cond.notify_all()

    def key(self):
        """The next key pressed in the page as a cv2.waitKey code, or 255 for none."""
        try:
            return self.keys.get_nowait()
        except queue.Empty:
            return 255

    def close(self):
        self.closed = True
        with self.cond:
            self.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()


_active = None


def from_env():
    """The WebView to use if app_server.py asked for one (BOXING_WEB_VIEW=port), else None."""
    global _active
    port = os.environ.get(ENV, "").strip()
    if not port.isdigit():
        return None
    _active = WebView(int(port))
    return _active


def active():
    """True while a tool is showing itself in the page (no OpenCV window exists then)."""
    return _active is not None
