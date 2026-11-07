"""Two-arm capture (punch_capture.py --both), the sharp text layer (overlay.py) and the slip key display."""
import os
import queue
import random
import sys
import tempfile
import unittest
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import punch_capture  # noqa: E402
import punch_segment  # noqa: E402
import punch_session as ps  # noqa: E402


class FakeSensors:
    def __init__(self):
        self.events, self.settings, self.now_t = queue.Queue(), punch_segment.SegmentSettings(), 100.0
        self.segmenter = type("S", (), {"ready": True})()

    def glove_ok(self):
        return True

    def latest_imu_t(self):
        return self.now_t


def make_capture(tmp, target=4, seed=1, person="test"):
    punch_capture.beep = lambda kind: None
    recorders = {}
    cap = punch_capture.BothCapture(recorders, target, rng=random.Random(seed), person=person)
    cap.say = lambda *a, **k: None
    for name in ("left", "right"):
        arm = ps.ARMS[name]
        rec = punch_capture.ArmRecorder(cap, FakeSensors(), ps.load_config(), target, os.path.join(tmp, name), "test", arm)
        rec.calls = []

        def fake_save(label, t0, t1, quiet=False, extra=None, data_end=None, rec=rec):  # every punch "found"
            rec.calls.append((label, extra))
            rec.counts[label] = rec.counts.get(label, 0) + 1
            rec.saved.append((f"{label}{len(rec.saved)}", label))
            return punch_segment.Segment(start_t=t0 + 0.1, end_t=t0 + 0.4, reason="decel")
        rec.save_block = fake_save
        recorders[name] = rec
    return cap


def run_until_done(cap, max_steps=2000):
    """Drive the phases by hand: every phase ends at once and the data for every prompt has arrived."""
    cap.on_calibrated()
    cap.toggle()
    for _ in range(max_steps):
        cap.phase_end = 0
        for rec in cap.recorders.values():
            rec.sensors.now_t = 1e12
        if cap.on_frame():
            return
    raise AssertionError("never finished")


class BothArms(unittest.TestCase):
    def test_every_punch_is_asked_and_half_the_combos_start_with_each_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, target=4)
            for rec in cap.recorders.values():
                rec.counts[punch_capture.NONE] = 99  # enough NONE, so no extra MOVE rounds
            run_until_done(cap)
            for name, rec in cap.recorders.items():
                labels = Counter(label for label, _ in rec.calls)
                self.assertEqual(labels, {k: 4 for k in rec.arm.labels}, name)  # each arm's own punches only
            self.assertEqual(cap.first_counts, {"left": 6, "right": 6})     # 12 combos, 6 each
            self.assertEqual(cap.phase, "done")

    def test_first_arm_order_is_random(self):
        orders = set()
        for seed in range(5):
            with tempfile.TemporaryDirectory() as tmp:
                cap = make_capture(tmp, target=4, seed=seed)
                for rec in cap.recorders.values():
                    rec.counts[punch_capture.NONE] = 99
                run_until_done(cap)
                orders.add(tuple(extra["combo"].split(" ")[0] for label, extra in cap.recorders["left"].calls))
        self.assertGreater(len(orders), 1)

    def test_blocks_record_their_combo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, target=1)
            for rec in cap.recorders.values():
                rec.counts[punch_capture.NONE] = 99
            run_until_done(cap)
            label, extra = cap.recorders["right"].calls[0]
            self.assertIn(f"RIGHT {label}", extra["combo"])
            self.assertIn(extra["combo_position"], (1, 2))

    def test_extra_move_rounds_when_none_examples_are_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, target=1)
            run_until_done(cap)  # no NONE ever arrives
            self.assertEqual(cap.extra_free, punch_capture.MAX_EXTRA_FREE)  # tried, then gave up
            self.assertEqual(cap.phase, "done")

    def test_none_is_saved_for_the_arm_that_moved_between_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, target=1)
            cap.on_calibrated()
            cap.toggle()                                   # announce: recording, no prompt yet
            seg = punch_segment.Segment(start_t=50.0, end_t=50.3, reason="decel")
            cap.recorders["right"].sensors.events.put(("end", seg))
            cap.on_frame()
            self.assertEqual([label for label, _ in cap.recorders["right"].calls], ["NONE"])
            self.assertEqual(cap.recorders["left"].calls, [])

    def test_undo_takes_back_the_newest_block_of_either_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, target=1)
            undone = []
            for name, rec in cap.recorders.items():
                rec.undo = lambda name=name: undone.append(name)
            cap.order = ["left", "right"]
            cap.command("x")
            self.assertEqual(undone, ["right"])

    def test_both_refuses_one_arm_options(self):
        with self.assertRaises(SystemExit):
            punch_capture.main(["--both", "--arm", "right"])
        with self.assertRaises(SystemExit):
            punch_capture.main(["--right-phone", "1.2.3.4"])


class NameInTheCameraWindow(unittest.TestCase):
    def test_typed_name_is_cleaned_and_shared_by_both_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = make_capture(tmp, person="")
            cap.on_calibrated()
            self.assertIn("type your name", cap.not_ready_reason())
            cap.toggle()
            self.assertEqual(cap.phase, "waiting")                   # can't start without a name
            for ch in "Sam Leex":
                self.assertTrue(cap.on_key(ord(ch)))                 # used up: c, a, d, q don't act as shortcuts
            cap.on_key(8)                                            # Backspace
            cap.on_key(ord("."))                                     # not allowed in a name
            self.assertTrue(cap.on_key(13))                          # Enter
            self.assertEqual(cap.names.name, "sam_lee")
            self.assertEqual({rec.names.name for rec in cap.recorders.values()}, {"sam_lee"})
            self.assertFalse(cap.on_key(ord("c")))                   # after the name, keys work as usual
            cap.toggle()
            self.assertEqual(cap.phase, "announce")

    def test_enter_on_an_empty_name_does_nothing(self):
        entry = punch_capture.NameEntry()
        entry.key(ord(" "))
        entry.key(13)
        self.assertTrue(entry.waiting)

    def test_person_option_skips_the_question(self):
        self.assertFalse(punch_capture.NameEntry("Andre").waiting)
        self.assertEqual(punch_capture.NameEntry("Andre").name, "andre")


class NoneThatRunsToTheTimeout(unittest.TestCase):
    """Real NONE movements often last the whole punch timeout. Saving only 0.35 s after their start cut them off
    before the punch finder saw them end, so they were rejected as "no punch found" (2026-11-07)."""

    def test_long_none_movement_is_kept(self):
        from types import SimpleNamespace
        config = ps.load_config()
        sensors = ps.LiveSensors(config["segment"], port=5012)
        try:
            rate, rng = 500, np.random.default_rng(5)
            t = np.arange(int(5.0 * rate)) / rate
            accel = np.tile([0.0, 0.0, 1.0], (len(t), 1)) + rng.normal(0, 0.01, (len(t), 3))
            gyro = np.zeros((len(t), 3))
            push = (t >= 3.0) & (t < 3.7)              # a long shove, longer than the 350 ms timeout
            accel[push, 0] += 3.5
            for k in range(len(t)):
                sensors._on_sample(None, SimpleNamespace(t=float(t[k]), t_us=int(t[k] * 1e6), accel=accel[k],
                                                         gyro=gyro[k], button=False, mag=None, mag_ok=False))
            seg = None
            while not sensors.events.empty():
                kind, value = sensors.events.get()
                if kind == "end":
                    seg = value
            self.assertIsNotNone(seg)
            self.assertGreaterEqual(seg.end_t - seg.start_t, config["segment"].timeout_s - 0.01)
            with tempfile.TemporaryDirectory() as tmp:
                cap = punch_capture.PromptedCapture(sensors, config, 5, tmp, "test")
                cap.say = lambda *a, **k: None
                short = cap.save_block("NONE", seg.start_t - 0.05, seg.start_t + 0.05, quiet=True)
                self.assertIsNone(short)                           # the old way: cut off, rejected
                kept = cap.save_block("NONE", seg.start_t - 0.05, seg.start_t + 0.05, quiet=True, data_end=seg.end_t)
                self.assertIsNotNone(kept)                         # the fix: saved
                import punch_train
                X, y, *_ = punch_train.build_dataset(tmp, config, quiet=True)
                self.assertEqual(list(y), ["NONE"])                # and training can use it
        finally:
            sensors.close()


class SharpText(unittest.TestCase):
    def test_text_on_the_camera_picture_is_drawn_after_enlarging(self):
        import overlay
        frame = np.zeros((480, 640, 3), np.uint8)
        overlay.begin_layer(frame)
        ps.put(frame, "HELLO", 100, (255, 255, 255), 1.0)
        self.assertEqual(int(frame.sum()), 0)              # held back, not drawn on the small picture
        out = overlay.finish_layer(frame, 1280, 960)
        self.assertEqual(out.shape[:2], (960, 1280))
        self.assertGreater(int(out[150:230].sum()), 0)     # drawn at twice the size, near row 200
        self.assertEqual(int(out[:120].sum()), 0)

    def test_text_on_another_picture_is_drawn_at_once(self):
        import overlay
        frame, popup = np.zeros((480, 640, 3), np.uint8), np.zeros((100, 100, 3), np.uint8)
        overlay.begin_layer(frame)
        ps.put(popup, "X", 50, (255, 255, 255), 1.0)
        self.assertGreater(int(popup.sum()), 0)
        overlay.finish_layer(frame, 640, 480)


class SlipKeys(unittest.TestCase):
    def test_key_for_state_is_the_held_key_only(self):
        import game_input
        mapping = game_input.Mapping({"defence": {"SLIP LEFT": "a", "SLIP RIGHT": "d"}})
        game = game_input.GameInput(mapping, game_input.DryRunKeyboard())
        game.set_enabled(True)
        game.set_states(["SLIP LEFT"])
        self.assertEqual(game.key_for_state("SLIP LEFT"), "a")
        self.assertIsNone(game.key_for_state("SLIP RIGHT"))
        game.set_enabled(False)
        self.assertIsNone(game.key_for_state("SLIP LEFT"))


if __name__ == "__main__":
    unittest.main()
