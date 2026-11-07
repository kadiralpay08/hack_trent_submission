"""Detect punches from glove motion-sensor data, and use them to confirm the camera's punches.

A punch is a short, hard burst of acceleration. We measure how far the total acceleration is from
1 g (gravity), so it does not matter how the glove is tilted. The burst starts when that crosses
START_G and ends when it falls back under END_G. The sensor reports about 200 times a second, far more
often than the camera sees frames, so the glove gives a precise time and a real impact strength.

The glove cannot tell a jab from a hook, so it never chooses the key. The camera does that. The glove
only answers "did this hand really punch just now?", which filters out the camera mistaking a wave
or a reach for a punch.

These numbers are starting points taken from the simulator. Tune them against the real glove.
"""
import time
from dataclasses import dataclass

import numpy as np

START_G = 3.0         # acceleration beyond gravity that starts a punch
END_G = 1.5           # the punch is over when it falls back below this
COOLDOWN = 0.25       # seconds before the same glove can start another punch
MAX_DURATION = 0.4    # a burst longer than this is reported anyway (shaking, not one punch)


@dataclass
class Punch:
    t: float          # when it started, on the laptop clock
    peak_g: float     # hardest acceleration beyond gravity, in g
    duration: float   # seconds


class PunchDetector:
    """Feed it one glove's samples in order. update() returns a Punch when one has just finished."""

    def __init__(self, start_g=START_G, end_g=END_G, cooldown=COOLDOWN, max_duration=MAX_DURATION):
        self.start_g, self.end_g = start_g, end_g
        self.cooldown, self.max_duration = cooldown, max_duration
        self.active, self.start_t, self.peak, self.last_end = False, 0.0, 0.0, -1e9

    def update(self, sample):
        beyond_gravity = abs(float(np.linalg.norm(sample.accel)) - 1.0)
        if not self.active:
            if beyond_gravity >= self.start_g and sample.t - self.last_end >= self.cooldown:
                self.active, self.start_t, self.peak = True, sample.t, beyond_gravity
            return None
        self.peak = max(self.peak, beyond_gravity)
        if beyond_gravity < self.end_g or sample.t - self.start_t >= self.max_duration:
            self.active, self.last_end = False, sample.t
            return Punch(self.start_t, self.peak, sample.t - self.start_t)
        return None


class GlovePunches:
    """Watches every glove a GloveReceiver hears and remembers each one's latest punch."""

    def __init__(self, receiver, **detector_settings):
        self.receiver, self.settings = receiver, detector_settings
        self.detectors, self.seen_t, self.last = {}, {}, {}

    def poll(self):
        """Run any new samples through the detectors. Returns a list of (glove, Punch)."""
        found = []
        for glove, buffer in list(self.receiver.samples.items()):
            detector = self.detectors.setdefault(glove, PunchDetector(**self.settings))
            seen = self.seen_t.get(glove, -1.0)
            samples = list(buffer)
            if samples and samples[-1].t < seen - 5:  # the glove rebooted and its clock restarted
                seen = -1.0
            for sample in samples:
                if sample.t > seen:
                    seen = sample.t
                    punch = detector.update(sample)
                    if punch:
                        self.last[glove] = punch
                        found.append((glove, punch))
            self.seen_t[glove] = seen
        return found

    def confirms(self, glove, within=0.6, now=None):
        """True if this hand punched in the last `within` seconds.

        A hand with no glove (nothing heard from it) is never blocked, so a single glove still works.
        """
        if glove not in self.receiver.samples:
            return True
        detector = self.detectors.get(glove)
        if detector is not None and detector.active:
            return True  # a punch is happening right now and has not finished yet
        punch = self.last.get(glove)
        now = time.time() if now is None else now
        return punch is not None and -0.1 <= now - punch.t <= within
