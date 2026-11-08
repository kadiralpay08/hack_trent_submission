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
from dataclasses import dataclass, field

import numpy as np

import glove_receiver
import punch_segment

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "punch_config.json")


# --- the two arms --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Arm:
    """Everything that differs between the left and right glove. Each arm has its own port, recordings,
    model and punch names, so the two are recorded, trained and run separately."""
    name: str            # "left" or "right"
    side: str            # "L" or "R": which arm the camera features look at
    port: int            # UDP port its glove sends to
    data_dir: str        # its training recordings
    model_path: str      # its trained random forest
    labels: tuple        # its punches; NONE (not a punch) is added by training when recorded
    how: dict = field(default_factory=dict)  # how to throw each punch, shown when recording
    mirror: bool = False  # the simulator mirrors left-right for this arm
    magnetometer: bool = False  # its glove has one (the simulator fakes it)


# Left: the ESP32-CAM glove (firmware/glove_imu; an MPU6050, or an MPU-6500, which reads the same).
# Right: a second glove on port 5006, for example firmware/glove_imu_s3 with UDP_PORT set to 5006.
ARMS = {
    "left": Arm("left", "L", 5005, os.path.join(ROOT, "punch_data"), os.path.join(ROOT, "models", "punch_rf.joblib"),
                ("JAB", "HOOK", "UPPERCUT"),
                {"JAB": "straight forward", "HOOK": "left, then forward, then right",
                 "UPPERCUT": "arm drops down, then drives up"}),
    "right": Arm("right", "R", 5006, os.path.join(ROOT, "punch_data_right"),
                 os.path.join(ROOT, "models", "punch_rf_right.joblib"),
                 ("CROSS", "HOOK", "UPPERCUT"),
                 {"CROSS": "straight forward", "HOOK": "right, then forward, then left",
                  "UPPERCUT": "arm drops down, then drives up"}, mirror=True, magnetometer=True),
}

# The left arm's paths, kept under their old names so existing code and data work unchanged.
DATA_DIR = ARMS["left"].data_dir
MODEL_PATH = ARMS["left"].model_path
LABELS = ARMS["left"].labels


def add_arm_argument(parser):
    parser.add_argument("--arm", choices=sorted(ARMS), default="left",
                        help="which glove: left (ESP32-CAM, port 5005) or right (ESP32-S3, port 5006)")


def add_phone_argument(parser):
    parser.add_argument("--phone", metavar="ADDRESS",
                        help="use a phone running phyphox (remote access on) as the right arm's sensor instead of a "
                             "glove: the address it shows, e.g. 172.20.10.1. Only with --arm right")


def phone_for(args):
    """args.phone, once checked to be for the right arm only, so a phone's recordings and model can never be mixed
    into the left glove's."""
    if args.phone and args.arm != "right":
        raise SystemExit("--phone is only for the right arm: add --arm right.")
    return args.phone


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

    def __init__(self, settings, seconds=30, simulate=False, port=glove_receiver.PORT, arm=None, phone=None):
        self.lock = threading.Lock()
        # (t, t_us, ax, ay, az, gx, gy, gz, button, mx, my, mz, mag_ok); mag is 0 and not ok without one
        self.imu = deque(maxlen=int(seconds * 600))
        self.pose = deque(maxlen=int(seconds * 40))   # (t, world 33x3, visibility 33)
        self.gravity_log = deque(maxlen=int(seconds * 60))  # (t, live gravity estimate), for gravity_at()
        self.events = queue.Queue()
        self.segmenter = punch_segment.Segmenter(settings)
        self.button = False
        self.last_sample_wall = 0.0
        self.samples_seen = 0
        self._pending_pose = None
        self.sim_stop = None
        if phone:  # a phone running phyphox, polled over WiFi, instead of a glove sending UDP
            if simulate:
                raise SystemExit("--phone and --simulate can't be used together.")
            import phyphox_imu
            try:
                self.receiver = phyphox_imu.open_receiver(phone, on_sample=self._on_sample, interval=0.05)
            except RuntimeError as error:
                raise SystemExit(str(error))
            return
        try:
            self.receiver = glove_receiver.GloveReceiver(port=port, on_sample=self._on_sample)
        except OSError:
            raise SystemExit(f"Cannot listen for the glove on UDP port {port}: another program already is. Close the "
                             "other punch tool (punch_tune, punch_capture, punch_live, glove_check or controller) first.")
        if simulate:
            import punch_sim
            arm = arm or ARMS["left"]
            self.sim_stop = punch_sim.start_simulator(port=port, kinds=arm.labels, mirror=arm.mirror,
                                                      magnetometer=arm.magnetometer)

    def set_settings(self, settings):
        with self.lock:
            self.segmenter = punch_segment.Segmenter(settings)

    @property
    def settings(self):
        return self.segmenter.s

    def _on_sample(self, glove, s):
        with self.lock:
            mag = s.mag if s.mag is not None else (0.0, 0.0, 0.0)
            self.imu.append((s.t, s.t_us, *s.accel, *s.gyro, s.button, *mag, s.mag_ok))
            self.samples_seen += 1
            self.last_sample_wall = time.time()
            if s.button != self.button:
                self.button = s.button
                self.events.put(("press" if s.button else "release", s.t))
            event = self.segmenter.feed(s.t, s.accel, s.gyro)
            if self.samples_seen % 10 == 0 and self.segmenter.gravity is not None:
                self.gravity_log.append((s.t, self.segmenter.gravity.copy()))
        if event:
            self.events.put(event)

    def gravity_at(self, t):
        """The live gravity estimate (glove axes, unit length) at time t, or None. Saved with each
        recording so training can cut it the way live play does, even if the glove was moving."""
        with self.lock:
            before = [g for gt, g in self.gravity_log if gt <= t]
        return before[-1] if before else None

    def glove_ok(self, within=0.5):
        return time.time() - self.last_sample_wall < within

    def latest_imu_t(self):
        with self.lock:
            return self.imu[-1][0] if self.imu else None

    def sample_rate_hz(self, default=500.0, recent=300):
        """How fast samples really arrive, from the typical gap between the latest few: about 500 for the glove,
        about 100 for a phone. Checks for "enough data" scale with this, so they catch gaps in the data and not
        a sensor that is simply slower than the glove. Uses the median gap, so a few dropped packets don't count."""
        with self.lock:
            times = [r[0] for r in itertools.islice(reversed(self.imu), recent)]  # newest first
        if len(times) < 20:
            return default
        step = float(np.median(-np.diff(times)))
        return 1.0 / step if step > 0 else default

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

    def latest_pose(self):
        """The newest camera frame as (t, world 33x3, visibility 33), or None. Used for steering."""
        with self.lock:
            return self.pose[-1] if self.pose else None

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
        if hasattr(self.receiver, "stop"):  # a phyphox receiver has a polling thread to stop
            self.receiver.close()


def shared_pose_callbacks(*sensors):
    """One camera feeding several gloves (both arms): every frame goes to each LiveSensors' pose buffer."""
    parts = [s.pose_callbacks() for s in sensors]

    def on_pose(world, visibility):
        for p in parts:
            p["on_pose"](world, visibility)

    def on_features(now, speeds, travel, from_low):
        for p in parts:
            p["on_features"](now, speeds, travel, from_low)
        return None

    return {"on_pose": on_pose, "on_features": on_features, "builtin_punches": False}


def imu_arrays(rows):
    a = np.array(rows, dtype=float).reshape(-1, 13)
    out = {"t": a[:, 0], "t_us": a[:, 1].astype(np.int64), "accel": a[:, 2:5], "gyro": a[:, 5:8],
           "button": a[:, 8].astype(bool)}
    if a[:, 12].any():  # only a glove with a working magnetometer gets magnetometer data
        out["mag"], out["mag_ok"] = a[:, 9:12], a[:, 12].astype(bool)
    return out


def pose_arrays(rows):
    if not rows:
        return {"t": np.zeros(0), "world": np.zeros((0, 33, 3)), "vis": np.zeros((0, 33))}
    return {"t": np.array([r[0] for r in rows]), "world": np.stack([r[1] for r in rows]),
            "vis": np.stack([r[2] for r in rows])}


# --- the trained model -------------------------------------------------------------------------------
class Classifier:
    """The random forest saved by punch_train.py, with the punch settings it was trained with.

    min_confidence (how sure the model must be to press a key) is NOT fixed by training: it comes from
    the argument, else from punch_config.json, so it can be changed without retraining.
    """

    def __init__(self, path=MODEL_PATH, min_confidence=None):
        import joblib
        bundle = joblib.load(path)
        self.model, self.features = bundle["model"], bundle["features"]
        self.model.n_jobs = 1  # one punch at a time: starting worker threads costs more than it saves
        self.labels = list(bundle["labels"])
        known = punch_segment.SegmentSettings().to_dict()
        old = sorted(set(bundle["segment"]) - set(known))
        if old:  # a model trained before a setting was removed (e.g. stop_ratio, 2026-11-07)
            print(f"Note: this model has settings that no longer exist and are ignored: {', '.join(old)}. Retrain to update it.")
        self.segment = punch_segment.SegmentSettings.from_dict({k: v for k, v in bundle["segment"].items() if k in known})
        self.camera_lag_s = bundle.get("camera_lag_s", 0.0)
        self.side = bundle.get("side", "L")  # models from before the right glove are left-arm models
        self.min_confidence = load_config()["min_confidence"] if min_confidence is None else float(min_confidence)
        self.info = {k: bundle.get(k) for k in ("trained_at", "counts", "cv_accuracy", "feature_set", "arm")}

    @staticmethod
    def exists(path=MODEL_PATH):
        return os.path.exists(path)

    def classify(self, imu, pose, seg, pre_s=None):
        """(label, confidence) for one punch, or (None, 0.0) if there is too little glove data."""
        import punch_features
        feats = punch_features.extract(imu, pose, seg, self.segment.pre_s if pre_s is None else pre_s,
                                       self.camera_lag_s, side=self.side)
        if feats is None:
            return None, 0.0
        x = np.array([[feats.get(n, np.nan) for n in self.features]], dtype=float)
        proba = self.model.predict_proba(x)[0]
        best = int(np.argmax(proba))
        return self.model.classes_[best], float(proba[best])


# --- training blocks ----------------------------------------------------------------------------------
# One .npz per punch: <arm's data_dir>/<LABEL>/<LABEL>_<time>.npz (left: punch_data/, right:
# punch_data_right/), holding the raw glove samples (imu_t, imu_t_us, imu_accel in g, imu_gyro in deg/s,
# imu_button, and for a glove with a magnetometer imu_mag in uT and imu_mag_ok), the pose frames (pose_t,
# pose_world, pose_vis) and a JSON "meta" string (label, arm, press_t, release_t, session, settings).
# Folders starting with "_" (such as _rejected) are ignored by training.

def save_block(label, imu, pose, meta, root=DATA_DIR, rejected=False):
    folder = os.path.join(root, "_rejected" if rejected else label)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    path, n = os.path.join(folder, f"{label}_{stamp}.npz"), 1
    while os.path.exists(path):  # never overwrite a block saved in the same millisecond
        path, n = os.path.join(folder, f"{label}_{stamp}_{n}.npz"), n + 1
    extra = {"imu_mag": imu["mag"], "imu_mag_ok": imu["mag_ok"]} if "mag" in imu else {}
    np.savez_compressed(path, imu_t=imu["t"], imu_t_us=imu["t_us"], imu_accel=imu["accel"],
                        imu_gyro=imu["gyro"], imu_button=imu["button"], pose_t=pose["t"],
                        pose_world=pose["world"], pose_vis=pose["vis"], meta=json.dumps(meta), **extra)
    return path


def load_block(path):
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        imu = {"t": z["imu_t"], "t_us": z["imu_t_us"], "accel": z["imu_accel"], "gyro": z["imu_gyro"],
               "button": z["imu_button"]}
        if "imu_mag" in z:
            imu["mag"], imu["mag_ok"] = z["imu_mag"], z["imu_mag_ok"]
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


def count_blocks(root=DATA_DIR, labels=LABELS):
    counts = dict.fromkeys(labels, 0)
    for path in block_paths(root):
        label = os.path.basename(os.path.dirname(path))
        counts[label] = counts.get(label, 0) + 1
    return counts


# --- keys and layout shared by the camera tools ------------------------------------------------------
# pose_tracker draws its calibration messages in the bottom 70 pixels (pose_tracker.BOTTOM_RESERVED).
# Tools keep their own bottom text at or above TEXT_BOTTOM pixels from the bottom, so nothing covers them.
TEXT_BOTTOM = 80
VK_PERIOD, VK_DECIMAL, VK_F11 = 0xBE, 0x6E, 0x7A


class ToolKeys:
    """Global keys that work from any window, without Enter: "." (start / pause) and F11 (full screen)."""

    def __init__(self, fullscreen):
        import controller
        self.keys = controller.Hotkeys()
        self.fullscreen = fullscreen

    def period(self):
        main, pad = self.keys.pressed(VK_PERIOD), self.keys.pressed(VK_DECIMAL)  # read both every frame
        return main or pad

    def poll_fullscreen(self):
        if self.keys.pressed(VK_F11):
            import pose_tracker
            self.fullscreen = not self.fullscreen
            pose_tracker.set_fullscreen(self.fullscreen)


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
    """Outlined text. On the camera window it is drawn at the window's real size, so it stays sharp (overlay.py)."""
    import overlay
    overlay.outlined_text(frame, text, x, row, scale, colour, thick)
