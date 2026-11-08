"""Variant: one webcam, with the boxer turned about 45 degrees to it. No gloves, no second camera.

Why: a camera facing the boxer cannot see a straight punch, because the arm moves along the line of sight
(recorded: 0.12 shoulder-widths of arm extension, no faster than the camera's own jitter). Turned 45
degrees, a straight punch moves partly across the image, where the camera is good. This is also the
natural boxing stance, lead shoulder forward, so the webcam on the laptop can see it.

What is specific to this variant:
  - settings.json: punch-detector settings, with straight punches switched on.
  - a live body-angle guide on the camera window, so the boxer stands at the same angle every time
    (a tuning session recorded at one angle does not describe play at another). When recording a
    tuning session it is large enough to read from 2 m, and the recording waits until the boxer is in place.
  - its own tuning sessions, in tuning_data/angled_camera/.
"""
import math
import time

import cv2

import overlay
from variants.base import Variant

LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
TARGET_DEGREES, TOLERANCE_DEGREES = 45.0, 10.0
STALE_SECONDS = 0.5  # a reading older than this is not trusted: nobody is in view

GOOD, OFF, LOST = (0, 200, 0), (0, 140, 255), (0, 0, 230)  # BGR
WHITE = (255, 255, 255)


def body_angle(world):
    """How far the shoulder line is turned from facing the camera: 0 = square on, 90 = side on."""
    line = world[RIGHT_SHOULDER] - world[LEFT_SHOULDER]
    return math.degrees(math.atan2(abs(line[2]), abs(line[0])))


class AngleGuide:
    """Shows the boxer's angle to the camera and whether it is close enough to the target.

    large=False: one line of small text (for play). large=True: a coloured border, a gauge and a short
    message, readable from across the room (for recording a tuning session).
    """

    def __init__(self, target=TARGET_DEGREES, tolerance=TOLERANCE_DEGREES, smoothing=0.3, text_y=95, large=False):
        self.target, self.tolerance, self.smoothing, self.text_y = target, tolerance, smoothing, text_y
        self.large = large
        self.angle = None
        self.updated = -1e9  # when the last reading arrived
        self.history = []    # every raw reading, for describing a recorded session
        self.samples = self.samples_in_range = 0  # smoothed readings, and how many were within tolerance

    def start_recording(self):
        """Forget the readings so far, so the session note describes only the recording itself."""
        self.history, self.samples, self.samples_in_range = [], 0, 0

    def on_pose(self, world, visibility):
        if min(visibility[LEFT_SHOULDER], visibility[RIGHT_SHOULDER]) < 0.5:
            return  # a shoulder is hidden, so the angle would be a guess
        now = time.time()
        if now - self.updated > STALE_SECONDS:
            self.angle = None  # they left and came back: start from the new reading, not the old one
        raw = body_angle(world)
        self.history.append(raw)
        self.angle = raw if self.angle is None else self.smoothing * raw + (1 - self.smoothing) * self.angle
        self.updated = now
        self.samples += 1
        self.samples_in_range += abs(self.angle - self.target) <= self.tolerance

    def current(self):
        """The smoothed angle, or None if nobody has been in view for a moment."""
        return self.angle if time.time() - self.updated <= STALE_SECONDS else None

    def in_range(self):
        angle = self.current()
        return angle is not None and abs(angle - self.target) <= self.tolerance

    def verdict(self):
        """(text, ok). The text says what to do to reach the target."""
        angle = self.current()
        if angle is None:
            return "body angle: waiting for both shoulders", False
        off = angle - self.target
        if abs(off) <= self.tolerance:
            return f"body angle {angle:.0f} deg: good (aim {self.target:.0f})", True
        move = "turn more side-on" if off < 0 else "turn more toward the camera"
        return f"body angle {angle:.0f} deg: {move} (aim {self.target:.0f})", False

    def coach(self):
        """(short message, colour) for the large display."""
        angle = self.current()
        if angle is None:
            return "STEP INTO VIEW", LOST
        off = angle - self.target
        if abs(off) <= self.tolerance:
            return "GOOD - HOLD IT", GOOD
        return ("MORE SIDEWAYS" if off < 0 else "FACE CAMERA MORE"), OFF

    def on_draw(self, frame):
        if self.large:
            self.draw_large(frame)
            return
        text, ok = self.verdict()
        cv2.putText(frame, text, (10, self.text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 200, 0) if ok else (0, 140, 255), 2)

    def draw_large(self, frame):
        """Border in the status colour, plus a bottom panel: what to do, and a gauge showing where you are."""
        h, w = frame.shape[:2]
        k = h / 480  # all sizes below are for a 480-pixel-high picture
        message, colour = self.coach()
        angle = self.current()
        overlay.border(frame, colour, 10 * k)

        overlay.shade(frame, h - 150 * k, h - 6 * k)
        overlay.fit_text(frame, message, w / 2, h - 100 * k, 2.0 * k, colour, 4 * k, max_width=w - 60 * k, align="centre")

        x0, x1 = 30 * k, w - 30 * k
        top, bottom = h - 80 * k, h - 52 * k
        at = lambda degrees: int(x0 + (x1 - x0) * min(max(degrees, 0), 90) / 90)
        lo, hi = self.target - self.tolerance, self.target + self.tolerance
        cv2.rectangle(frame, (at(0), int(top)), (at(lo), int(bottom)), (0, 90, 160), -1)
        cv2.rectangle(frame, (at(lo), int(top)), (at(hi), int(bottom)), (0, 170, 0), -1)
        cv2.rectangle(frame, (at(hi), int(top)), (at(90), int(bottom)), (0, 90, 160), -1)
        overlay.fit_text(frame, "FACING CAMERA", x0, h - 22 * k, 0.8 * k, WHITE, 2 * k)
        overlay.fit_text(frame, f"{self.target:.0f}", at(self.target), h - 22 * k, 0.8 * k, WHITE, 2 * k, align="centre")
        overlay.fit_text(frame, "SIDE-ON", x1, h - 22 * k, 0.8 * k, WHITE, 2 * k, align="right")

        if angle is not None:  # a white dot with the number in it, where the boxer is now
            centre = (at(angle), int((top + bottom) / 2))
            cv2.circle(frame, centre, int(22 * k), WHITE, -1)
            cv2.circle(frame, centre, int(22 * k), (0, 0, 0), max(1, int(2 * k)))
            overlay.fit_text(frame, f"{angle:.0f}", centre[0], centre[1] + 8 * k, 0.75 * k, (0, 0, 0), 2 * k, align="centre")


class AngledCamera(Variant):
    name = "angled_camera"
    title = "Front camera at 45 degrees"
    summary = ("One webcam, boxer turned about 45 degrees to it, so jabs and crosses move across the image "
               "and can be seen. Rule-based; no hardware beyond the webcam.")
    needs = ("A webcam 1.5 to 2 m away, aimed at your upper body (a laptop on a desk, screen tilted back, works), "
             "and room to stand in a boxing stance.")

    guide = None

    def extra_callbacks(self, args, text_y=95, large=False):
        self.guide = AngleGuide(text_y=text_y, large=large)
        return {"on_pose": self.guide.on_pose, "on_draw": self.guide.on_draw}

    tuning_short = "Left shoulder to the camera, hands up in guard"
    tuning_straight = "punch straight out in front of you"  # not at the camera: you are turned 45 degrees to it

    def tuning_prompt(self):
        return ("Stand in your boxing stance, lead (left) shoulder toward the camera. The recording starts by "
                "itself once the body angle has held at about 45 for 2 seconds. Keep the same stance for the "
                "whole session, and for play, because a session recorded at one angle does not describe another.")

    def play_prompt(self):
        return ("Stand in your boxing stance, lead (left) shoulder toward the camera, in the same spot and at the "
                "same angle (about 45) as when you recorded. Press F7 (or C in the camera window), then get in guard: "
                "it counts down 5 seconds and beeps when calibrated. The line on the camera window shows your body angle. "
                "Jab, cross and uppercut are the reliable punches; hooks are often read as crosses or uppercuts.")

    def tuning_position(self):
        return self.guide.in_range() if self.guide else None

    def tuning_started(self):
        if self.guide:
            self.guide.start_recording()

    def session_note(self):
        angles = sorted(self.guide.history) if self.guide else []
        if len(angles) < 10:
            return "body angle: not measured (shoulders were not visible)"
        pick = lambda q: angles[int(q * (len(angles) - 1))]
        share = self.guide.samples_in_range / max(1, self.guide.samples)
        note = (f"body angle during the session: median {pick(0.5):.0f} deg, middle 80% from {pick(0.1):.0f} to "
                f"{pick(0.9):.0f} deg (target {TARGET_DEGREES:.0f}), within {TOLERANCE_DEGREES:.0f} deg of the "
                f"target {share:.0%} of the time")
        if share < 0.7:
            note += ". TOO MUCH OF THIS SESSION WAS OFF THE ANGLE: record it again"
        return note


VARIANT = AngledCamera()
