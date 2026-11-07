"""Decide when a punch happened and what kind it was, from the wrist-motion numbers pose_tracker measures.

pose_tracker gives, every frame and for each hand: the wrist speed, and how far the wrist moved
(extension, sideways, up) over the last few frames. This module turns that stream into punch events.

How it differs from the check built into pose_tracker.py, which looks at one frame at a time:
  - A punch is a burst. We wait until the hand has been fast for a few frames and its speed has just
    started to fall (the peak has passed), then judge the whole burst. Judging the first fast frame,
    when the hand has barely moved, mislabels punches.
  - People wind up before a punch (the hand drops or pulls back) and swing back to guard after it.
    Those bursts match no punch, so they are ignored and do not stop the real punch that follows.
  - One punch locks that hand out briefly, so the swing back to guard is not counted as another punch.
  - Raising both hands to guard at once looks like two uppercuts. It is ignored.
  - Straight punches (jab, cross) are not detected. A webcam facing you cannot see an arm extending
    straight toward it: in recorded sessions the arm moved only 0.1 shoulder-widths and its speed was
    no higher than the camera's own jitter. Set straight_ext to try anyway.

The numbers are shoulder-widths and seconds, taken from a recorded session. Tune with tune_punches.py
and replay_punches.py.
"""
from dataclasses import dataclass


@dataclass
class Settings:
    start_speed: float = 5.0    # wrist speed that starts a burst (jitter at rest reaches about 3 to 4)
    start_frames: int = 2       # ...and must hold for this many frames in a row
    drop_ratio: float = 0.8     # the peak has passed once speed falls to this fraction of it
    max_wait: float = 0.25      # decide at the latest this long after the burst began
    rearm_speed: float = 4.0    # after a punch the hand must slow below this before it can punch again
    min_gap: float = 0.45       # shortest time between two punches from the same hand
    min_inward: float = 0.7     # hook: sideways travel toward the middle of the body
    min_up: float = 0.8         # uppercut: upward travel, with the hand low at some point in the burst
    guard_raise_up: float = 0.35  # the other hand rising at least this much means both are raising guard
    straight_ext: float = None  # jab/cross: arm extension needed; None = off (see module docstring)
    early: bool = True          # fire as soon as the motion is clearly a hook or uppercut, not at the peak
    early_inward: float = 1.2   # ...hook: sideways travel needed to be sure
    early_up: float = 1.2       # ...uppercut: upward travel needed to be sure
    early_ratio: float = 1.3    # ...and the winning direction must beat the other by this factor


class _Hand:
    def __init__(self, side):
        self.side = side
        self.armed, self.last_fire = True, -1e9
        self.reset()

    def reset(self):
        self.over, self.active = 0, False
        self.began = self.peak = 0.0
        self.ext = self.inward = self.up = 0.0
        self.low = False


class PunchDetector:
    """Feed update() every frame. It returns the punches decided this frame as (side, kind) pairs."""

    def __init__(self, settings=None):
        self.s = settings or Settings()
        self.hands = {"L": _Hand("L"), "R": _Hand("R")}
        self.latency_log = []  # (side, kind, seconds from the hand going fast to the decision)

    def update(self, now, speeds, travel, from_low):
        """speeds = [left, right]; travel = 2x3 (extension, right, up) per hand; from_low = [left, right]."""
        decided = []
        for i, side in enumerate("LR"):
            kind = self._step(self.hands[side], now, speeds[i], travel[i], from_low[i])
            if kind is not None:
                decided.append((side, kind))
        return self._drop_guard_raises(decided, travel, from_low)

    # --- one hand ---
    def _step(self, h, now, speed, travel, low):
        s = self.s
        ext, right, up = float(travel[0]), float(travel[1]), float(travel[2])
        inward = right if h.side == "L" else -right
        if not h.armed:
            h.armed = now - h.last_fire >= s.min_gap and speed < s.rearm_speed
        if not h.active:
            if not h.armed or speed <= s.start_speed:
                h.over = 0
                return None
            if h.over == 0:
                h.began, h.peak, h.low = now, 0.0, False
                h.ext = h.inward = h.up = 0.0
            h.over += 1
        # the hand is fast: keep measuring the burst
        h.peak = max(h.peak, speed)
        h.low = h.low or low
        h.ext, h.inward, h.up = max(h.ext, ext), max(h.inward, inward), max(h.up, up)
        if h.over >= s.start_frames:
            h.active = True
        if not h.active:
            return None
        kind = self._classify_early(h) if s.early else None
        if kind is None:
            if speed > s.drop_ratio * h.peak and now - h.began < s.max_wait:
                return None  # still speeding up
            kind = self._classify(h)
        if kind is not None:
            h.last_fire, h.armed = now, False  # only a real punch locks the hand out
            self.latency_log.append((h.side, kind, now - h.began))
        h.reset()
        return kind

    def _classify_early(self, h):
        """A hook or uppercut that is already unmistakable, so there is no need to wait for the peak."""
        s = self.s
        up = h.up if h.low else 0.0
        if up >= s.early_up and up >= s.early_ratio * h.inward:
            return "UPPERCUT"
        if h.inward >= s.early_inward and h.inward >= s.early_ratio * max(up, h.ext):
            return "HOOK"
        return None

    def _classify(self, h):
        s = self.s
        if h.low and h.up >= s.min_up and h.up >= h.inward:
            return "UPPERCUT"
        if h.inward >= s.min_inward and h.inward >= h.ext:
            return "HOOK"
        if s.straight_ext is not None and h.ext >= s.straight_ext:
            return "JAB" if h.side == "L" else "CROSS"
        return None

    # --- both hands ---
    def _drop_guard_raises(self, decided, travel, from_low):
        """An uppercut while the other hand is also rising from low is both hands raising guard."""
        out = []
        for side, kind in decided:
            if kind == "UPPERCUT":
                j = 1 if side == "L" else 0
                other = self.hands["R" if side == "L" else "L"]
                other_rising = travel[j][2] >= self.s.guard_raise_up and (from_low[j] or other.low)
                if other_rising:
                    # the other hand will be taken for an uppercut too, unless it already was; undo its lockout
                    other.last_fire = -1e9
                    continue
            out.append((side, kind))
        return out
