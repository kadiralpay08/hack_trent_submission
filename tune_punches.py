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
import os
import sys
import time

import cv2

import pose_tracker

HAND = {"L": "LEFT", "R": "RIGHT"}

# (kind, hand, what to do, repetitions)
BLOCKS = [
    ("JAB", "L", "LEFT hand JAB: punch straight at the camera", 6),
    ("CROSS", "R", "RIGHT hand CROSS: punch straight at the camera", 6),
    ("HOOK", "L", "LEFT hand HOOK: swing in a curve toward the middle", 6),
    ("HOOK", "R", "RIGHT hand HOOK: swing in a curve toward the middle", 6),
    ("UPPERCUT", "L", "LEFT hand UPPERCUT: scoop up from low", 6),
    ("UPPERCUT", "R", "RIGHT hand UPPERCUT: scoop up from low", 6),
    ("NONE", "-", "NO PUNCHES: wave, reach, shift about, stay loose", 6),
]
GO, REST = 1.3, 1.2  # seconds


def build_timeline(ready_seconds=8.0):
    """List of (start, end, phase, kind, hand, rep, title, subtitle)."""
    segs, t = [], 0.0

    def add(duration, phase, kind, hand, rep, title, subtitle=""):
        nonlocal t
        segs.append((t, t + duration, phase, kind, hand, rep, title, subtitle))
        t += duration

    add(ready_seconds, "ready", "", "", 0, "GET READY", "Hands up in guard. Head and both hands in view.")
    for kind, hand, what, reps in BLOCKS:
        add(4.0, "announce", kind, hand, 0, "NEXT", what)
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

    def __init__(self, timeline, max_seconds=None):
        self.timeline = timeline
        self.end = timeline[-1][1] if max_seconds is None else min(max_seconds, timeline[-1][1])
        self.t0 = None  # starts with the first video frame, after the camera and model have warmed up
        self.rows, self.fired, self.last_go = [], "", None

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
        seg = self.segment(self.now())
        self.rows.append([f"{self.now():.3f}", seg[2], seg[3], seg[4], seg[5], self.fired,
                          f"{speeds[0]:.3f}", f"{speeds[1]:.3f}",
                          *[f"{x:.3f}" for x in travel[0]], int(from_low[0]),
                          *[f"{x:.3f}" for x in travel[1]], int(from_low[1])])
        self.fired = ""

    def on_draw(self, frame):
        if self.t0 is None:
            self.t0 = time.time()
        t = self.now()
        seg = self.segment(t)
        _, end, phase, kind, hand, rep, title, subtitle = seg
        if phase == "go" and self.last_go != (kind, hand, rep):
            self.last_go = (kind, hand, rep)
            beep()
        colour = {"go": (0, 200, 0), "rest": (60, 60, 60), "announce": (160, 90, 0),
                  "ready": (160, 90, 0), "done": (60, 60, 60)}[phase]
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 110), colour, -1)
        cv2.putText(frame, title, (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        cv2.putText(frame, subtitle[:60], (15, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        if phase in ("ready", "announce"):
            cv2.putText(frame, f"{max(0.0, end - t):.0f}", (frame.shape[1] - 90, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)

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
    parser.add_argument("--max-seconds", type=float, help="stop early (used for testing the tool)")
    parser.add_argument("--ready-seconds", type=float, default=8.0)
    args = parser.parse_args()

    session = Session(build_timeline(args.ready_seconds), args.max_seconds)
    print(f"Session length about {session.end:.0f} s. Look at the camera window.")
    try:
        pose_tracker.main(on_punch=session.on_punch, on_features=session.on_features,
                          on_draw=session.on_draw, on_frame=session.on_frame)
    finally:
        path = session.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuning_data"))
        print(f"Saved {len(session.rows)} frames to {path}")


if __name__ == "__main__":
    main()
