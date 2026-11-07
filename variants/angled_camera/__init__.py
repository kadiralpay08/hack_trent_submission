"""Variant: one webcam, with the boxer turned about 45 degrees to it. No gloves, no second camera.

Why: a camera facing the boxer cannot see a straight punch, because the arm moves along the line of sight
(recorded: 0.12 shoulder-widths of arm extension, no faster than the camera's own jitter). Turned 45
degrees, a straight punch moves partly across the image, where the camera is good. This is also the
natural boxing stance, lead shoulder forward, so the webcam on the laptop can see it.

What is specific to this variant:
  - settings.json: punch-detector settings, with straight punches switched on.
  - a live body-angle readout on the camera window, so the boxer stands at the same angle every time
    (a tuning session recorded at one angle does not describe play at another).
  - its own tuning sessions, in tuning_data/angled_camera/.
"""
import math

import cv2

from variants.base import Variant

LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
TARGET_DEGREES, TOLERANCE_DEGREES = 45.0, 10.0


def body_angle(world):
    """How far the shoulder line is turned from facing the camera: 0 = square on, 90 = side on."""
    line = world[RIGHT_SHOULDER] - world[LEFT_SHOULDER]
    return math.degrees(math.atan2(abs(line[2]), abs(line[0])))


class AngleGuide:
    """Shows the boxer's angle to the camera and whether it is close enough to the target."""

    def __init__(self, target=TARGET_DEGREES, tolerance=TOLERANCE_DEGREES, smoothing=0.3, text_y=95):
        self.target, self.tolerance, self.smoothing, self.text_y = target, tolerance, smoothing, text_y
        self.angle = None
        self.history = []  # every raw reading, for describing a recorded session

    def on_pose(self, world, visibility):
        if min(visibility[LEFT_SHOULDER], visibility[RIGHT_SHOULDER]) < 0.5:
            return  # a shoulder is hidden, so the angle would be a guess
        raw = body_angle(world)
        self.history.append(raw)
        self.angle = raw if self.angle is None else self.smoothing * raw + (1 - self.smoothing) * self.angle

    def verdict(self):
        """(text, ok). The text says what to do to reach the target."""
        if self.angle is None:
            return "body angle: waiting for both shoulders", False
        off = self.angle - self.target
        if abs(off) <= self.tolerance:
            return f"body angle {self.angle:.0f} deg: good (aim {self.target:.0f})", True
        move = "turn more side-on" if off < 0 else "turn more toward the camera"
        return f"body angle {self.angle:.0f} deg: {move} (aim {self.target:.0f})", False

    def on_draw(self, frame):
        text, ok = self.verdict()
        cv2.putText(frame, text, (10, self.text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 200, 0) if ok else (0, 140, 255), 2)


class AngledCamera(Variant):
    name = "angled_camera"
    title = "Front camera at 45 degrees"
    summary = ("One webcam, boxer turned about 45 degrees to it, so jabs and crosses move across the image "
               "and can be seen. Rule-based; no hardware beyond the webcam.")
    needs = "A webcam about chest height, 1.5 to 2 m away, and room to stand in a boxing stance."

    guide = None

    def extra_callbacks(self, args, text_y=95):
        self.guide = AngleGuide(text_y=text_y)
        return {"on_pose": self.guide.on_pose, "on_draw": self.guide.on_draw}

    tuning_short = "Boxing stance, 45 deg to the camera (angle below)"

    def tuning_prompt(self):
        return ("Stand in your boxing stance, lead shoulder toward the camera, until the body angle on the "
                "camera window reads about 45 and says good. Keep the same stance for the whole session, and "
                "for play, because a session recorded at one angle does not describe another.")

    def session_note(self):
        angles = sorted(self.guide.history) if self.guide else []
        if len(angles) < 10:
            return "body angle: not measured (shoulders were not visible)"
        pick = lambda q: angles[int(q * (len(angles) - 1))]
        return (f"body angle during the session: median {pick(0.5):.0f} deg, middle 80% from {pick(0.1):.0f} to "
                f"{pick(0.9):.0f} deg (target {TARGET_DEGREES:.0f})")


VARIANT = AngledCamera()
