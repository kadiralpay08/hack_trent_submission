"""Record punches from punch_stream.ino and reconstruct their paths in 3D.

Install:  pip install numpy matplotlib pyserial

Record:   python punch_tracker.py capture --port COM5 --seconds 30 --out punches.csv
Analyse:  python punch_tracker.py analyze punches.csv
          python punch_tracker.py analyze punches.csv --save punches.png
Live:     python punch_tracker.py live --port COM7 --dry-run
          python punch_tracker.py live --port COM7

Live mode (Windows only) presses w for a straight punch and s for a punch that
also travels up or down, such as an uppercut or body shot. Keys go to whichever
window has focus, so use --dry-run to see the angles and tune --angle first.
Hold still for 2 s at the start, and pause briefly before each punch. Stop with
Ctrl+C.

While recording, hold still for the first 2 seconds, then rest for at least
1 second before and after each punch. Rest periods are what stop the error from
growing, so back-to-back combinations cannot be reconstructed.

Paths are rough estimates: compare shape and direction between punches rather
than trusting exact distances. "Forward" is the direction of the punch's furthest
point, because the sensor cannot measure compass heading.
"""

import argparse
import csv
import struct
import sys
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

# Must match the sensor settings in punch_stream.ino
ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 16.4
FRAME_SIZE = 19
SYNC = b"\xa5\x5a"

CLIP_RAW = 32700  # raw readings beyond this are treated as saturated
GRAVITY = 9.80665

# Detection settings: tune these against your own recordings
STILL_ACCEL_G = 0.15  # still means acceleration within this of 1 g
STILL_GYRO_DPS = 30.0  # and rotation below this
STILL_WINDOW_S = 0.15  # for at least this long
PUNCH_THRESHOLD_G = 2.0  # a motion is a punch if total acceleration deviates this far from 1 g
MAX_PUNCH_S = 2.0  # longer motions are skipped, because drift gets too large
CALIBRATION_MIN_S = 1.0
GRAVITY_WINDOW_S = 0.1  # rest period before a punch used to find "down"

# Live detection settings: tune these with --dry-run
TWO_DIRECTION_ANGLE_DEG = 35.0  # punches this far above or below level press s instead of w
MIN_PUNCH_SPEED_MS = 1.0  # slower motions are ignored, even with a hard jolt
SPEED_DROP_RATIO = 0.8  # the peak has passed once speed falls to this fraction of it
MAX_DECISION_S = 0.4  # give up this long after the threshold if no peak was found
MAX_FRAME_GAP_S = 0.05  # a longer gap in the data means the sensor state is unknown
NOT_READY_REPORT_S = 3.0  # say why punches are not being detected after this long
NO_DATA_REPORT_S = 2.0
KEY_HOLD_S = 0.05
STRAIGHT_KEY = "w"
TWO_DIRECTION_KEY = "s"
SCAN_CODES = {"w": 0x11, "s": 0x1F}  # physical key codes, which games also recognise

MAX_PLOTTED_PUNCHES = 8
PUNCH_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"


@dataclass
class Punch:
    number: int
    start_s: float
    duration_s: float
    peak_g: float
    peak_gyro_dps: float
    reach_m: float
    peak_speed_ms: float
    end_speed_error_ms: float  # NaN when the path was cut short by clipping
    clipped_samples: int
    gap_samples: int
    time_s: np.ndarray
    position_m: np.ndarray  # columns: forward, sideways, up


def parse_frames(buffer):
    """Return complete valid frames from buffer and the bytes left unparsed."""
    frames = []
    position = 0
    while True:
        position = buffer.find(SYNC, position)
        if position < 0:
            # Keep a trailing first sync byte, in case the second is still arriving
            return frames, buffer[-1:] if buffer[-1:] == SYNC[:1] else bytearray()
        if position + FRAME_SIZE > len(buffer):
            return frames, buffer[position:]

        payload = buffer[position + 2:position + 18]
        checksum = 0
        for byte in payload:
            checksum ^= byte
        if checksum != buffer[position + 18]:
            position += 1
            continue

        timestamp = struct.unpack("<I", payload[:4])[0]
        frames.append((timestamp, *struct.unpack(">6h", payload[4:])))
        position += FRAME_SIZE


def capture(port, baud, seconds, out_path):
    import serial

    with serial.Serial(port, baud, timeout=0.1) as connection, \
            open(out_path, "w", newline="") as out_file:
        writer = csv.writer(out_file)
        writer.writerow(["t_us", "ax", "ay", "az", "gx", "gy", "gz"])

        print("Connecting (the Uno resets when the port opens)...")
        connect_deadline = time.monotonic() + 5.0
        buffer = bytearray()
        all_frames = []
        recording_until = None

        while recording_until is None or time.monotonic() < recording_until:
            buffer += connection.read(4096)
            frames, buffer = parse_frames(buffer)
            if frames:
                writer.writerows(frames)
                all_frames.extend(frames)
                if recording_until is None:
                    recording_until = time.monotonic() + seconds
                    print(f"Recording for {seconds:g} s. "
                          "Hold still for 2 s, then start punching.")
            elif recording_until is None and time.monotonic() > connect_deadline:
                text = bytes(buffer).decode("ascii", "replace").strip()
                sys.exit("No data received. Check the port and baud rate, that "
                         "punch_stream.ino is uploaded, and that the Serial "
                         f"Monitor is closed. Received: {text[:100]!r}")

    data = np.array(all_frames, dtype=np.int64)
    t = np.unwrap(data[:, 0].astype(float), period=2.0**32) / 1e6
    gaps = np.diff(t)
    print(f"Saved {len(data)} samples to {out_path} "
          f"({1 / np.median(gaps):.0f} Hz).")
    print(f"Timing gaps: {int(np.sum(gaps > 1.5 * np.median(gaps)))}")
    print(f"Clipped accel samples: {int(np.sum(np.abs(data[:, 1:4]).max(axis=1) >= CLIP_RAW))}")
    print(f"Clipped gyro samples: {int(np.sum(np.abs(data[:, 4:7]).max(axis=1) >= CLIP_RAW))}")


def load_recording(path):
    data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if len(data) < 2:
        sys.exit(f"{path} does not contain enough samples.")
    t = np.unwrap(data[:, 0], period=2.0**32) / 1e6
    return t - t[0], data[:, 1:4], data[:, 4:7]


def find_still(t, accel_g, gyro_dps):
    rate = 1.0 / np.median(np.diff(t))
    window = max(1, int(STILL_WINDOW_S * rate)) | 1
    accel_excess = np.abs(np.linalg.norm(accel_g, axis=1) - 1.0)
    instant = (accel_excess < STILL_ACCEL_G) & (np.linalg.norm(gyro_dps, axis=1) < STILL_GYRO_DPS)
    return np.convolve(instant.astype(int), np.ones(window, dtype=int), "same") == window


def runs_of(mask):
    """Return (start, end) index pairs for each run of True, with end exclusive."""
    edges = np.diff(np.concatenate(([0], mask.astype(int), [0])))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def quat_multiply(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw])


def quat_rotate(q, v):
    u = q[1:]
    return v + 2.0 * np.cross(u, np.cross(u, v) + q[0] * v)


def quat_to_world_up(specific_force):
    """Return the rotation taking the at-rest accelerometer reading to world +Z."""
    f = specific_force / np.linalg.norm(specific_force)
    if f[2] < -0.999999:
        return np.array([0.0, 1.0, 0.0, 0.0])
    q = np.array([1.0 + f[2], f[1], -f[0], 0.0])
    return q / np.linalg.norm(q)


def quat_step(q, mean_rate, dt):
    """Advance orientation q by a body rotation rate (rad/s) held for dt seconds."""
    half_angle = 0.5 * np.linalg.norm(mean_rate) * dt
    axis = mean_rate / np.linalg.norm(mean_rate) if half_angle > 0 else mean_rate
    q = quat_multiply(q, np.concatenate(([np.cos(half_angle)], np.sin(half_angle) * axis)))
    return q / np.linalg.norm(q)


def cumulative_trapezoid(values, t):
    steps = 0.5 * (values[1:] + values[:-1]) * np.diff(t)[:, None]
    return np.vstack((np.zeros(values.shape[1]), np.cumsum(steps, axis=0)))


def reconstruct(number, t, accel_raw, gyro_raw, gyro_bias_dps, start, end):
    accel_ms2 = accel_raw / ACCEL_LSB_PER_G * GRAVITY
    gyro_rad = np.radians(gyro_raw / GYRO_LSB_PER_DPS - gyro_bias_dps)

    rate = 1.0 / np.median(np.diff(t))
    gravity_samples = max(1, int(GRAVITY_WINDOW_S * rate))
    specific_force = accel_ms2[max(0, start - gravity_samples):start].mean(axis=0)
    gravity_world = np.array([0.0, 0.0, np.linalg.norm(specific_force)])

    saturated = (np.abs(accel_raw[start:end]).max(axis=1) >= CLIP_RAW) | \
                (np.abs(gyro_raw[start:end]).max(axis=1) >= CLIP_RAW)
    clipped_samples = int(saturated.sum())
    # Clipped readings are wrong by an unknown amount, so the path stops there
    stop = start + int(np.argmax(saturated)) if clipped_samples else end
    if stop - start < 3:
        return None

    q = quat_to_world_up(specific_force)
    linear_world = np.zeros((stop - start, 3))
    for k in range(stop - start):
        i = start + k
        if k:
            q = quat_step(q, 0.5 * (gyro_rad[i - 1] + gyro_rad[i]), t[i] - t[i - 1])
        linear_world[k] = quat_rotate(q, accel_ms2[i]) - gravity_world

    time_s = t[start:stop] - t[start]
    velocity = cumulative_trapezoid(linear_world, time_s)
    end_speed_error = float("nan")
    if not clipped_samples:
        # The glove is at rest at both ends, so any leftover velocity is accumulated error
        end_speed_error = float(np.linalg.norm(velocity[-1]))
        velocity -= np.outer(time_s / time_s[-1], velocity[-1])

    position = cumulative_trapezoid(velocity, time_s)
    distances = np.linalg.norm(position, axis=1)
    furthest = position[int(np.argmax(distances))]
    heading = np.arctan2(furthest[1], furthest[0])
    cos_h, sin_h = np.cos(-heading), np.sin(-heading)
    aligned = np.column_stack((cos_h * position[:, 0] - sin_h * position[:, 1],
                               sin_h * position[:, 0] + cos_h * position[:, 1],
                               position[:, 2]))

    dt = np.diff(t[start:stop])
    return Punch(
        number=number,
        start_s=float(t[start]),
        duration_s=float(t[stop - 1] - t[start]),
        peak_g=float(np.max(np.abs(np.linalg.norm(accel_raw[start:end], axis=1) / ACCEL_LSB_PER_G - 1.0))),
        peak_gyro_dps=float(np.max(np.linalg.norm(gyro_raw[start:end], axis=1) / GYRO_LSB_PER_DPS)),
        reach_m=float(distances.max()),
        peak_speed_ms=float(np.linalg.norm(velocity, axis=1).max()),
        end_speed_error_ms=end_speed_error,
        clipped_samples=clipped_samples,
        gap_samples=int(np.sum(dt > 1.5 / rate)),
        time_s=time_s,
        position_m=aligned,
    )


def find_punches(t, accel_raw, gyro_raw):
    accel_g = accel_raw / ACCEL_LSB_PER_G
    gyro_dps = gyro_raw / GYRO_LSB_PER_DPS
    still = find_still(t, accel_g, gyro_dps)

    calibration = next(((a, b) for a, b in runs_of(still)
                        if t[b - 1] - t[a] >= CALIBRATION_MIN_S), None)
    if calibration is None:
        sys.exit(f"No still period of at least {CALIBRATION_MIN_S:g} s found. "
                 "Hold the glove still for 2 s at the start of the recording.")
    gyro_bias_dps = gyro_dps[calibration[0]:calibration[1]].mean(axis=0)

    punches = []
    accel_excess = np.abs(np.linalg.norm(accel_g, axis=1) - 1.0)
    for start, end in runs_of(~still):
        if accel_excess[start:end].max() < PUNCH_THRESHOLD_G:
            continue
        if start == 0 or end == len(t):
            print(f"Skipped motion at {t[start]:.2f} s: recording starts or ends during it.")
            continue
        if t[end - 1] - t[start] > MAX_PUNCH_S:
            print(f"Skipped motion at {t[start]:.2f} s: lasted over {MAX_PUNCH_S:g} s without a rest.")
            continue
        punch = reconstruct(len(punches) + 1, t, accel_raw, gyro_raw, gyro_bias_dps, start, end)
        if punch is None:
            print(f"Skipped motion at {t[start]:.2f} s: sensor clipped almost immediately.")
            continue
        punches.append(punch)
    return punches


def print_report(punches):
    if not punches:
        print(f"No punches found. Nothing exceeded {PUNCH_THRESHOLD_G:g} g between rest periods.")
        return

    print(f"{'#':>2} {'start s':>8} {'length s':>8} {'peak g':>7} {'gyro d/s':>8} "
          f"{'reach m':>8} {'speed m/s':>9} {'end err m/s':>11}  notes")
    for p in punches:
        notes = []
        if p.clipped_samples:
            notes.append(f"CLIPPED, path stops at first of {p.clipped_samples} clipped samples")
        if p.gap_samples:
            notes.append(f"{p.gap_samples} timing gaps")
        error = "n/a" if np.isnan(p.end_speed_error_ms) else f"{p.end_speed_error_ms:.2f}"
        print(f"{p.number:>2} {p.start_s:>8.2f} {p.duration_s:>8.2f} {p.peak_g:>7.1f} "
              f"{p.peak_gyro_dps:>8.0f} {p.reach_m:>8.2f} {p.peak_speed_ms:>9.2f} {error:>11}  "
              f"{'; '.join(notes)}")
    print("\n'end err' is the leftover speed when the glove stopped, which should be 0. "
          "The larger it is, the less reliable that path is.")


def plot(t, accel_raw, punches, save_path):
    import matplotlib
    if save_path:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if len(punches) > MAX_PLOTTED_PUNCHES:
        print(f"Plotting the first {MAX_PLOTTED_PUNCHES} punches. "
              "The table above includes all of them.")
    shown = punches[:MAX_PLOTTED_PUNCHES]

    plt.rcParams.update({"font.size": 10, "text.color": TEXT_PRIMARY,
                         "axes.labelcolor": TEXT_SECONDARY,
                         "xtick.color": TEXT_SECONDARY, "ytick.color": TEXT_SECONDARY})
    fig = plt.figure(figsize=(13, 5.5), facecolor=SURFACE)

    path_ax = fig.add_subplot(1, 2, 1, projection="3d", facecolor=SURFACE)
    for i, p in enumerate(shown):
        x, y, z = p.position_m.T
        path_ax.plot(x, y, z, color=PUNCH_COLORS[i], linewidth=2, label=f"Punch {p.number}")
        path_ax.scatter(x[-1], y[-1], z[-1], color=PUNCH_COLORS[i], s=30, depthshade=False)

    all_points = np.vstack([p.position_m for p in shown] + [np.zeros((1, 3))])
    center = 0.5 * (all_points.max(axis=0) + all_points.min(axis=0))
    half_range = max(0.5 * (all_points.max(axis=0) - all_points.min(axis=0)).max(), 0.01)
    path_ax.set_xlim(center[0] - half_range, center[0] + half_range)
    path_ax.set_ylim(center[1] - half_range, center[1] + half_range)
    path_ax.set_zlim(center[2] - half_range, center[2] + half_range)
    path_ax.set_box_aspect((1, 1, 1))
    path_ax.set_xlabel("Forward (m)")
    path_ax.set_ylabel("Sideways (m)")
    path_ax.set_zlabel("Up (m)")
    for axis in (path_ax.xaxis, path_ax.yaxis, path_ax.zaxis):
        axis.set_pane_color((1, 1, 1, 0))
        axis._axinfo["grid"]["color"] = GRID
    path_ax.set_title("Punch paths, each starting at 0, 0, 0", loc="left", fontsize=12)
    if len(shown) >= 2:
        path_ax.legend(loc="upper left", frameon=False, labelcolor=TEXT_SECONDARY)

    time_ax = fig.add_subplot(1, 2, 2, facecolor=SURFACE)
    magnitude = np.linalg.norm(accel_raw, axis=1) / ACCEL_LSB_PER_G
    time_ax.plot(t, magnitude, color=TEXT_SECONDARY, linewidth=1)
    top = max(magnitude.max() * 1.1, 2.0)
    time_ax.set_ylim(0, top)
    time_ax.set_xlim(t[0], t[-1])
    for i, p in enumerate(shown):
        time_ax.axvspan(p.start_s, p.start_s + p.duration_s, color=PUNCH_COLORS[i], alpha=0.25, linewidth=0)
        time_ax.text(p.start_s + p.duration_s / 2, top * 0.97, str(p.number),
                     ha="center", va="top", color=TEXT_PRIMARY)
    time_ax.set_xlabel("Time (s)")
    time_ax.set_ylabel("Acceleration magnitude (g)")
    time_ax.grid(axis="y", color=GRID, linewidth=0.8)
    time_ax.set_axisbelow(True)
    for side in ("top", "right"):
        time_ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        time_ax.spines[side].set_color(GRID)
    time_ax.set_title("Detected punches in the recording", loc="left", fontsize=12)

    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, facecolor=SURFACE)
        print(f"Saved plot to {save_path}")
    else:
        plt.show()


@dataclass
class LivePunch:
    key: str
    angle_deg: float  # above (+) or below (-) level, at peak speed
    speed_ms: float
    reaction_s: float  # from the start of the motion to this decision


class LiveDetector:
    """Classify punches one sample at a time, as soon as they pass peak speed.

    Direction is read at peak speed rather than when the glove stops, because that
    is both sooner and more accurate: error has had less time to grow, and the
    retraction has not started.
    """

    CALIBRATING, WAITING, READY, TRACKING = range(4)

    def __init__(self, angle_deg=TWO_DIRECTION_ANGLE_DEG):
        self.angle_deg = angle_deg
        self.state = self.CALIBRATING
        self.gyro_bias_dps = None
        self.last_us = None
        self._reset_rest()
        self._reset_not_ready()

    def _reset_not_ready(self):
        self.not_ready_s = 0.0
        self.not_ready_accel_g = 0.0
        self.not_ready_gyro_dps = 0.0
        self.not_ready_count = 0

    def _report_not_ready(self, dt, accel_g, gyro_dps):
        """Explain a long wait for rest, which would otherwise look like a silent hang."""
        if self.state not in (self.CALIBRATING, self.WAITING):
            self._reset_not_ready()
            return
        self.not_ready_s += dt
        self.not_ready_accel_g += accel_g
        self.not_ready_gyro_dps += gyro_dps
        self.not_ready_count += 1
        if self.not_ready_s < NOT_READY_REPORT_S:
            return
        accel = self.not_ready_accel_g / self.not_ready_count
        gyro = self.not_ready_gyro_dps / self.not_ready_count
        hint = ("The sensor looks like it reset, so check its wiring."
                if accel < 0.5 or accel > 2.0 else "Hold still briefly to re-arm.")
        print(f"Not ready: average accel {accel:.2f} g (needs about 1), average rotation "
              f"{gyro:.0f} deg/s (needs under {STILL_GYRO_DPS:g}). {hint}")
        self._reset_not_ready()

    def _reset_rest(self):
        self.still_s = 0.0
        self.rest_accel = deque()  # (seconds since rest began, accel) for finding "down"
        self.gyro_sum = np.zeros(3)
        self.gyro_count = 0

    def feed(self, frame):
        """Process one (t_us, ax, ay, az, gx, gy, gz) frame. Returns a LivePunch or None."""
        # Timestamps are uint32 microseconds, which wrap every 71 minutes
        dt = 0.0 if self.last_us is None else ((frame[0] - self.last_us) & 0xFFFFFFFF) / 1e6
        self.last_us = frame[0]
        if dt > MAX_FRAME_GAP_S:
            self._reset_rest()
            self.state = self.CALIBRATING if self.gyro_bias_dps is None else self.WAITING
            return None

        accel_raw = np.array(frame[1:4], dtype=float)
        gyro_raw = np.array(frame[4:7], dtype=float)
        accel_g = np.linalg.norm(accel_raw) / ACCEL_LSB_PER_G
        accel_excess = abs(accel_g - 1.0)
        gyro_dps = gyro_raw / GYRO_LSB_PER_DPS
        still = accel_excess < STILL_ACCEL_G and np.linalg.norm(gyro_dps) < STILL_GYRO_DPS
        self._report_not_ready(dt, accel_g, np.linalg.norm(gyro_dps))

        if self.state == self.READY and not still:
            self._begin_tracking(accel_raw, gyro_dps)

        if still:
            self.still_s += dt
            self.rest_accel.append((self.still_s, accel_raw))
            while self.rest_accel[0][0] < self.still_s - GRAVITY_WINDOW_S:
                self.rest_accel.popleft()
            self.gyro_sum += gyro_dps
            self.gyro_count += 1
        else:
            self._reset_rest()

        if self.state == self.CALIBRATING:
            if self.still_s >= CALIBRATION_MIN_S:
                self.gyro_bias_dps = self.gyro_sum / self.gyro_count
                self.state = self.READY
                print("Ready. Pause briefly before each punch.")
        elif self.state == self.WAITING:
            if self.still_s >= STILL_WINDOW_S:
                self.state = self.READY
        elif self.state == self.TRACKING and dt > 0:
            return self._track(dt, accel_raw, gyro_raw, gyro_dps, accel_excess)
        return None

    def _begin_tracking(self, accel_raw, gyro_dps):
        rest_force = np.mean([a for _, a in self.rest_accel], axis=0) / ACCEL_LSB_PER_G * GRAVITY
        self.gravity_world = np.array([0.0, 0.0, np.linalg.norm(rest_force)])
        self.q = quat_to_world_up(rest_force)
        self.prev_rate = np.radians(gyro_dps - self.gyro_bias_dps)
        self.prev_linear = quat_rotate(self.q, accel_raw / ACCEL_LSB_PER_G * GRAVITY) - self.gravity_world
        self.velocity = np.zeros(3)
        self.peak_velocity = np.zeros(3)
        self.peak_speed = 0.0
        self.elapsed_s = 0.0
        self.armed_at_s = None
        self.state = self.TRACKING

    def _track(self, dt, accel_raw, gyro_raw, gyro_dps, accel_excess):
        self.elapsed_s += dt
        if self.armed_at_s is None and accel_excess >= PUNCH_THRESHOLD_G:
            self.armed_at_s = self.elapsed_s
        clipped = max(np.abs(accel_raw).max(), np.abs(gyro_raw).max()) >= CLIP_RAW

        if not clipped:  # clipped readings are wrong by an unknown amount
            rate = np.radians(gyro_dps - self.gyro_bias_dps)
            self.q = quat_step(self.q, 0.5 * (self.prev_rate + rate), dt)
            linear = quat_rotate(self.q, accel_raw / ACCEL_LSB_PER_G * GRAVITY) - self.gravity_world
            self.velocity = self.velocity + 0.5 * (self.prev_linear + linear) * dt
            self.prev_rate, self.prev_linear = rate, linear
            speed = np.linalg.norm(self.velocity)
            if speed > self.peak_speed:
                self.peak_speed = speed
                self.peak_velocity = self.velocity

        if self.armed_at_s is None:
            if self.still_s >= STILL_WINDOW_S:
                self.state = self.READY  # just a small movement, not a punch
            elif self.elapsed_s > MAX_PUNCH_S:
                self.state = self.WAITING
            return None

        fast_enough = self.peak_speed >= MIN_PUNCH_SPEED_MS
        past_peak = fast_enough and np.linalg.norm(self.velocity) < SPEED_DROP_RATIO * self.peak_speed
        timed_out = self.elapsed_s - self.armed_at_s >= MAX_DECISION_S
        if not (clipped or past_peak or timed_out):
            return None

        self.state = self.WAITING
        if not fast_enough:
            return None
        vx, vy, vz = self.peak_velocity
        angle = float(np.degrees(np.arctan2(vz, np.hypot(vx, vy))))
        key = TWO_DIRECTION_KEY if abs(angle) >= self.angle_deg else STRAIGHT_KEY
        return LivePunch(key, angle, float(self.peak_speed), self.elapsed_s)


def press_key(key):
    """Tap a key using Windows SendInput. Returns False if Windows rejected it."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):  # never sent, but it sets the size of the union
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 1, 0x0002, 0x0008

    def send(flags):
        event = INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(0, SCAN_CODES[key], flags, 0, 0)))
        return ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1

    pressed = send(KEYEVENTF_SCANCODE)
    time.sleep(KEY_HOLD_S)
    return send(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP) and pressed


def live(port, baud, angle_deg, dry_run):
    import serial

    detector = LiveDetector(angle_deg)
    with serial.Serial(port, baud, timeout=0.1) as connection:
        print("Connecting (the Uno resets when the port opens)...")
        connect_deadline = time.monotonic() + 5.0
        buffer = bytearray()
        connected = False
        count = 0
        last_data = time.monotonic()
        no_data_reported = False
        try:
            while True:
                # Read only what has arrived, because read(4096) would wait out the timeout
                buffer += connection.read(connection.in_waiting or 1)
                frames, buffer = parse_frames(buffer)
                if frames:
                    last_data = time.monotonic()
                    no_data_reported = False
                elif connected and not no_data_reported and time.monotonic() - last_data > NO_DATA_REPORT_S:
                    no_data_reported = True
                    print("No data from the Arduino. Check the sensor wiring.")
                if frames and not connected:
                    connected = True
                    print("Connected. Hold still for 2 s." + (" Dry run: no keys will be pressed." if dry_run else ""))
                elif not connected and time.monotonic() > connect_deadline:
                    text = bytes(buffer).decode("ascii", "replace").strip()
                    sys.exit("No data received. Check the port and baud rate, that "
                             "punch_stream.ino is uploaded, and that the Serial "
                             f"Monitor is closed. Received: {text[:100]!r}")
                for frame in frames:
                    punch = detector.feed(frame)
                    if punch is None:
                        continue
                    count += 1
                    print(f"Punch {count}: {punch.key}  angle {punch.angle_deg:+4.0f} deg  "
                          f"speed {punch.speed_ms:.1f} m/s  decided after {punch.reaction_s * 1000:.0f} ms")
                    if not dry_run and not press_key(punch.key):
                        print("Windows blocked the key press. If the target app runs as "
                              "administrator, run this from an administrator terminal too.")
        except KeyboardInterrupt:
            print(f"\nStopped after {count} punches.")


def main():
    parser = argparse.ArgumentParser(description="Record and reconstruct punches from an MPU6050.")
    commands = parser.add_subparsers(dest="command", required=True)

    capture_parser = commands.add_parser("capture", help="record data from the Arduino to a CSV file")
    capture_parser.add_argument("--port", required=True, help="serial port, for example COM5")
    capture_parser.add_argument("--baud", type=int, default=500000)
    capture_parser.add_argument("--seconds", type=float, default=30.0)
    capture_parser.add_argument("--out", default="punches.csv")

    live_parser = commands.add_parser("live", help="detect punches as they happen and press w or s (Windows only)")
    live_parser.add_argument("--port", required=True, help="serial port, for example COM7")
    live_parser.add_argument("--baud", type=int, default=500000)
    live_parser.add_argument("--angle", type=float, default=TWO_DIRECTION_ANGLE_DEG,
                             help="degrees above or below level at which a punch presses s instead of w")
    live_parser.add_argument("--dry-run", action="store_true", help="print punches without pressing keys")

    analyze_parser = commands.add_parser("analyze", help="detect punches in a CSV file and plot their paths")
    analyze_parser.add_argument("recording")
    analyze_parser.add_argument("--save", help="save the plot to this image file instead of showing it")

    args = parser.parse_args()
    if args.command == "capture":
        capture(args.port, args.baud, args.seconds, args.out)
    elif args.command == "live":
        live(args.port, args.baud, args.angle, args.dry_run)
    else:
        t, accel_raw, gyro_raw = load_recording(args.recording)
        punches = find_punches(t, accel_raw, gyro_raw)
        print_report(punches)
        if punches:
            plot(t, accel_raw, punches, args.save)


if __name__ == "__main__":
    main()
