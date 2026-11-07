"""Record labelled training punches: hold the glove button, throw one punch, release.

  python punch_capture.py                    100 each of JAB, HOOK, UPPERCUT (resumes where you left off)
  python punch_capture.py --target 20        a smaller set, for a first test
  python punch_capture.py --simulate         fake glove and button (no hardware), to try the tool

One press = one training block. Hold the button for the whole punch, from guard, and release once your
hand is back. Stay in view of the camera, standing where you will play.
Each block is checked with the same punch-finding code training uses; one with no punch in it is set
aside in punch_data/_rejected/ and does not count.

Type in THIS TERMINAL, then Enter:
  j / h / u    switch to JAB / HOOK / UPPERCUT
  x            undo the last saved block (moves it to punch_data/_rejected/)
  q            quit (everything saved so far is kept)

The data stays on this computer in punch_data/ (not committed). Train with python punch_train.py.
"""
import argparse
import os
import shutil
import threading
import time

import punch_segment
import punch_session as ps

HOW = {
    "JAB": "straight forward",
    "HOOK": "left, then forward, then right",
    "UPPERCUT": "arm drops down, then drives up",
}
KEYS = {"j": "JAB", "h": "HOOK", "u": "UPPERCUT"}
PRE_S, POST_S = 1.0, 0.3       # data kept before the press and after the release
MIN_HOLD_S, MAX_HOLD_S = 0.15, 8.0
GREEN, RED, ORANGE, GREY, YELLOW = (0, 220, 0), (0, 0, 255), (0, 150, 255), (190, 190, 190), (0, 255, 255)


class Capture:
    def __init__(self, sensors, config, target, root, person):
        self.sensors, self.config, self.target, self.root, self.person = sensors, config, target, root, person
        self.counts = ps.count_blocks(root)
        self.label = self.next_label()
        self.press_t = None
        self.pending = None          # (press_t, release_t) waiting for the data after the release
        self.saved = []              # paths saved this session, for undo
        self.session = time.strftime("%Y%m%d_%H%M%S")
        self.result, self.result_colour, self.result_t = "", GREY, 0.0
        self.quit = False

    def next_label(self):
        for label in ps.LABELS:
            if self.counts.get(label, 0) < self.target:
                return label
        return None

    def say(self, text, colour=GREY):
        print(text)
        self.result, self.result_colour, self.result_t = text, colour, time.time()

    # --- typed commands ---
    def command(self, line):
        word = line.strip().lstrip("﻿").lower()
        if word in KEYS:
            self.label = KEYS[word]
            self.say(f"now recording {self.label}")
        elif word == "x":
            self.undo()
        elif word in ("q", "quit"):
            self.quit = True
        elif word:
            self.say("type j, h or u to switch punch, x to undo, q to quit")

    def read_terminal(self):
        while not self.quit:
            try:
                self.command(input())
            except EOFError:
                return

    def undo(self):
        if not self.saved:
            self.say("nothing to undo")
            return
        path, label = self.saved.pop()
        folder = os.path.join(self.root, "_rejected")
        os.makedirs(folder, exist_ok=True)
        shutil.move(path, os.path.join(folder, "undone_" + os.path.basename(path)))
        self.counts[label] -= 1
        self.say(f"undid the last {label} ({self.counts[label]}/{self.target})", ORANGE)

    # --- one block ---
    def finish_block(self, press_t, release_t):
        label = self.label
        if label is None:
            self.say("all punches are done; type j, h or u to record more", ORANGE)
            return
        held = release_t - press_t
        imu, pose = self.sensors.window(press_t - PRE_S, release_t + POST_S)
        settings = self.sensors.settings
        problem = None
        if label is None:
            problem = "all punches are done; type j, h or u to record more"
        elif held < MIN_HOLD_S:
            problem = f"button held only {1000 * held:.0f} ms"
        elif held > MAX_HOLD_S:
            problem = f"button held {held:.1f} s; hold it for just one punch"
        elif len(imu["t"]) < 0.6 * 500 * (held + PRE_S + POST_S):
            problem = "too little glove data arrived (WiFi dropping?)"
        segment, found = None, 0
        if problem is None:
            segments = punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], settings)
            segment, found = punch_segment.pick_block_segment(segments, press_t, release_t)
            if segment is None:
                problem = "no punch found while the button was held; punch harder, or lower start_g in punch_tune.py"
            elif segment.reason == "gap":
                problem = "the glove data had a gap during the punch"
        meta = {"label": label or "NONE", "press_t": press_t, "release_t": release_t, "session": self.session,
                "person": self.person, "segment_settings": settings.to_dict(), "problem": problem,
                "segments_found": found, "pose_frames": int(len(pose["t"]))}
        if problem:
            ps.save_block(label or "NONE", imu, pose, meta, self.root, rejected=True)
            self.say(f"NOT SAVED: {problem}", RED)
            return
        path = ps.save_block(label, imu, pose, meta, self.root)
        self.saved.append((path, label))
        self.counts[label] += 1
        note = "" if len(pose["t"]) else "  (camera did not see you: step into view)"
        if found > 1:
            note += f"  ({found} motions found; used the strongest)"
        self.say(f"saved {label} {self.counts[label]}/{self.target}: {1000 * segment.duration:.0f} ms, "
                 f"{segment.peak_g:.1f} g{note}", ORANGE if note else GREEN)
        if self.counts[label] >= self.target:
            self.label = self.next_label()
            self.say(f"{label} done! " + (f"next: {self.label}" if self.label else "ALL DONE. Train with python punch_train.py"),
                     GREEN)

    # --- camera loop callbacks ---
    def on_frame(self):
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "press":
                self.press_t = value
            elif kind == "release" and self.press_t is not None:
                self.pending, self.press_t = (self.press_t, value), None
        if self.pending:
            latest = self.sensors.latest_imu_t() or 0.0
            if latest >= self.pending[1] + POST_S:
                self.finish_block(*self.pending)
                self.pending = None
        return self.quit

    def on_draw(self, frame):
        h, w = frame.shape[:2]
        top = 150
        if self.label:
            done = self.counts.get(self.label, 0)
            ps.put(frame, f"{self.label}  {done}/{self.target}", top, YELLOW, 1.1, thick=3)
            ps.put(frame, f"hold button, punch {HOW[self.label]}, release", top + 32, (255, 255, 255), 0.6)
        else:
            ps.put(frame, "ALL DONE - train with python punch_train.py", top, GREEN, 0.8)
        totals = "  ".join(f"{k} {self.counts.get(k, 0)}" for k in ps.LABELS)
        ps.put(frame, totals, top + 58, GREY, 0.55)
        if not self.sensors.glove_ok():
            ps.put(frame, "NO GLOVE DATA", top + 88, RED, 0.7)
        elif self.press_t is not None:
            ps.put(frame, "BUTTON HELD - recording", top + 88, RED, 0.8, thick=3)
        if time.time() - self.result_t < 4:
            ps.put(frame, self.result[:70], h - 45, self.result_colour, 0.55)
        ps.put(frame, "terminal: j/h/u switch punch, x undo, q quit", h - 15, GREY, 0.45)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", type=int, default=100, help="blocks wanted of each punch")
    parser.add_argument("--person", default="", help="who is punching (saved with each block)")
    parser.add_argument("--data", default=ps.DATA_DIR, help="where to save blocks")
    parser.add_argument("--simulate", action="store_true", help="fake glove and button (no hardware)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    args = parser.parse_args()

    config = ps.load_config()
    sensors = ps.LiveSensors(config["segment"], simulate=args.simulate)
    cap = Capture(sensors, config, args.target, args.data, args.person)
    print("Recording to", os.path.abspath(args.data), "| already have:", cap.counts)
    print("j/h/u switch punch, x undo, q quit (type in this terminal, then Enter)")
    threading.Thread(target=cap.read_terminal, daemon=True).start()
    started = time.time()

    def on_frame():
        return cap.on_frame() or bool(args.max_seconds and time.time() - started > args.max_seconds)

    import pose_tracker
    try:
        pose_tracker.main(on_frame=on_frame, on_draw=cap.on_draw, camera_index=args.camera,
                          **sensors.pose_callbacks())
    finally:
        sensors.close()
    print("Saved this session:", len(cap.saved), "| totals:", cap.counts)
    return cap


if __name__ == "__main__":
    main()
