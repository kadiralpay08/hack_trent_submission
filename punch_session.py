"""Shared plumbing for the punch pipeline: live glove and camera buffers, settings, and training blocks.

Synchronising the glove and the camera: both are put on the laptop clock (time.time()).
  - glove samples: glove_receiver.Clock maps the ESP32's microsecond counter onto it.
  - camera frames: pose_tracker stamps each frame when it is read, before pose estimation; that stamp
    reaches us through on_features(now, ...), right after on_pose(world, visibility) for the same frame.
Any remaining camera delay can be set as camera_lag_s in punch_config.json.
"""
import itertools
import json
import os
import queue
import threading
import time
from collections import deque

import numpy as np

import glove_receiver
import punch_segment

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "punch_config.json")
DATA_DIR = os.path.join(ROOT, "punch_data")
MODEL_PATH = os.path.join(ROOT, "models", "punch_rf.joblib")
LABELS = ("JAB", "HOOK", "UPPERCUT")


# --- settings -----------------------------------------------------------------------------------------
def load_config(path=CONFIG_PATH):
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    return {
        "segment": punch_segment.SegmentSettings.from_dict(data.get("segment", {})),
        "min_confidence": float(data.get("min_confidence", 0.6)),
        "camera_lag_s": float(data.get("camera_lag_s", 0.0)),
    }


def save_config(config, path=CONFIG_PATH):
    data = {"_note": "Shared punch settings. punch_tune.py saves the segment settings here.",
            "segment": config["segment"].to_dict(), "min_confidence": config["min_confidence"],
            "camera_lag_s": config["camera_lag_s"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# --- live data ----------------------------------------------------------------------------------------
class LiveSensors:
    """Listens to the glove, runs the segmenter on every sample, and keeps recent glove and pose data.

    events is a queue of (kind, value): ("start", Segment), ("end", Segment), ("press", t), ("release", t).
    Wire the camera in with pose_callbacks().
    """

    def __init__(self, settings, seconds=30, simulate=False, port=glove_receiver.PORT):
        self.lock = threading.Lock()
        self.imu = deque(maxlen=int(seconds * 600))  # (t, t_us, ax, ay, az, gx, gy, gz, button)
        self.pose = deque(maxlen=int(seconds * 40))   # (t, world 33x3, visibility 33)
        self.events = queue.Queue()
        self.segmenter = punch_segment.Segmenter(settings)
        self.button = False
        self.last_sample_wall = 0.0
        self.samples_seen = 0
        self._pending_pose = None
        try:
            self.receiver = glove_receiver.GloveReceiver(port=port, on_sample=self._on_sample)
        except OSError:
            raise SystemExit(f"Cannot listen for the glove on UDP port {port}: another program already is. Close the "
                             "other punch tool (punch_tune, punch_capture, punch_live, glove_check or controller) first.")
        self.sim_stop = None
        if simulate:
            import punch_sim
            self.sim_stop = punch_sim.start_simulator(port=port)

    def set_settings(self, settings):
        with self.lock:
            self.segmenter = punch_segment.Segmenter(settings)

    @property
    def settings(self):
        return self.segmenter.s

    def _on_sample(self, glove, s):
        with self.lock:
            self.imu.append((s.t, s.t_us, *s.accel, *s.gyro, s.button))
            self.samples_seen += 1
            self.last_sample_wall = time.time()
            if s.button != self.button:
                self.button = s.button
                self.events.put(("press" if s.button else "release", s.t))
            event = self.segmenter.feed(s.t, s.accel, s.gyro)
        if event:
            self.events.put(event)

    def glove_ok(self, within=0.5):
        return time.time() - self.last_sample_wall < within

    def latest_imu_t(self):
        with self.lock:
            return self.imu[-1][0] if self.imu else None

    # camera side
    def pose_callbacks(self):
        def on_pose(world, visibility):
            self._pending_pose = (np.array(world, dtype=float), np.array(visibility, dtype=float))

        def on_features(now, speeds, travel, from_low):
            if self._pending_pose is not None:
                with self.lock:
                    self.pose.append((now, *self._pending_pose))
                self._pending_pose = None
            return None  # we decide punches ourselves

        return {"on_pose": on_pose, "on_features": on_features, "builtin_punches": False}

    def latest_pose_t(self):
        with self.lock:
            return self.pose[-1][0] if self.pose else None

    def window(self, t0, t1):
        """Glove and pose data between t0 and t1, as arrays (see block format below)."""
        with self.lock:
            imu = [r for r in self.imu if t0 <= r[0] <= t1]
            pose = [r for r in self.pose if t0 <= r[0] <= t1]
        return imu_arrays(imu), pose_arrays(pose)

    def recent_linear_g(self, seconds):
        """(t, |acceleration - 1 g|) for the last few seconds, for drawing a trace."""
        with self.lock:
            rows = list(itertools.islice(reversed(self.imu), int(seconds * 520)))[::-1]
        if not rows:
            return np.zeros(0), np.zeros(0)
        a = np.array(rows, dtype=float)
        return a[:, 0], np.abs(np.linalg.norm(a[:, 2:5], axis=1) - 1.0)

    def close(self):
        if self.sim_stop:
            self.sim_stop.set()


def imu_arrays(rows):
    a = np.array(rows, dtype=float).reshape(-1, 9)
    return {"t": a[:, 0], "t_us": a[:, 1].astype(np.int64), "accel": a[:, 2:5], "gyro": a[:, 5:8],
            "button": a[:, 8].astype(bool)}


def pose_arrays(rows):
    if not rows:
        return {"t": np.zeros(0), "world": np.zeros((0, 33, 3)), "vis": np.zeros((0, 33))}
    return {"t": np.array([r[0] for r in rows]), "world": np.stack([r[1] for r in rows]),
            "vis": np.stack([r[2] for r in rows])}


# --- the trained model -------------------------------------------------------------------------------
class Classifier:
    """The random forest saved by punch_train.py, with the punch settings it was trained with."""

    def __init__(self, path=MODEL_PATH):
        import joblib
        bundle = joblib.load(path)
        self.model, self.features = bundle["model"], bundle["features"]
        self.model.n_jobs = 1  # one punch at a time: starting worker threads costs more than it saves
        self.labels = list(bundle["labels"])
        self.segment = punch_segment.SegmentSettings.from_dict(bundle["segment"])
        self.camera_lag_s = bundle.get("camera_lag_s", 0.0)
        self.min_confidence = bundle.get("min_confidence", 0.6)
        self.info = {k: bundle.get(k) for k in ("trained_at", "counts", "cv_accuracy", "feature_set")}

    @staticmethod
    def exists(path=MODEL_PATH):
        return os.path.exists(path)

    def classify(self, imu, pose, seg, pre_s=None):
        """(label, confidence) for one punch, or (None, 0.0) if there is too little glove data."""
        import punch_features
        feats = punch_features.extract(imu, pose, seg, self.segment.pre_s if pre_s is None else pre_s,
                                       self.camera_lag_s)
        if feats is None:
            return None, 0.0
        x = np.array([[feats.get(n, np.nan) for n in self.features]], dtype=float)
        proba = self.model.predict_proba(x)[0]
        best = int(np.argmax(proba))
        return self.model.classes_[best], float(proba[best])


# --- training blocks ----------------------------------------------------------------------------------
# One .npz per button press: punch_data/<LABEL>/<LABEL>_<time>.npz, holding the raw glove samples
# (imu_t, imu_t_us, imu_accel in g, imu_gyro in deg/s, imu_button), the pose frames (pose_t, pose_world,
# pose_vis) and a JSON "meta" string (label, press_t, release_t, session, settings when recorded).
# Folders starting with "_" (such as _rejected) are ignored by training.

def save_block(label, imu, pose, meta, root=DATA_DIR, rejected=False):
    folder = os.path.join(root, "_rejected" if rejected else label)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    path, n = os.path.join(folder, f"{label}_{stamp}.npz"), 1
    while os.path.exists(path):  # never overwrite a block saved in the same millisecond
        path, n = os.path.join(folder, f"{label}_{stamp}_{n}.npz"), n + 1
    np.savez_compressed(path, imu_t=imu["t"], imu_t_us=imu["t_us"], imu_accel=imu["accel"],
                        imu_gyro=imu["gyro"], imu_button=imu["button"], pose_t=pose["t"],
                        pose_world=pose["world"], pose_vis=pose["vis"], meta=json.dumps(meta))
    return path


def load_block(path):
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        imu = {"t": z["imu_t"], "t_us": z["imu_t_us"], "accel": z["imu_accel"], "gyro": z["imu_gyro"],
               "button": z["imu_button"]}
        pose = {"t": z["pose_t"], "world": z["pose_world"], "vis": z["pose_vis"]}
    return {"path": path, "meta": meta, "imu": imu, "pose": pose}


def block_paths(root=DATA_DIR):
    out = []
    if not os.path.isdir(root):
        return out
    for label in sorted(os.listdir(root)):
        folder = os.path.join(root, label)
        if label.startswith("_") or not os.path.isdir(folder):
            continue
        out += [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith(".npz")]
    return out


def count_blocks(root=DATA_DIR):
    counts = dict.fromkeys(LABELS, 0)
    for path in block_paths(root):
        label = os.path.basename(os.path.dirname(path))
        counts[label] = counts.get(label, 0) + 1
    return counts


# --- drawing helpers shared by the camera windows ------------------------------------------------------
def draw_trace(frame, times, values, now, seconds, top_g, marks, origin, size, threshold_g=None):
    """A strip chart of acceleration beyond gravity. marks = [(t, colour)] vertical lines."""
    import cv2
    x0, y0 = origin
    w, h = size
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), (30, 30, 30), -1)
    to_x = lambda t: int(x0 + w * (1 - (now - t) / seconds))
    to_y = lambda g: int(y0 + h - h * min(g, top_g) / top_g)
    if threshold_g is not None:
        cv2.line(frame, (x0, to_y(threshold_g)), (x0 + w, to_y(threshold_g)), (90, 90, 90), 1)
    keep = times >= now - seconds
    if keep.sum() > 1:
        pts = np.stack([[to_x(t) for t in times[keep]], [to_y(v) for v in values[keep]]], axis=1)
        cv2.polylines(frame, [pts.astype(np.int32)], False, (255, 255, 255), 1)
    for t, colour in marks:
        if t >= now - seconds:
            cv2.line(frame, (to_x(t), y0), (to_x(t), y0 + h), colour, 2)


def put(frame, text, row, colour=(255, 255, 255), scale=0.6, x=10, thick=2):
    import cv2
    cv2.putText(frame, text, (x, row), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3)
    cv2.putText(frame, text, (x, row), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick)
