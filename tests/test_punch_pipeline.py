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
            # the confidence cutoff is not fixed by training: it comes from the caller or punch_config.json
            self.assertEqual(punch_session.Classifier(model, 0.8).min_confidence, 0.8)
            self.assertEqual(punch_session.Classifier(model).min_confidence, punch_session.load_config()["min_confidence"])
            import punch_live
            play = punch_live.LivePlay(None, punch_session.Classifier(model, 0.9), None, log=lambda *a: None)
            for _ in range(5):
                play.adjust_confidence(+0.05)
            self.assertEqual(play.clf.min_confidence, 0.95)
            for _ in range(20):
                play.adjust_confidence(-0.05)
            self.assertEqual(play.clf.min_confidence, 0.35)


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
        rows = punch_tune.score_test(imu, None, [(0.0, t0)], punch_segment.SegmentSettings(), timeouts_ms=(200, 400))
        self.assertEqual({ms: found for ms, found, _, _ in rows}, {200: 5, 400: 5})
        # a pause in the middle: punches that started while paused do not count
        half = parts[2]["t"][0]
        rows = punch_tune.score_test(imu, None, [(0.0, half - 0.01)], punch_segment.SegmentSettings(), timeouts_ms=(400,))
        self.assertEqual(rows[0][1], 2)


class RightGlove(unittest.TestCase):
    """The second glove: its own frame (with a magnetometer), port, data folder, labels and model."""

    def test_magnetometer_frame_round_trips_beside_old_frames(self):
        old = glove_receiver.pack_frame(10, [0, 0, 1], [1, 2, 3])
        new = glove_receiver.pack_frame(12, [0.5, 0, 1], [0, 0, 300], button=True, mag=[20.0, -5.0, 40.0])
        self.assertEqual((len(old), len(new)), (19, 26))
        a, b = glove_receiver.parse_frames(b"x" + old + new)
        self.assertIsNone(a.mag)
        np.testing.assert_allclose(b.mag, [20, -5, 40], atol=0.1)
        self.assertTrue(b.mag_ok and b.button)
        self.assertEqual(b.size, 26)
        bad = bytearray(new)
        bad[20] ^= 0xFF
        self.assertEqual(list(glove_receiver.parse_frames(bytes(bad))), [])

    def test_arms_keep_separate_ports_data_and_models(self):
        left, right = punch_session.ARMS["left"], punch_session.ARMS["right"]
        self.assertEqual((left.port, right.port), (5005, 5006))
        self.assertNotEqual(left.data_dir, right.data_dir)
        self.assertNotEqual(left.model_path, right.model_path)
        self.assertEqual(left.data_dir, os.path.join(punch_session.ROOT, "punch_data"))  # the original recordings
        self.assertEqual(left.model_path, os.path.join(punch_session.ROOT, "models", "punch_rf.joblib"))
        self.assertEqual(right.labels, ("CROSS", "HOOK", "UPPERCUT"))

    def test_live_rows_with_and_without_magnetometer(self):
        plain = [(0.0, 0, 0, 0, 1, 0, 0, 0, False, 0, 0, 0, False)] * 3
        withmag = [(0.0, 0, 0, 0, 1, 0, 0, 0, False, 20, 0, -40, True)] * 3
        self.assertNotIn("mag", punch_session.imu_arrays(plain))
        self.assertEqual(punch_session.imu_arrays(withmag)["mag"].shape, (3, 3))

    def test_right_arm_pose_features_are_mirrored(self):
        # a right hook swings out to the RIGHT first; mirrored, it should look like a left hook does
        frames = 12
        world = np.zeros((frames, 33, 3))
        world[:, 11], world[:, 12] = [0.2, -0.5, 0], [-0.2, -0.5, 0]   # MediaPipe: left shoulder at +x
        world[:, 14], world[:, 24] = [-0.25, -0.3, -0.1], [-0.1, 0.0, 0]
        x = np.concatenate([np.linspace(-0.3, -0.55, 4), np.linspace(-0.55, 0.1, 8)])  # out right, then across
        world[:, 16] = np.stack([x, np.full(frames, -0.4), np.full(frames, -0.3)], axis=1)
        pose = {"t": np.linspace(0, 0.3, frames), "world": world, "vis": np.ones((frames, 33))}
        feats = punch_features.pose_features(pose, punch_segment.Segment(start_t=0.0, end_t=0.3), 0.0, side="R")
        self.assertGreater(feats["pose_out_left"], 0.4)    # "out" first...
        self.assertGreater(feats["pose_back_right"], 1.0)  # ...then back across, as for the left arm

    def test_right_arm_trains_with_magnetometer_features(self):
        import punch_train
        rng = np.random.default_rng(8)
        with tempfile.TemporaryDirectory() as tmp:
            data, model = os.path.join(tmp, "right"), os.path.join(tmp, "right.joblib")
            for kind in punch_session.ARMS["right"].labels:
                for _ in range(10):
                    rec = punch_sim.synthetic_recording(kind, rng, mirror=True, magnetometer=True)
                    imu = {k: rec[k] for k in ("t", "t_us", "accel", "gyro", "mag", "mag_ok")}
                    imu["button"] = np.zeros(len(rec["t"]), dtype=bool)
                    meta = {"label": kind, "arm": "right", "press_t": rec["press_t"], "release_t": rec["release_t"],
                            "session": "s1"}
                    punch_session.save_block(kind, imu, punch_session.pose_arrays([]), meta, data)
            block = punch_session.load_block(punch_session.block_paths(data)[0])
            self.assertIn("mag", block["imu"])
            config = os.path.join(tmp, "config.json")
            punch_session.save_config(punch_session.load_config(os.path.join(tmp, "none.json")), config)
            code = punch_train.main(["--arm", "right", "--data", data, "--out", model, "--config", config,
                                     "--features", "imu"])
            self.assertEqual(code, 0)
            clf = punch_session.Classifier(model)
            self.assertEqual(clf.side, "R")
            self.assertTrue(any(n.startswith("imu_mag_") for n in clf.features))
            rec = punch_sim.synthetic_recording("CROSS", rng, mirror=True, magnetometer=True)
            seg, _ = block_segment(rec, clf.segment)
            label, _ = clf.classify({k: rec[k] for k in ("t", "accel", "gyro", "mag", "mag_ok")}, None, seg)
            self.assertEqual(label, "CROSS")

    def test_one_camera_feeds_both_arms(self):
        class Buf:
            def __init__(self):
                self.got = []

            def pose_callbacks(self):
                return {"on_pose": lambda w, v: self.got.append("pose"),
                        "on_features": lambda now, s, t, f: self.got.append(now)}
        a, b = Buf(), Buf()
        cb = punch_session.shared_pose_callbacks(a, b)
        cb["on_pose"](None, None)
        cb["on_features"](1.5, None, None, None)
        self.assertEqual(a.got, ["pose", 1.5])
        self.assertEqual(b.got, ["pose", 1.5])


class FakeSensors:
    """Just enough of LiveSensors for the start / pause logic, with no glove or camera."""

    def __init__(self):
        import queue
        self.events, self.settings, self.now_t = queue.Queue(), punch_segment.SegmentSettings(), 100.0
        self.segmenter = type("S", (), {"ready": True})()

    def glove_ok(self):
        return True

    def latest_imu_t(self):
        return self.now_t

    def sample_rate_hz(self):
        return 500.0


class StartAndPause(unittest.TestCase):
    def test_tuner_test_waits_for_calibration_and_pauses(self):
        import punch_tune
        tuner = punch_tune.Tuner(FakeSensors(), punch_session.load_config())
        tuner.classifier = None
        tuner.say = lambda text: None
        tuner.set_up_test(["3", "10", "jab"])
        tuner.toggle_test()                       # not calibrated yet
        self.assertEqual(tuner.test["state"], "waiting")
        tuner.on_calibrated()
        tuner.toggle_test()
        self.assertEqual(tuner.test["state"], "countdown")
        tuner.test["go_wall"] = 0
        tuner.update_test()
        self.assertEqual(tuner.test["state"], "running")
        self.assertTrue(tuner.counts_for_test(100.5))
        tuner.sensors.now_t = 102.0
        tuner.test["run_wall"] -= 2.0             # as if it had been running for 2 s
        tuner.toggle_test()                       # pause
        self.assertEqual(tuner.test["state"], "paused")
        self.assertEqual(tuner.test["intervals"], [(100.0, 102.0)])
        self.assertFalse(tuner.counts_for_test(103.0))  # a punch while paused does not count
        self.assertAlmostEqual(tuner.test["left"], 8.0, delta=0.1)  # the clock stopped at the pause

    def test_capture_waits_then_pause_cancels_the_showing_prompt(self):
        import time
        import punch_capture
        with tempfile.TemporaryDirectory() as tmp:
            cap = punch_capture.PromptedCapture(FakeSensors(), punch_session.load_config(), 5, tmp, "test")
            cap.say = lambda *a: None
            punch_capture.beep = lambda kind: None
            cap.toggle()
            self.assertEqual(cap.phase, "waiting")        # not calibrated: nothing starts
            seg = punch_segment.Segment(start_t=99.0, end_t=99.2, reason="decel")
            cap.sensors.events.put(("end", seg))
            cap.on_frame()
            self.assertEqual(cap.none_pending, [])       # movement while waiting is not saved as NONE
            cap.on_calibrated()
            cap.toggle()
            self.assertEqual(cap.phase, "announce")
            cap.phase_end = 0
            cap.on_frame()                                # announce -> rest
            cap.phase_end = 0
            cap.on_frame()                                # rest -> go: a prompt is showing
            self.assertEqual((cap.phase, cap.prompts_done, len(cap.pending)), ("go", 1, 1))
            cap.toggle()                                  # pause during the prompt
            self.assertTrue(cap.paused)
            self.assertEqual((cap.prompts_done, len(cap.pending), len(cap.windows)), (0, 0, 0))
            before = cap.phase_end
            cap.on_frame()
            self.assertEqual(cap.phase_end, before)       # nothing moves on while paused
            cap.toggle()
            self.assertFalse(cap.paused)
            self.assertEqual(cap.phase, "announce")


class NoneWhileMoving(unittest.TestCase):
    """NONE examples happen while the boxer is moving, so the glove is never still in the saved slice.
    They used to be rejected as "no punch found", because a fresh segmenter waits for stillness."""

    def moving_then_jolt(self):
        from types import SimpleNamespace
        rate, rng = 500, np.random.default_rng(3)
        t = np.arange(int(4.0 * rate)) / rate
        accel = np.tile([0.0, 0.0, 1.0], (len(t), 1)) + rng.normal(0, 0.01, (len(t), 3))
        gyro = np.zeros((len(t), 3))
        moving = t >= 1.0                       # still for 1 s, then shuffling about for good
        accel[moving] += 0.3 * np.stack([np.sin(7 * t[moving]), np.cos(5 * t[moving]), 0 * t[moving]], 1)
        gyro[moving, 2] = 80 + 30 * np.sin(3 * t[moving])  # turning about gravity, never slow enough to be still
        lin, rot = punch_sim.punch_motion("JAB", rng)  # a sharp guard snap at 3 s
        i = int(3.0 * rate)
        accel[i:i + len(lin)] += lin
        samples = [SimpleNamespace(t=float(t[k]), t_us=int(t[k] * 1e6), accel=accel[k], gyro=gyro[k], button=False,
                                   mag=None, mag_ok=False) for k in range(len(t))]
        return samples, 3.0

    def test_none_example_is_saved_and_trained_on(self):
        import punch_capture
        import punch_train
        config = punch_session.load_config()
        sensors = punch_session.LiveSensors(config["segment"], port=5011)
        try:
            samples, jolt_t = self.moving_then_jolt()
            for s in samples:
                sensors._on_sample(None, s)
            with tempfile.TemporaryDirectory() as tmp:
                cap = punch_capture.PromptedCapture(sensors, config, 5, tmp, "test")
                imu, _ = sensors.window(jolt_t - 1.05, jolt_t + 0.35)
                self.assertEqual(punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], config["segment"]),
                                 [])                                  # the old way: nothing found
                seg = cap.save_block("NONE", jolt_t - 0.05, jolt_t + 0.05, quiet=True)
                self.assertIsNotNone(seg)                             # now it is saved
                self.assertAlmostEqual(seg.start_t, jolt_t, delta=0.02)
                X, y, *_ = punch_train.build_dataset(tmp, config, quiet=True)
                self.assertEqual(list(y), ["NONE"])                   # and training uses it
        finally:
            sensors.receiver.close() if hasattr(sensors.receiver, "close") else None
            sensors.close()


if __name__ == "__main__":
    unittest.main()
