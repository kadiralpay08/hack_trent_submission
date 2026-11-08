"""Record a guided punching session so the punch settings can be tuned to how you really move.

  python tune_punches.py

Stand back so your head and both hands are in view, hands up in guard. The camera window shows what
to do. A beep marks the start of each punch. Throw each punch once per beep, then return to guard.
It takes about 2.5 minutes. Nothing is pressed or sent to any game. The measurements are saved to
tuning_data/session_<time>.csv, one row per camera frame.
"""
import argparse
import csv
import ctypes
import math
import os
import sys
import time

import cv2

import overlay
import pose_tracker

HAND = {"L": "LEFT", "R": "RIGHT"}
WHITE = (255, 255, 255)
STRAIGHT_AT_CAMERA = "punch straight at the camera"

# (kind, hand, what to do, repetitions). {straight} is filled in by the variant: facing the camera you
# punch at it, but turned 45 degrees you punch straight ahead along your stance.
BLOCKS = [
    ("JAB", "L", "LEFT hand JAB: {straight}", 6),
    ("CROSS", "R", "RIGHT hand CROSS: {straight}", 6),
    ("HOOK", "L", "LEFT hand HOOK: swing in a curve toward the middle", 6),
    ("HOOK", "R", "RIGHT hand HOOK: swing in a curve toward the middle", 6),
    ("UPPERCUT", "L", "LEFT hand UPPERCUT: scoop up from low", 6),
    ("UPPERCUT", "R", "RIGHT hand UPPERCUT: scoop up from low", 6),
    ("NONE", "-", "NO PUNCHES: wave, reach, shift about, stay loose", 6),
]
GO, REST = 1.3, 1.2  # seconds


def build_timeline(ready_seconds=8.0, ready_text="Hands up in guard. Head and both hands in view.",
                   straight=STRAIGHT_AT_CAMERA):
    """List of (start, end, phase, kind, hand, rep, title, subtitle)."""
    segs, t = [], 0.0

    def add(duration, phase, kind, hand, rep, title, subtitle=""):
        nonlocal t
        segs.append((t, t + duration, phase, kind, hand, rep, title, subtitle))
        t += duration

    add(ready_seconds, "ready", "", "", 0, "GET READY", ready_text)
    for kind, hand, what, reps in BLOCKS:
        add(4.0, "announce", kind, hand, 0, "NEXT", what.format(straight=straight))
        for rep in range(1, reps + 1):
            add(REST, "rest", kind, hand, rep, "guard", f"{kind} {rep} of {reps} coming")
            title = "MOVE FREELY" if kind == "NONE" else f"{HAND[hand]} {kind}!"
            add(GO, "go", kind, hand, rep, title, f"{rep} of {reps}")
    add(2.0, "done", "", "", 0, "DONE", "Saving...")
    return segs


def beep():
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBeep(0)


class Session:
    COLUMNS = ["t", "phase", "kind", "hand", "rep", "fired", "spd_L", "spd_R",
               "ext_L", "right_L", "up_L", "low_L", "ext_R", "right_R", "up_R", "low_R"]
    HOLD = 2.0       # seconds the boxer must stay in position before the recording starts
    GIVE_UP = 60.0   # seconds to wait for that before starting anyway

    def __init__(self, timeline, max_seconds=None, position=None, on_start=None):
        """position: None to start at once, or a function that says whether the boxer is in place.
        The recording then starts by itself once they have stayed in place for HOLD seconds.
        on_start: called once when the recording starts."""
        self.timeline = timeline
        self.end = timeline[-1][1] if max_seconds is None else min(max_seconds, timeline[-1][1])
        self.t0 = None  # starts with the first video frame, after the camera and model have warmed up
        self.rows, self.fired, self.last_go = [], "", None
        self.position, self.on_start = position, on_start
        self.waiting = position is not None
        self.waiting_since = self.held_since = None
        self.was_in_position, self.last_cue = False, 0.0
        self.started_anyway = False

    def begin(self):
        self.t0, self.waiting = time.time(), False
        if self.on_start:
            self.on_start()

    def wait_for_position(self):
        """Called every frame while waiting. Starts the recording when the boxer has held position."""
        now = time.time()
        if self.waiting_since is None:
            self.waiting_since = now
        in_position = bool(self.position())
        if in_position and not self.was_in_position and now - self.last_cue > 1.5:
            beep()  # a sound, because the boxer is across the room and may not be looking at the screen
            self.last_cue = now
        self.was_in_position = in_position
        if not in_position:
            self.held_since = None
        elif self.held_since is None:
            self.held_since = now
        if self.held_since is not None and now - self.held_since >= self.HOLD:
            self.begin()
        elif now - self.waiting_since >= self.GIVE_UP:
            self.started_anyway = True
            self.begin()

    def now(self):
        return 0.0 if self.t0 is None else time.time() - self.t0

    def segment(self, t):
        for seg in self.timeline:
            if seg[0] <= t < seg[1]:
                return seg
        return self.timeline[-1]

    # --- callbacks for pose_tracker.main ---
    def on_punch(self, kind, side, speed):
        self.fired = f"{side}:{kind}"

    def on_features(self, now, speeds, travel, from_low):
        if self.waiting:
            self.fired = ""  # nothing is recorded until the recording starts
            return
        seg = self.segment(self.now())
        self.rows.append([f"{self.now():.3f}", seg[2], seg[3], seg[4], seg[5], self.fired,
                          f"{speeds[0]:.3f}", f"{speeds[1]:.3f}",
                          *[f"{x:.3f}" for x in travel[0]], int(from_low[0]),
                          *[f"{x:.3f}" for x in travel[1]], int(from_low[1])])
        self.fired = ""

    def on_draw(self, frame):
        if self.waiting:
            self.wait_for_position()
        elif self.t0 is None:
            self.begin()
        t = self.now()
        seg = self.segment(t)
        _, end, phase, kind, hand, rep, title, subtitle = seg
        countdown = f"{max(0.0, end - t):.0f}" if phase in ("ready", "announce") else ""
        if self.waiting:  # the ready countdown has not started: show the hold-still countdown instead
            title = "GET IN POSITION"
            countdown = "" if self.held_since is None else f"{math.ceil(self.HOLD - (time.time() - self.held_since))}"
            left = self.GIVE_UP - (time.time() - self.waiting_since)
            if left < 15:
                subtitle = f"starting anyway in {max(0, math.ceil(left))} s"
        if phase == "go" and self.last_go != (kind, hand, rep):
            self.last_go = (kind, hand, rep)
            beep()
        colour = {"go": (0, 200, 0), "rest": (60, 60, 60), "announce": (160, 90, 0),
                  "ready": (160, 90, 0), "done": (60, 60, 60)}[phase]
        # Everything is sized for reading from 2 m, and scales with the picture (k = 1 at 480 pixels high).
        h, w = frame.shape[:2]
        k = h / 480
        cv2.rectangle(frame, (0, 0), (w, int(150 * k)), colour, -1)
        room = w - 30 * k - (150 * k if countdown else 0)
        overlay.fit_text(frame, title, 15 * k, 82 * k, 2.4 * k, WHITE, 5 * k, max_width=room)
        overlay.fit_text(frame, subtitle, 15 * k, 132 * k, 1.1 * k, WHITE, 2 * k, max_width=w - 30 * k)
        if countdown:
            overlay.fit_text(frame, countdown, w - 20 * k, 112 * k, 3.0 * k, WHITE, 7 * k, align="right")

    def on_frame(self):
        return self.now() >= self.end

    def save(self, folder):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("session_%Y%m%d_%H%M%S.csv"))
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.COLUMNS)
            writer.writerows(self.rows)
        return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--variant", help="record for this product variant (see python play.py); its guidance "
                                          "is shown and the session is saved under tuning_data/<variant>/")
    parser.add_argument("--camera", type=int, default=0, help="camera number (0 is the default webcam)")
    parser.add_argument("--max-seconds", type=float, help="stop early (used for testing the tool)")
    parser.add_argument("--ready-seconds", type=float,
                        help="time to get ready once in position (default 6; 8 with no --variant; 15 for a variant that does not wait for a position)")
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    variant, extras, folder, ready_text = None, {}, os.path.join(root, "tuning_data"), "Hands up in guard. Head and both hands in view."
    straight, position, on_start = STRAIGHT_AT_CAMERA, None, None
    if args.variant:
        import variants
        try:
            variant = variants.get(args.variant)
        except ValueError as e:
            raise SystemExit(str(e))
        extras = variant.extra_callbacks(args, text_y=135, large=True)  # large: read from across the room
        folder, ready_text, straight = variant.data_dir, variant.tuning_short, variant.tuning_straight
        if variant.tuning_position() is not None:
            position, on_start = variant.tuning_position, variant.tuning_started
        print(variant.tuning_prompt())
    ready = args.ready_seconds if args.ready_seconds else (6.0 if position else 15.0 if variant else 8.0)

    session = Session(build_timeline(ready, ready_text, straight), args.max_seconds, position, on_start)
    extra_draw = extras.get("on_draw")

    def draw(frame):
        session.on_draw(frame)
        if extra_draw:
            extra_draw(frame)

    if position:
        print(f"Waiting for you to stand in position. The recording starts by itself, or after {Session.GIVE_UP:.0f} s anyway.")
    print(f"Session length about {session.end:.0f} s. Look at the camera window.")
    try:
        pose_tracker.main(on_punch=session.on_punch, on_features=session.on_features, on_draw=draw,
                          on_frame=session.on_frame, camera_index=args.camera, on_pose=extras.get("on_pose"),
                          big_window=True, show_3d=False)  # the 3D view is no use while recording
    finally:
        if not session.rows:
            print("The recording never started, so nothing was saved.")
        else:
            path = session.save(folder)
            print(f"Saved {len(session.rows)} frames to {path}")
            note = variant.session_note() if variant else ""
            if session.started_anyway:
                note = (note + "; " if note else "") + "started without ever holding the position"
            if note:
                print(note)
                if variant:
                    with open(path[:-4] + ".txt", "w", encoding="utf-8") as f:
                        f.write(f"variant: {variant.name}\n{note}\n")
    if not session.rows:
        sys.exit(1)  # so a script that scores the newest session does not score an old one by mistake


if __name__ == "__main__":
    main()
