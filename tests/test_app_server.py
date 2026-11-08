"""Tests for app_server.py, the control panel's server: who may talk to it, what it may start, and the key mapping.

Nothing here starts a camera or a glove tool. Jobs are run with tiny Python one-liners, and the controls file is a
temporary copy, so the real mappings/controls.json is never touched.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_server  # noqa: E402


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.real_controls = app_server.CONTROLS_PATH
        app_server.CONTROLS_PATH = os.path.join(cls.tmp, "controls.json")
        shutil.copy(cls.real_controls, app_server.CONTROLS_PATH)
        cls.server = app_server.make_server(0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        app_server.CONTROLS_PATH = cls.real_controls
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": f"127.0.0.1:{self.port}"}
        data = raw
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        response = conn.getresponse()
        text = response.read()
        conn.close()
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = text
        return response.status, parsed, response


class WhoMayAsk(ServerCase):
    def test_a_foreign_host_name_is_refused(self):
        """DNS rebinding: another site's name pointing at 127.0.0.1 must not drive the panel."""
        status, _, _ = self.call("GET", "/api/status", headers={"Host": "evil.example:8770"})
        self.assertEqual(status, 403)

    def test_a_foreign_origin_cannot_post(self):
        status, _, _ = self.call("POST", "/api/stop", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_our_own_origin_can_post(self):
        status, _, _ = self.call("POST", "/api/stop", {}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_a_form_post_is_refused(self):
        """A plain cross-site form cannot set a JSON content type, so refusing everything else shuts it out."""
        status, _, _ = self.call("POST", "/api/run", raw="tool=train", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_garbage_json_is_a_clear_error(self):
        status, body, _ = self.call("POST", "/api/run", raw="{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)


class Pages(ServerCase):
    def test_the_panel_is_served_with_a_content_security_policy(self):
        status, body, response = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Skip to main content", body)
        self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))

    def test_the_sparring_game_page_still_exists_and_is_not_restricted(self):
        status, _, response = self.call("GET", "/sparring.html")
        self.assertEqual(status, 200)
        self.assertIsNone(response.getheader("Content-Security-Policy"))

    def test_the_games_list_is_valid(self):
        status, body, _ = self.call("GET", "/ui/games.json")
        self.assertEqual(status, 200)
        ids = [g["id"] for g in body["games"]]
        self.assertIn("street-fighter", ids)
        for g in body["games"]:
            self.assertTrue(g["steps"], g["id"])

    def test_status_describes_both_arms(self):
        status, body, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(set(body["arms"]), {"left", "right"})
        self.assertEqual(body["arms"]["right"]["labels"], ["CROSS", "HOOK", "UPPERCUT"])
        self.assertIn("running", body["job"])

    def test_sessions_lists_without_error(self):
        status, body, _ = self.call("GET", "/api/sessions")
        self.assertEqual(status, 200)
        self.assertIn("sessions", body)
        self.assertIn("deleted", body)

    def test_unknown_api_paths_are_404(self):
        status, _, _ = self.call("GET", "/api/nothing")
        self.assertEqual(status, 404)


class WhatMayRun(ServerCase):
    def run_tool(self, **body):
        return self.call("POST", "/api/run", body)

    def test_unknown_tools_are_refused(self):
        status, body, _ = self.run_tool(tool="rm")
        self.assertEqual(status, 400)
        self.assertIn("Unknown tool", body["error"])

    def test_options_are_checked(self):
        for bad in ({"tool": "capture", "arm": "left", "person": ""},
                    {"tool": "capture", "arm": "left", "person": "Sam", "target": 0},
                    {"tool": "capture", "arm": "left", "person": "Sam", "target": "many"},
                    {"tool": "capture", "arm": "left", "person": "Sam; calc"},
                    {"tool": "capture", "arm": "right", "person": "Sam", "phone": "1.2.3.4 && calc"},
                    {"tool": "capture", "arm": "middle", "person": "Sam"},
                    {"tool": "train", "arm": "../../etc"},
                    {"tool": "live", "arm": "left", "confidence": 5},
                    {"tool": "live", "arm": "left", "camera": 99},
                    {"tool": "sessions", "action": "delete", "sessions": ["../../x"]},
                    {"tool": "sessions", "action": "delete", "sessions": ["20990101_000000"]},
                    {"tool": "sessions", "action": "wipe", "sessions": ["a"]}):
            status, body, _ = self.run_tool(**bad)
            self.assertEqual(status, 400, bad)
            self.assertIn("error", body)

    def test_commands_are_built_from_checked_options(self):
        title, cmd = app_server.tool_capture({"arm": "right", "person": "Sam", "target": "25", "phone": "192.168.137.252"})
        self.assertEqual(cmd, ["punch_capture.py", "--target", "25", "--person", "Sam", "--arm", "right", "--phone", "192.168.137.252"])
        title, cmd = app_server.tool_capture({"arm": "both", "person": "Sam", "target": 10, "phone": "10.0.0.2", "windowed": True})
        self.assertEqual(cmd, ["punch_capture.py", "--target", "10", "--person", "Sam", "--both", "--right-phone", "10.0.0.2", "--windowed"])
        title, cmd = app_server.tool_capture({"arm": "left", "person": "Sam", "phone": "10.0.0.2"})
        self.assertNotIn("--phone", cmd, "a phone is for the right arm only")

    def test_playing_is_practice_unless_keys_are_asked_for(self):
        title, cmd = app_server.tool_live({"arm": "both"})
        self.assertEqual(cmd, ["punch_live_both.py"])
        self.assertIn("no keys pressed", title)
        title, cmd = app_server.tool_live({"arm": "left", "live": True, "confidence": "0.5", "camera": 1})
        # keys start on: the page already asked "Press keys for real?"
        self.assertEqual(cmd, ["punch_live.py", "--arm", "left", "--confidence", "0.5", "--live", "--keys-on", "--camera", "1"])
        title, cmd = app_server.tool_live({"arm": "right", "phone": "10.0.0.2", "live": False})
        self.assertNotIn("--live", cmd)

    def test_train_and_calibrate(self):
        self.assertEqual(app_server.tool_train({"arm": "right"})[1], ["punch_train.py", "--arm", "right"])
        self.assertEqual(app_server.tool_calibrate({})[1], ["punch_tune.py"])


class Controls(ServerCase):
    def current(self):
        return self.call("GET", "/api/controls")[1]

    def payload(self):
        data = self.current()
        return {"normal": {k: dict(v) for k, v in data["normal"].items()}, "defence": dict(data["defence"])}

    def test_six_punches_have_six_different_keys(self):
        keys = [v["bind"] for v in self.current()["normal"].values()]
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(set(keys)), 6)

    def test_saving_changes_a_key_and_keeps_everything_else(self):
        before = self.current()
        body = self.payload()
        body["normal"]["JAB"]["bind"] = "F"
        status, after, _ = self.call("POST", "/api/controls", body)
        self.assertEqual(status, 200)
        self.assertEqual(after["normal"]["JAB"]["bind"], "f", "keys are stored in lower case")
        self.assertEqual(after["tracking"], before["tracking"])
        self.assertEqual(after["aim"], before["aim"])
        self.assertEqual(after["notes"], before["notes"])
        self.assertEqual(self.current()["normal"]["JAB"]["bind"], "f")

    def test_two_punches_may_share_a_key_with_a_warning(self):
        """Allowed for a game with fewer punch buttons (Punch-Out!! has two), but the page is told."""
        body = self.payload()
        body["normal"]["JAB"]["bind"] = body["normal"]["HOOK"]["bind"]
        status, saved, _ = self.call("POST", "/api/controls", body)
        self.assertEqual(status, 200)
        self.assertIn("more than one punch", saved["warning"])
        body["normal"]["JAB"]["bind"] = "j" if body["normal"]["HOOK"]["bind"] != "j" else "q"
        self.assertEqual(self.call("POST", "/api/controls", body)[1]["warning"], "")

    def test_a_defence_move_can_have_no_key(self):
        body = self.payload()
        body["defence"]["BLOCK"] = ""
        status, saved, _ = self.call("POST", "/api/controls", body)
        self.assertEqual((status, saved["defence"]["BLOCK"]), (200, ""))
        import game_input
        self.assertNotIn("BLOCK", game_input.Mapping.load(app_server.CONTROLS_PATH).holds)

    def test_an_unknown_key_is_refused_with_the_list_of_good_ones(self):
        body = self.payload()
        body["defence"]["BLOCK"] = "banana"
        status, error, _ = self.call("POST", "/api/controls", body)
        self.assertEqual(status, 400)
        self.assertIn("unknown key", error["error"])

    def test_bad_hold_times_and_unknown_actions_are_refused(self):
        body = self.payload()
        body["normal"]["JAB"]["hold_ms"] = 5
        self.assertEqual(self.call("POST", "/api/controls", body)[0], 400)
        body = self.payload()
        body["normal"]["TELEPORT"] = {"bind": "t", "hold_ms": 50}
        self.assertEqual(self.call("POST", "/api/controls", body)[0], 400)

    def test_a_refused_save_changes_nothing(self):
        before = self.current()
        body = self.payload()
        body["defence"]["BLOCK"] = "banana"
        self.call("POST", "/api/controls", body)
        self.assertEqual(self.current(), before)


class RunningATool(unittest.TestCase):
    def wait_for(self, jobs, seconds=20):
        end = time.time() + seconds
        while jobs.running() and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.2)  # the reader thread writes the last lines

    def test_output_is_captured_and_the_exit_is_reported(self):
        jobs = app_server.Jobs()
        jobs.start("glove_check", "Test", ["-c", "print('hello'); print('there')"])
        self.wait_for(jobs)
        snap = jobs.snapshot()
        self.assertFalse(snap["running"])
        self.assertEqual(snap["exit_code"], 0)
        self.assertIn("hello", snap["lines"])
        self.assertIn("there", snap["lines"])

    def test_only_one_tool_runs_at_a_time(self):
        jobs = app_server.Jobs()
        jobs.start("glove_check", "First", ["-c", "import time; time.sleep(30)"])
        try:
            with self.assertRaises(app_server.BadRequest):
                jobs.start("train", "Second", ["-c", "print(1)"])
        finally:
            jobs.stop()
            self.wait_for(jobs)
        self.assertTrue(jobs.stopped_by_user)

    def test_typed_commands_reach_the_tool(self):
        jobs = app_server.Jobs()
        jobs.start("capture", "Test", ["-c", "line = input(); print('got ' + line)"])
        jobs.send("x")
        self.wait_for(jobs)
        self.assertIn("got x", jobs.snapshot()["lines"])

    def test_typed_commands_are_checked_and_only_for_tools_that_read_them(self):
        jobs = app_server.Jobs()
        jobs.start("capture", "Test", ["-c", "import time; time.sleep(30)"])
        try:
            for bad in ("", "x; calc", "a" * 100, "$(calc)"):
                with self.assertRaises(app_server.BadRequest, msg=bad):
                    jobs.send(bad)
        finally:
            jobs.stop()
            self.wait_for(jobs)
        other = app_server.Jobs()
        other.start("train", "Test", ["-c", "import time; time.sleep(30)"])
        try:
            with self.assertRaises(app_server.BadRequest):
                other.send("q")
        finally:
            other.stop()
            self.wait_for(other)

    def test_the_log_can_be_read_from_where_you_left_off(self):
        jobs = app_server.Jobs()
        jobs.start("glove_check", "Test", ["-c", "[print(i) for i in range(5)]"])
        self.wait_for(jobs)
        everything = jobs.snapshot()
        later = jobs.snapshot(since=3)
        self.assertEqual(later["lines"], everything["lines"][3:])
        self.assertEqual(later["total"], everything["total"])


if __name__ == "__main__":
    unittest.main()
