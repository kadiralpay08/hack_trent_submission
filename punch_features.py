"""Turn one punch (a Segment plus the glove and camera data around it) into numbers for the random forest.

Used identically by punch_train.py and punch_live.py. Only data up to the end of the punch is used, so
live classification never waits for anything training had but play does not.

The window is [start - pre_s, end]. The part before the start is the wind-up, such as an uppercut's dip.

Glove features (prefix imu_) are in the glove's own axes, so the sensor must be mounted the same way for
training and play. "Vertical" means along gravity as measured just before the punch.
Magnetometer features (prefix imu_mag_) are only made for a glove that has one (the S3 glove's MPU-9250). They
describe how the glove turned relative to the Earth's magnetic field, which the gyro cannot drift on.
Camera features (prefix pose_) are for the glove's arm (side "L" or "R"), in a body frame: "right" =
toward the middle of the body (for the right arm this is mirrored, so it means toward the left), up,
and forward, all in shoulder-widths. Mirroring keeps "a hook swings out, then in" the same numbers for
both arms. They are NaN when no body was seen; the random forest handles missing values.

The motions they are built to tell apart (PROJECT.md):
  jab/cross straight forward
  hook      out, then forward, then across   -> wrist "right" goes down first, then up past the start
            (left: left, forward, right. right: right, forward, left)
  uppercut  down, then up                    -> wrist "up" dips first, then rises; vertical accel - then +
"""
import numpy as np

NOSE, L_SH, R_SH, L_EL, L_WR, L_HIP = 0, 11, 12, 13, 15, 23
R_EL, R_WR, R_HIP = 14, 16, 24
ARM_POINTS = {"L": (L_SH, L_EL, L_WR, L_HIP), "R": (R_SH, R_EL, R_WR, R_HIP)}  # shoulder, elbow, wrist, hip
CLIP_G, CLIP_DPS = 15.9, 1990.0
MAG_NAMES = ["imu_mag_turn", "imu_mag_spread"] + [f"imu_mag_{k}_{a}" for k in ("start", "change") for a in "xyz"]


def _stats(prefix, values, out):
    """mean, std, min, max, and the time (0 to 1 through the window) of the min and the max."""
    n = len(values)
    out[prefix + "_mean"] = float(np.mean(values))
    out[prefix + "_std"] = float(np.std(values))
    out[prefix + "_min"] = float(np.min(values))
    out[prefix + "_max"] = float(np.max(values))
    out[prefix + "_tmin"] = float(np.argmin(values) / max(n - 1, 1))
    out[prefix + "_tmax"] = float(np.argmax(values) / max(n - 1, 1))


def _thirds(prefix, values, out):
    for i, part in enumerate(np.array_split(values, 3)):
        out[f"{prefix}_p{i + 1}"] = float(np.mean(part)) if len(part) else 0.0


def imu_features(imu, seg, pre_s):
    t, accel, gyro = imu["t"], imu["accel"], imu["gyro"]
    out = {}
    main = (t >= seg.start_t) & (t <= seg.end_t)
    pre = (t >= seg.start_t - pre_s) & (t < seg.start_t)
    if main.sum() < 5:
        return None
    up = seg.gravity / np.linalg.norm(seg.gravity)

    # Linear acceleration: subtract gravity, turning it with the glove during the punch.
    idx = np.flatnonzero(main)
    g = up.copy()
    linear = np.empty((len(idx), 3))
    for k, i in enumerate(idx):
        if k:
            dt = t[i] - t[idx[k - 1]]
            g = g - np.cross(np.radians(gyro[i]), g) * dt
            g /= np.linalg.norm(g)
        linear[k] = accel[i] - g
    rot = gyro[main]
    pre_linear = accel[pre] - up if pre.any() else np.zeros((1, 3))
    pre_rot = gyro[pre] if pre.any() else np.zeros((1, 3))

    out["imu_duration"] = float(seg.end_t - seg.start_t)
    out["imu_ended_by_decel"] = float(seg.reason == "decel")
    out["imu_peak_g"] = float(seg.peak_g)
    out["imu_peak_speed"] = float(seg.peak_speed)
    for j, axis in enumerate("xyz"):
        _stats(f"imu_a{axis}", linear[:, j], out)
        _thirds(f"imu_a{axis}", linear[:, j], out)
        _stats(f"imu_g{axis}", rot[:, j], out)
        _thirds(f"imu_g{axis}", rot[:, j], out)
        out[f"imu_a{axis}_pre"] = float(np.mean(pre_linear[:, j]))
        out[f"imu_g{axis}_pre"] = float(np.mean(pre_rot[:, j]))
        out[f"imu_turn_{axis}"] = float(np.sum(rot[:, j]) * np.median(np.diff(t[main])))  # degrees turned
        out[f"imu_tilt_{axis}"] = float(up[j])  # how the glove was held before the punch

    # Along gravity: an uppercut is down (negative) then up (positive).
    vertical = np.concatenate([pre_linear @ up, linear @ up])
    horizontal = np.linalg.norm(linear - np.outer(linear @ up, up), axis=1)
    _stats("imu_vert", vertical, out)
    _thirds("imu_vert", linear @ up, out)
    out["imu_vert_pre"] = float(np.mean(pre_linear @ up))
    # uppercut: how far it went DOWN before its strongest push UP (the brake at the top comes after)
    top = int(np.argmax(vertical))
    out["imu_dip_before_up"] = float(max(0.0, -np.min(vertical[:top + 1])))
    out["imu_up_after_dip"] = float(vertical[top] - np.min(vertical[:top + 1]))
    out["imu_horiz_max"] = float(np.max(horizontal))
    out["imu_horiz_mean"] = float(np.mean(horizontal))
    out["imu_vert_share"] = float(np.mean(np.abs(linear @ up)) / (np.mean(np.linalg.norm(linear, axis=1)) + 1e-6))
    size = np.linalg.norm(linear, axis=1)
    out["imu_lin_mean"] = float(np.mean(size))
    out["imu_rot_max"] = float(np.max(np.linalg.norm(rot, axis=1)))
    out["imu_rot_mean"] = float(np.mean(np.linalg.norm(rot, axis=1)))
    out["imu_clipped"] = float(np.mean((np.abs(accel[main]) >= CLIP_G).any(axis=1) | (np.abs(rot) >= CLIP_DPS).any(axis=1)))
    if "mag" in imu:
        out.update(mag_features(imu, main, pre))
    return out


def _unit(v):
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def mag_features(imu, main, pre):
    """How the glove turned relative to the Earth's magnetic field. NaN if the magnetometer was not
    working for most of the punch. The field's direction in glove axes says how the glove is turned;
    its change over the punch is a rotation the gyro cannot drift on."""
    out = dict.fromkeys(MAG_NAMES, float("nan"))
    ok = imu["mag_ok"]
    window = main | pre
    if ok[window].mean() < 0.8 or ok[main].sum() < 5:
        return out
    dirs = _unit(imu["mag"][main & ok])
    k = max(1, len(dirs) // 5)
    before = imu["mag"][pre & ok]
    first = _unit(before.mean(axis=0)) if len(before) else _unit(dirs[:k].mean(axis=0))
    last = _unit(dirs[-k:].mean(axis=0))
    out["imu_mag_turn"] = float(np.degrees(np.arccos(np.clip(first @ last, -1, 1))))
    mean = _unit(dirs.mean(axis=0))
    out["imu_mag_spread"] = float(np.degrees(np.mean(np.arccos(np.clip(dirs @ mean, -1, 1)))))
    for j, a in enumerate("xyz"):
        out[f"imu_mag_start_{a}"] = float(first[j])
        out[f"imu_mag_change_{a}"] = float(last[j] - first[j])
    return out


def _angle(a, b, c):
    """Angle at b, in degrees, for each frame."""
    u, v = a - b, c - b
    cos = np.sum(u * v, axis=1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1) + 1e-9)
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


POSE_SERIES = ("right", "up", "fwd", "elbow", "shoulder")
POSE_STATS = ("first", "last", "min", "max", "change", "tmin", "tmax")


def pose_feature_names():
    names = [f"pose_{s}_{k}" for s in POSE_SERIES for k in POSE_STATS]
    return names + ["pose_out_left", "pose_back_right", "pose_dip", "pose_rise",
                    "pose_speed_max", "pose_low_start", "pose_wrist_vis", "pose_frames"]


def pose_features(pose, seg, pre_s, camera_lag_s=0.0, side="L"):
    SH, EL, WR, HIP = ARM_POINTS[side]
    mirror = -1.0 if side == "R" else 1.0  # so "right" means toward the middle of the body for both arms
    names = pose_feature_names()
    out = dict.fromkeys(names, float("nan"))
    if pose is None or len(pose["t"]) == 0:
        out["pose_frames"] = 0.0
        return out
    t = pose["t"] - camera_lag_s
    keep = (t >= seg.start_t - pre_s) & (t <= seg.end_t)
    out["pose_frames"] = float(keep.sum())
    if keep.sum() < 3:
        return out
    w, vis, t = pose["world"][keep], pose["vis"][keep], t[keep]

    sw = np.linalg.norm(w[:, R_SH] - w[:, L_SH], axis=1).mean() + 1e-6
    right = (w[:, R_SH] - w[:, L_SH]).mean(axis=0)
    right[1] = 0.0
    right /= np.linalg.norm(right) + 1e-9
    up = np.array([0.0, -1.0, 0.0])  # MediaPipe world y points down
    fwd = np.cross(up, right)
    rel = (w[:, WR] - w[:, SH]) / sw
    series = {
        "right": mirror * (rel @ right), "up": rel @ up, "fwd": rel @ fwd,
        "elbow": _angle(w[:, SH], w[:, EL], w[:, WR]),
        "shoulder": _angle(w[:, HIP], w[:, SH], w[:, EL]),
    }
    n = keep.sum()
    for name, v in series.items():
        out[f"pose_{name}_first"], out[f"pose_{name}_last"] = float(v[0]), float(v[-1])
        out[f"pose_{name}_min"], out[f"pose_{name}_max"] = float(v.min()), float(v.max())
        out[f"pose_{name}_change"] = float(v[-1] - v[0])
        out[f"pose_{name}_tmin"] = float(np.argmin(v) / (n - 1))
        out[f"pose_{name}_tmax"] = float(np.argmax(v) / (n - 1))
    r, u = series["right"], series["up"]
    out["pose_out_left"] = float(r[0] - r.min())             # hook: swings out to the left first...
    out["pose_back_right"] = float(r[-1] - r.min())          # ...then comes back across to the right
    out["pose_dip"] = float(u[0] - u.min())                  # uppercut: drops first...
    out["pose_rise"] = float(u.max() - u.min())              # ...then rises
    steps = np.linalg.norm(np.diff(rel, axis=0), axis=1) / np.maximum(np.diff(t), 1e-3)
    out["pose_speed_max"] = float(steps.max()) if len(steps) else float("nan")
    out["pose_low_start"] = float(w[0, WR, 1] > w[0, EL, 1])  # wrist below elbow at the start
    out["pose_wrist_vis"] = float(vis[:, WR].mean())
    return out


def extract(imu, pose, seg, pre_s, camera_lag_s=0.0, side="L"):
    """All features for one punch, as {name: value}, or None if there is too little glove data.
    side is the glove's arm ("L" or "R"), for the camera features."""
    imu_part = imu_features(imu, seg, pre_s)
    if imu_part is None:
        return None
    return {**imu_part, **pose_features(pose, seg, pre_s, camera_lag_s, side)}


def select(names, feature_set):
    """Names to use for 'imu', 'pose' or 'both'."""
    if feature_set == "both":
        return list(names)
    return [n for n in names if n.startswith(feature_set + "_")]
