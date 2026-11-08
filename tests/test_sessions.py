"""Deleting and restoring whole sessions (punch_sessions.py) and discarding one during capture."""
import dataclasses
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import punch_session as ps  # noqa: E402
import punch_sessions  # noqa: E402


def fake_block(root, label, session, person, n):
    folder = os.path.join(root, label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{label}_{session}_{n}.npz")
    np.savez(path, meta=json.dumps({"label": label, "session": session, "person": person}))
    return path


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_arms = dict(ps.ARMS)
        for name in ("left", "right"):
            ps.ARMS[name] = dataclasses.replace(ps.ARMS[name], data_dir=os.path.join(self.tmp.name, name))
        for n in range(3):
            fake_block(ps.ARMS["left"].data_dir, "JAB", "20261107_100000", "andre", n)
            fake_block(ps.ARMS["right"].data_dir, "CROSS", "20261107_100000", "andre", n)
            fake_block(ps.ARMS["left"].data_dir, "HOOK", "20261107_110000", "sam", n)

    def tearDown(self):
        ps.ARMS.update(self.saved_arms)
        self.tmp.cleanup()

    def counts(self):
        return {name: ps.count_blocks(arm.data_dir, arm.labels) for name, arm in ps.ARMS.items()}

    def test_delete_by_person_then_restore(self):
        before = self.counts()
        punch_sessions.main(["--person", "SAM", "--yes", "--no-train"])
        self.assertEqual(self.counts()["left"]["HOOK"], 0)
        self.assertEqual(self.counts()["left"]["JAB"], 3)                 # other people's sessions stay
        self.assertIn("20261107_110000", punch_sessions.scan(deleted=True))
        punch_sessions.main(["--restore", "20261107_110000", "--yes", "--no-train"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(punch_sessions.scan(deleted=True), {})

    def test_delete_last_takes_both_arms(self):
        punch_sessions.main(["--delete", "20261107_100000", "--yes", "--no-train"])
        c = self.counts()
        self.assertEqual((c["left"]["JAB"], c["right"]["CROSS"]), (0, 0))

    def test_one_arm_only_then_restore(self):
        before = self.counts()
        punch_sessions.main(["--delete", "20261107_100000", "--arm", "right", "--yes", "--no-train"])
        c = self.counts()
        self.assertEqual((c["left"]["JAB"], c["right"]["CROSS"]), (3, 0))   # the left arm's part stays
        punch_sessions.main(["--delete", "20261107_100000", "--arm", "left", "--yes", "--no-train"])
        punch_sessions.main(["--restore", "20261107_100000", "--arm", "right", "--yes", "--no-train"])
        c = self.counts()
        self.assertEqual((c["left"]["JAB"], c["right"]["CROSS"]), (0, 3))   # left still deleted, not lost
        punch_sessions.main(["--restore", "20261107_100000", "--yes", "--no-train"])
        self.assertEqual(self.counts(), before)

    def test_unknown_session_changes_nothing(self):
        before = self.counts()
        with self.assertRaises(SystemExit):
            punch_sessions.main(["--delete", "nope", "--yes", "--no-train"])
        self.assertEqual(self.counts(), before)

    def test_discard_during_capture_moves_the_session_out_of_training(self):
        import punch_capture
        root = ps.ARMS["left"].data_dir
        cap = punch_capture.Capture(None, None, 5, root, "andre", ps.ARMS["left"])
        cap.say = lambda *a, **k: None
        cap.session = "20261107_120000"
        path = fake_block(root, "UPPERCUT", cap.session, "andre", 0)
        cap.saved.append((path, "UPPERCUT"))
        cap.counts["UPPERCUT"] = 1
        cap.command("discard")
        self.assertTrue(cap.quit)
        self.assertEqual((cap.saved, cap.counts["UPPERCUT"]), ([], 0))
        self.assertIn("20261107_120000", punch_sessions.scan(deleted=True))  # restorable


if __name__ == "__main__":
    unittest.main()
