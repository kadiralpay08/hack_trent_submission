"""Find where a punch starts and ends in the glove's motion data.

The same code runs on live data (punch_tune.py, punch_capture.py, punch_live.py) and on recorded data
(punch_train.py), so the model is trained on exactly the windows it will see during play.

A punch STARTS when acceleration beyond gravity reaches start_g.

It ENDS at whichever comes first:
  - timeout: timeout_s after the start.
  - deceleration: the arm braked (acceleration of at least decel_g against the direction of travel)
    until it was nearly stopped (speed below stop_ratio of the punch's top speed), and then settled
    (acceleration below settle_g for settle_s).

Why the extra conditions on deceleration: an uppercut goes down then up, and a hook goes left, forward,
then right. Each reversal is also strong acceleration against the direction of travel. Ending there would
cut the punch off before the part that matters. A reversal keeps accelerating hard, so it never "settles",
and the small wind-up never reaches min_peak_speed. A real stop does both.

Gravity is tracked while the glove is still and turned with the gyroscope while it moves, so the
acceleration used here is the glove's own motion rather than its tilt.
"""
from dataclasses import asdict, dataclass, field, fields

import numpy as np

G = 9.80665


@dataclass
class SegmentSettings:
    timeout_s: float = 0.40        # the punch ends this long after it starts, unless it stopped sooner
    start_g: float = 2.5           # acceleration beyond gravity that starts a punch
    decel_g: float = 1.5           # braking against the direction of travel, in g, that counts as stopping
    stop_ratio: float = 0.3        # ...and speed must fall below this fraction of the punch's top speed
    settle_g: float = 1.5          # ...and acceleration must then stay below this
    settle_s: float = 0.02         # ...for this long
    min_peak_speed: float = 1.5    # m/s; a motion slower than this cannot end early (an uppercut's dip)
    min_s: float = 0.08            # no early end before this
    rearm_s: float = 0.40          # after a punch, wait this long before another can start
    pre_s: float = 0.25            # data before the start that belongs to the punch (the wind-up)
    still_g: float = 0.10          # the glove counts as still within this of 1 g...
    still_dps: float = 30.0        # ...and rotating slower than this
    ready_s: float = 0.15          # still for this long in total before the first punch can be found
    max_gap_s: float = 0.10        # a longer gap in the data ends the punch (reason "gap")

    @classmethod
    def from_dict(cls, values):
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown segment setting(s) {unknown}; known: {sorted(known)}")
        return cls(**{k: float(v) for k, v in values.items()})

    def to_dict(self):
        return asdict(self)


@dataclass
class Segment:
    start_t: float
    end_t: float = None
    reason: str = ""                 # "decel", "timeout" or "gap"
    peak_g: float = 0.0              # largest acceleration beyond gravity
    peak_speed: float = 0.0          # m/s, estimated
    gravity: np.ndarray = field(default=None, repr=False)  # gravity reading at the start, glove axes, in g

    @property
    def duration(self):
        return None if self.end_t is None else self.end_t - self.start_t


class Segmenter:
    """Feed samples in time order. feed() returns ("start", segment), ("end", segment) or None."""

    def __init__(self, settings=None):
        self.s = settings or SegmentSettings()
        self.gravity = None      # current gravity estimate in glove axes, in g
        self.still_total = 0.0
        self.last_t = None
        self.rearm_until = -1e9
        self.active = None       # the Segment being measured
        self.linear = np.zeros(3)

    @property
    def ready(self):
        return self.gravity is not None and self.still_total >= self.s.ready_s

    def feed(self, t, accel, gyro):
        """t in seconds, accel in g, gyro in deg/s (both length 3, glove axes)."""
        s = self.s
        accel = np.asarray(accel, dtype=float)
        dt = 0.0 if self.last_t is None else t - self.last_t
        self.last_t = t
        if dt < 0 or dt > s.max_gap_s:
            if self.active is not None:
                return self._end(t, "gap")
            dt = 0.0
        rate = np.radians(np.asarray(gyro, dtype=float))

        # Track gravity: average it in while still, and turn it with the glove while moving.
        still = abs(np.linalg.norm(accel) - 1.0) < s.still_g and np.degrees(np.linalg.norm(rate)) < s.still_dps
        if self.gravity is None:
            if still:
                self.gravity = accel / np.linalg.norm(accel)
        else:
            self.gravity = self.gravity - np.cross(rate, self.gravity) * dt
            if still and self.active is None:
                self.gravity += min(1.0, dt / 0.3) * (accel - self.gravity)
            self.gravity /= np.linalg.norm(self.gravity)
        if still:
            self.still_total += dt
        if self.gravity is None:
            return None

        self.linear = accel - self.gravity
        size = float(np.linalg.norm(self.linear))

        if self.active is None:
            if self.ready and t >= self.rearm_until and size >= s.start_g:
                self.active = Segment(start_t=t, peak_g=size, gravity=self.gravity.copy())
                self.velocity = np.zeros(3)
                self.braked, self.settled_for = False, 0.0
                return ("start", self.active)
            return None

        seg = self.active
        seg.peak_g = max(seg.peak_g, size)
        self.velocity = self.velocity - np.cross(rate, self.velocity) * dt + self.linear * G * dt
        speed = float(np.linalg.norm(self.velocity))
        seg.peak_speed = max(seg.peak_speed, speed)
        elapsed = t - seg.start_t

        if speed > 0.05:
            along = float(np.dot(self.linear, self.velocity / speed))
            if along <= -s.decel_g:
                self.braked = True
            elif along >= s.decel_g and speed > s.stop_ratio * seg.peak_speed:
                # speeding up again to real speed: that was a reversal, not a stop. (Braking a little
                # past zero at a real stop also turns the velocity round, but it stays slow.)
                self.braked = False
        stopped = self.braked and seg.peak_speed >= s.min_peak_speed and speed <= s.stop_ratio * seg.peak_speed
        self.settled_for = self.settled_for + dt if stopped and size < s.settle_g else 0.0

        if elapsed >= s.timeout_s:
            return self._end(t, "timeout")
        if elapsed >= s.min_s and self.settled_for >= s.settle_s:
            return self._end(t, "decel")
        return None

    def _end(self, t, reason):
        seg = self.active
        seg.end_t, seg.reason = t, reason
        self.active = None
        self.rearm_until = t + self.s.rearm_s
        return ("end", seg)


def find_segments(t, accel, gyro, settings=None):
    """Run the segmenter over recorded arrays. Returns the finished segments, in order."""
    seg = Segmenter(settings)
    found = []
    for i in range(len(t)):
        event = seg.feed(float(t[i]), accel[i], gyro[i])
        if event and event[0] == "end":
            found.append(event[1])
    return found


def pick_block_segment(segments, press_t, release_t):
    """The punch inside a training block: the strongest segment that started while the button was held."""
    inside = [s for s in segments if press_t <= s.start_t <= release_t]
    return max(inside, key=lambda s: s.peak_g) if inside else None, len(inside)
