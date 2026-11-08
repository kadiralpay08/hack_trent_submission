"""The camera picture shown in the control panel page instead of an OpenCV window (web_view.py, app_server.py).

No webcam: a stand-in camera tool (a few lines of Python) publishes a black picture and prints the keys it gets."""
import http.client
import json
import os
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_server  # noqa: E402
import web_view  # noqa: E402

FAKE_TOOL = """
import time, numpy as np, web_view
view = web_view.from_env()
print("view", bool(view), flush=True)
frame = np.zeros((48, 64, 3), np.uint8)
end = time.time() + 30
while time.time() < end:
    view.show(frame)
    key = view.key()
    if key != 255:
        print("key", key, flush=True)
    if key == ord("q"):
        break
    time.sleep(0.02)
view.close()
"""


def http_call(port, method, path, body=None, host=None, content_type="text/plain"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": content_type}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    return response, conn


class WebViewOnItsOwn(unittest.TestCase):
    def setUp(self):
        self.view = web_view.WebView(app_server.free_port())
        self.port = self.view.server.server_address[1]

    def tearDown(self):
        self.view.close()

    def test_a_picture_is_served_as_a_jpeg(self):
        self.view.show(np.full((48, 64, 3), 200, np.uint8))
        response, conn = http_call(self.port, "GET", "/frame.jpg")
        data = response.read()
        conn.close()
        self.assertEqual((response.status, data[:2]), (200, b"\xff\xd8"))

    def test_keys_from_the_page_reach_the_tool_as_waitkey_codes(self):
        for name, code in (("c", ord("c")), ("C", ord("c")), (".", ord(".")), ("Enter", 13), ("Backspace", 8)):
            response, conn = http_call(self.port, "POST", "/key", name.encode())
            response.read()
            conn.close()
            self.assertEqual(response.status, 204, name)
            self.assertEqual(self.view.key(), code, name)
        self.assertEqual(self.view.key(), 255)              # nothing waiting

    def test_unknown_keys_and_other_hosts_are_refused(self):
        response, conn = http_call(self.port, "POST", "/key", b"F12")
        response.read(), conn.close()
        self.assertEqual(response.status, 400)
        response, conn = http_call(self.port, "GET", "/frame.jpg", host="evil.example:80")
        response.read(), conn.close()
        self.assertEqual(response.status, 403)


class ThroughThePanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app_server.make_server(0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        app_server.JOBS.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def api(self, method, path, body=None):
        response, conn = http_call(self.port, method, path, json.dumps(body) if body is not None else None,
                                   content_type="application/json")
        data = response.read()
        conn.close()
        return response.status, json.loads(data) if data[:1] == b"{" else data

    def wait_for_line(self, text, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            if any(text in line for line in app_server.JOBS.snapshot()["lines"]):
                return True
            time.sleep(0.05)
        return False

    def test_a_camera_tool_shows_in_the_page_and_takes_keys(self):
        status, _ = self.api("GET", "/api/camera")
        self.assertEqual(status, 400)                              # nothing running yet
        app_server.JOBS.start("live", "Fake camera tool", ["-c", FAKE_TOOL])
        self.assertTrue(self.wait_for_line("view True"))           # the tool was told to show itself in the page
        self.assertTrue(app_server.JOBS.snapshot()["camera"])

        response, conn = http_call(self.port, "GET", "/api/camera", content_type="application/json")
        self.assertEqual(response.status, 200)
        self.assertIn("multipart/x-mixed-replace", response.getheader("Content-Type"))
        self.assertIn(b"\xff\xd8", response.read1(65536) + response.read1(65536))  # a JPEG arrived
        conn.close()

        self.assertEqual(self.api("POST", "/api/camera/key", {"key": "c"})[0], 200)
        self.assertTrue(self.wait_for_line(f"key {ord('c')}"))
        self.assertEqual(self.api("POST", "/api/camera/key", {"key": "F12"})[0], 400)
        self.api("POST", "/api/camera/key", {"key": "q"})
        end = time.time() + 10
        while app_server.JOBS.running() and time.time() < end:
            time.sleep(0.05)
        self.assertFalse(app_server.JOBS.snapshot()["camera"])     # gone from the page when the tool ends

    def test_play_can_also_have_the_corner_popup(self):
        _, cmd = app_server.tool_live({"arm": "both", "popup": True})
        self.assertIn("--popup", cmd)
        _, cmd = app_server.tool_live({"arm": "both"})
        self.assertNotIn("--popup", cmd)

    def test_one_picture_can_be_finished_for_the_page_and_the_popup(self):
        import overlay
        frame = np.zeros((480, 640, 3), np.uint8)
        overlay.begin_layer(frame)
        overlay.outlined_text(frame, "JAB", 100, 200, 2.0, (0, 255, 0), 4)
        page = overlay.finish_layer(frame, *web_view.SIZE, keep=True)
        self.assertFalse(frame.any())                              # the page's copy did not draw on the frame
        window = overlay.finish_layer(frame, 640, 480)
        self.assertTrue(page.any() and window.any())               # both have the text
        self.assertEqual(page.shape[:2], (720, 960))

    def test_tools_without_a_camera_are_not_given_a_view(self):
        jobs = app_server.Jobs()
        jobs.start("train", "Not a camera tool", ["-c", "import os; print('env', os.environ.get('BOXING_WEB_VIEW'))"])
        end = time.time() + 20
        while jobs.running() and time.time() < end:
            time.sleep(0.05)
        self.assertIn("env None", jobs.snapshot()["lines"])
        self.assertFalse(jobs.snapshot()["camera"])


if __name__ == "__main__":
    unittest.main()
