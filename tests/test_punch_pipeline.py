"""Tests for the punch pipeline, on synthetic data (punch_sim.py). Run: python -m unittest discover tests

Synthetic punches only show the code works. They say nothing about accuracy on real punches.
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glove_receiver  # noqa: E402
import punch_features  # noqa: E402
import punch_segment  # noqa: E402
import punch_session  # noqa: E402
import punch_sim  # noqa: E402


def block_segment(rec, settings=None):
    segs = punch_segment.find_segments(rec["t"], rec["accel"], rec["gyro"], settings)
    return punch_segment.pick_block_segment(segs, rec["press_t"], rec["release_t"])


class FrameFormat(unittest.TestCase):
    def test_button_round_trips_and_old_frames_still_parse(self):
        released = glove_receiver.pack_frame(1234, [0, 0, 1], [1, 2, 3])
        pressed = glove_receiver.pack_frame(1236, [0.5, 0, 1], [0, 0, 300], button=True)
        self.assertEqual(released[:2], b"\xA5\x5A")
        self.assertEqual(pressed[:2], b"\xA5\x5B")
        samples = list(glove_receiver.parse_frames(b"junk" + released + pressed))
        self.assertEqual([s.button for s in samples], [False, True])
        self.assertEqual([s.t_us for s in samples], [1234, 1236])
        np.testing.assert_allclose(samples[1].gyro, [0, 0, 300], atol=0.1)

    def test_corrupt_pressed_frame_is_skipped(self):
        bad = bytearray(glove_receiver.pack_frame(1, [0, 0, 1], [0, 0, 0], button=True))
        bad[10] ^= 0xFF
        self.assertEqual(list(glove_receiver.parse_frames(bytes(bad))), [])


class Segmenting(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(3)

    def test_every_block_has_one_punch(self):
        for kind in punch_sim.LABELS:
            for _ in range(20):
                seg, found = block_segment(punch_sim.synthetic_recording(kind, self.rng))
                self.assertIsNotNone(seg, kind)
                self.assertEqual(found, 1, kind)

    def test_jab_ends_when_it_stops_not_at_the_timeout(self):
        reasons = [block_segment(punch_sim.synthetic_recording("JAB", self.rng))[0].reason for _ in range(30)]
        self.assertGreaterEqual(reasons.count("decel"), 25)

    def test_uppercut_dip_does_not_end_the_punch(self):
        # the dip-then-drive reversal must not count as the stop: the punch must last past the drive
        for _ in range(30):
            rec = punch_sim.synthetic_recording("UPPERCUT", self.rng)
            seg, _ = block_segment(rec)
            self.assertGreater(seg.end_t - rec["punch_t"], 0.20)

    def test_hook_reversal_does_not_end_the_punch(self):
        for _ in range(30):
            rec = punch_sim.synthetic_recording("HOOK", self.rng)
            seg, _ = block_segment(rec)
            self.assertGreater(seg.end_t - rec["punch_t"], 0.25)

    def test_timeout_caps_the_punch(self):
        settings = punch_segment.SegmentSettings(timeout_s=0.1)
        for kind in punch_sim.LABELS:
            seg, _ = block_segment(punch_sim.synthetic_recording(kind, self.rng), settings)
            self.assertLessEqual(seg.duration, 0.1 + 0.003)

    def test_nothing_found_at_rest(self):
        t = np.arange(1000) / 500
        accel = np.tile([0, 0, 1.0], (1000, 1)) + self.rng.normal(0, 0.02, (1000, 3))
        self.assertEqual(punch_segment.find_segments(t, accel, np.zeros((1000, 3))), [])

    def test_unknown_setting_is_an_error(self):
        with self.assertRaises(ValueError):
            punch_segment.SegmentSettings.from_dict({"timout_s": 0.3})


class Features(unittest.TestCase):
    def test_glove_features_are_finite_and_pose_is_missing_without_a_camera(self):
        rng = np.random.default_rng(4)
        rec = punch_sim.synthetic_recording("UPPERCUT", rng)
        seg, _ = block_segment(rec)
        imu = {k: rec[k] for k in ("t", "accel", "gyro")}
        feats = punch_features.extract(imu, None, seg, 0.25)
        imu_vals = [v for k, v in feats.items() if k.startswith("imu_")]
        self.assertTrue(np.all(np.isfinite(imu_vals)))
        self.assertTrue(np.isnan(feats["pose_up_min"]))
        # uppercut: the window should see the dip (down) before the drive (up)
        self.assertGreater(feats["imu_dip_before_up"], 1.0)
        jab = punch_sim.synthetic_recording("JAB", rng)
        jab_feats = punch_features.extract({k: jab[k] for k in ("t", "accel", "gyro")}, None, block_segment(jab)[0], 0.25)
        self.assertLess(jab_feats["imu_dip_before_up"], 0.5)

    def test_pose_dip_then_rise_is_measured(self):
        frames = 12
        world = np.zeros((frames, 33, 3))
        world[:, 11] = [0.2, -0.5, 0]   # left shoulder (MediaPipe y points down)
        world[:, 12] = [-0.2, -0.5, 0]  # right shoulder
        world[:, 13] = [0.25, -0.3, -0.1]
        world[:, 23] = [0.1, 0.0, 0]
        heights = np.concatenate([np.linspace(-0.4, -0.2, 4), np.linspace(-0.2, -0.7, 8)])  # down, then up
        world[:, 15] = np.stack([np.full(frames, 0.2), heights, np.full(frames, -0.3)], axis=1)
        pose = {"t": np.linspace(0.0, 0.3, frames), "world": world, "vis": np.ones((frames, 33))}
        seg = punch_segment.Segment(start_t=0.05, end_t=0.3)
        feats = punch_features.pose_features(pose, seg, 0.1)
        self.assertGreater(feats["pose_dip"], 0.3)
        self.assertGreater(feats["pose_rise"], 1.0)
        self.assertEqual(feats["pose_frames"], frames)


class TrainEndToEnd(unittest.TestCase):
    def test_capture_format_train_and_classify(self):
        import punch_train
        rng = np.random.default_rng(5)
        with tempfile.TemporaryDirectory() as tmp:
            data, model = os.path.join(tmp, "data"), os.path.join(tmp, "model.joblib")
            for session in ("s1", "s2"):
                for kind in punch_sim.LABELS:
                    for _ in range(12):
                        rec = punch_sim.synthetic_recording(kind, rng)
                        imu = {"t": rec["t"], "t_us": rec["t_us"], "accel": rec["accel"], "gyro": rec["gyro"],
                               "button": (rec["t"] >= rec["press_t"]) & (rec["t"] <= rec["release_t"])}
                        meta = {"label": kind, "press_t": rec["press_t"], "release_t": rec["release_t"], "session": session}
                        punch_session.save_block(kind, imu, punch_session.pose_arrays([]), meta, data)
            self.assertEqual(punch_session.count_blocks(data), {"JAB": 24, "HOOK": 24, "UPPERCUT": 24})
            config = os.path.join(tmp, "config.json")
            punch_session.save_config(punch_session.load_config(os.path.join(tmp, "none.json")), config)
            code = punch_train.main(["--data", data, "--out", model, "--config", config, "--features", "imu"])
            self.assertEqual(code, 0)
            clf = punch_session.Classifier(model)
            rec = punch_sim.synthetic_recording("HOOK", rng)
            seg, _ = block_segment(rec, clf.segment)
            label, conf = clf.classify({"t": rec["t"], "accel": rec["accel"], "gyro": rec["gyro"]}, None, seg)
            self.assertEqual(label, "HOOK")
            self.assertGreater(conf, 0.5)


class TimedTest(unittest.TestCase):
    def test_score_test_counts_punches_at_each_timeout(self):
        import punch_tune
        rng = np.random.default_rng(6)
        parts, t0 = [], 0.0
        for _ in range(5):
            rec = punch_sim.synthetic_recording("JAB", rng, t0=t0)
            parts.append(rec)
            t0 = rec["t"][-1] + 1 / 500
        imu = {k: np.concatenate([p[k] for p in parts]) for k in ("t", "accel", "gyro")}
        rows = punch_tune.score_test(imu, None, 0.0, t0, punch_segment.SegmentSettings(), timeouts_ms=(200, 400))
        self.assertEqual({ms: found for ms, found, _, _ in rows}, {200: 5, 400: 5})


if __name__ == "__main__":
    unittest.main()
