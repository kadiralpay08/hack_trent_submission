"""Record labelled training punches.

  python punch_capture.py --person andre               prompted (default): the screen calls the punch
  python punch_capture.py --target 20 --person andre   a smaller set, for a first test
  python punch_capture.py --button                     hold the glove button around each punch instead
  python punch_capture.py --simulate                   fake glove (no hardware), to try the tool

PROMPTED MODE (default). Nothing starts until you have calibrated (press C, get into guard during the
5 s countdown) and pressed "." (works from any window). "." also pauses and resumes at any time.
The screen shows "JAB!", "HOOK!" or "UPPERCUT!" with that punch's arcade sound from the game, in random order.
Throw that punch once, then return to guard. The strongest punch in the next 1.6 s gets that label.
Anything the punch finder catches while you are NOT being prompted is saved as NONE, a "not a punch"
example. Every 15 prompts there is a "MOVE, DON'T PUNCH" round: shuffle, adjust your guard, feint,
bob. These NONE examples teach the model to press nothing for movement that is not a punch.
Your hands stay as they will be in play, so the training punches match the game.

BUTTON MODE (--button). Hold the glove button, throw one punch, release. Collects no NONE examples.

Keys that work in any window, no Enter needed:
  .       prompted mode: start, then pause or resume at any time
  F11     full screen on or off (it starts full screen; --windowed starts in a window)
Type in THIS TERMINAL, then Enter:
  p       pause or resume (prompted mode), the same as "."
  x       undo the last saved punch (moves it to punch_data/_rejected/)
  j/h/u   button mode only: switch to JAB / HOOK / UPPERCUT
  q       quit (everything saved so far is kept); q in the camera window also works

The data stays on this computer in punch_data/<LABEL>/ (not committed). Train with python punch_train.py.
"""
import argparse
import os
import random
import shutil
import sys
import threading
import time

import punch_segment
import punch_session as ps

HOW = {
    "JAB": "straight forward",
    "HOOK": "left, then forward, then right",
    "UPPERCUT": "arm drops down, then drives up",
}
NONE = "NONE"
KEYS = {"j": "JAB", "h": "HOOK", "u": "UPPERCUT"}
PRE_S, POST_S = 1.0, 0.3       # data kept before and after each block
MIN_HOLD_S, MAX_HOLD_S = 0.15, 8.0
REST_S, GO_S, LATE_S = 1.6, 1.6, 0.6   # prompted: guard, punch window, grace after the window
FREE_EVERY, FREE_S, ANNOUNCE_S = 15, 15.0, 4.0
GREEN, RED, ORANGE, GREY, YELLOW = (0, 220, 0), (0, 0, 255), (0, 150, 255), (190, 190, 190), (0, 255, 255)
TONES = {"JAB": 880, "HOOK": 660, "UPPERCUT": 520, "FREE": 300}


def beep(kind):
    """Call a punch (or the MOVE, DON'T PUNCH round) with the game's arcade sound for it: see punch_sounds.py."""
    if sys.platform != "win32":
        return
    threading.Thread(target=_play, args=(kind,), daemon=True).start()


def _play(kind):
    import winsound
    try:
        import punch_sounds  # imported here, so if it ever fails the tool still starts and still beeps
        punch_sounds.play(kind)
    except Exception:  # a sound problem must never stop a recording session: fall back to the plain tone
        winsound.Beep(TONES.get(kind, 700), 120)


class Capture:
    """Shared by both modes: counting, saving and checking blocks, undo, and the terminal."""

    def __init__(self, sensors, config, target, root, person):
        self.sensors, self.config, self.target, self.root, self.person = sensors, config, target, root, person
        self.counts = ps.count_blocks(root)
        self.counts.setdefault(NONE, 0)
        self.saved = []              # (path, label) saved this session, for undo
        self.session = time.strftime("%Y%m%d_%H%M%S")
        self.result, self.result_colour, self.result_t = "", GREY, 0.0
        self.quit = False
        self.calibrated = False  # set by pose_tracker after C and its 5 s countdown

    def on_calibrated(self):
        self.calibrated = True

    def remaining(self):
        return {k: max(0, self.target - self.counts.get(k, 0)) for k in ps.LABELS}

    def say(self, text, colour=GREY):
        print(text)
        self.result, self.result_colour, self.result_t = text, colour, time.time()

    def read_terminal(self):
        while not self.quit:
            try:
                self.command(input().strip().lstrip("﻿").lower())  # ﻿: a byte-order mark
            except EOFError:
                return

    def command(self, word):
        if word == "x":
            self.undo()
        elif word in ("q", "quit"):
            self.quit = True
        elif word:
            self.say(self.HELP)

    def undo(self):
        if not self.saved:
            self.say("nothing to undo")
            return
        path, label = self.saved.pop()
        folder = os.path.join(self.root, "_rejected")
        os.makedirs(folder, exist_ok=True)
        shutil.move(path, os.path.join(folder, "undone_" + os.path.basename(path)))
        self.counts[label] -= 1
        self.say(f"undid the last {label} ({self.counts[label]})", ORANGE)

    def save_block(self, label, t0, t1, quiet=False):
        """Check and save the data around [t0, t1] as one block. Returns the punch Segment or None."""
        imu, pose = self.sensors.window(t0 - PRE_S, t1 + POST_S)
        settings = self.sensors.settings
        problem, segment, found = None, None, 0
        if len(imu["t"]) < 0.6 * 500 * (t1 - t0 + PRE_S + POST_S):
            problem = "too little glove data arrived (WiFi dropping?)"
        else:
            segments = punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], settings)
            segment, found = punch_segment.pick_block_segment(segments, t0, t1)
            if segment is None:
                problem = "no punch found"
            elif segment.reason == "gap":
                problem = "the glove data had a gap during the punch"
        meta = {"label": label, "press_t": t0, "release_t": t1, "session": self.session, "person": self.person,
                "mode": self.MODE, "segment_settings": settings.to_dict(), "problem": problem,
                "segments_found": found, "pose_frames": int(len(pose["t"]))}
        if problem:
            ps.save_block(label, imu, pose, meta, self.root, rejected=True)
            if not quiet:
                self.say(f"NOT SAVED: {problem}", RED)
            return None
        path = ps.save_block(label, imu, pose, meta, self.root)
        self.saved.append((path, label))
        self.counts[label] = self.counts.get(label, 0) + 1
        return segment


class ButtonCapture(Capture):
    MODE = "button"
    HELP = "type j, h or u to switch punch, x to undo, q to quit"

    def __init__(self, *args):
        super().__init__(*args)
        self.label = self.next_label()
        self.press_t, self.pending = None, None

    def next_label(self):
        return next((k for k, n in self.remaining().items() if n > 0), None)

    def command(self, word):
        if word in KEYS:
            self.label = KEYS[word]
            self.say(f"now recording {self.label}")
        else:
            super().command(word)

    def on_frame(self, keys=None):
        if keys is not None:
            keys.poll_fullscreen()
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "press":
                self.press_t = value
            elif kind == "release" and self.press_t is not None:
                self.pending, self.press_t = (self.press_t, value), None
        if self.pending and (self.sensors.latest_imu_t() or 0.0) >= self.pending[1] + POST_S:
            press_t, release_t = self.pending
            self.pending = None
            self.finish(press_t, release_t)
        return self.quit

    def finish(self, press_t, release_t):
        label, held = self.label, release_t - press_t
        if label is None:
            self.say("all punches are done; type j, h or u to record more", ORANGE)
        elif held < MIN_HOLD_S:
            self.say(f"NOT SAVED: button held only {1000 * held:.0f} ms", RED)
        elif held > MAX_HOLD_S:
            self.say(f"NOT SAVED: button held {held:.1f} s; hold it for just one punch", RED)
        else:
            seg = self.save_block(label, press_t, release_t)
            if seg is not None:
                self.say(f"saved {label} {self.counts[label]}/{self.target}: {1000 * seg.duration:.0f} ms, {seg.peak_g:.1f} g", GREEN)
                if self.counts[label] >= self.target:
                    self.label = self.next_label()
                    self.say(f"{label} done! " + (f"next: {self.label}" if self.label else "ALL DONE"), GREEN)

    def on_draw(self, frame):
        h = frame.shape[0]
        top = 150
        if self.label:
            ps.put(frame, f"{self.label}  {self.counts.get(self.label, 0)}/{self.target}", top, YELLOW, 1.1, thick=3)
            ps.put(frame, f"hold button, punch {HOW[self.label]}, release", top + 32, (255, 255, 255), 0.6)
        else:
            ps.put(frame, "ALL DONE - train with python punch_train.py", top, GREEN, 0.8)
        draw_common(self, frame, top + 58, self.press_t is not None)
        ps.put(frame, "terminal: j/h/u switch punch, x undo, q quit  |  F11 full screen", h - ps.TEXT_BOTTOM, GREY, 0.45)


class PromptedCapture(Capture):
    MODE = "prompted"
    HELP = "calibrate with C, then press . to start (. pauses and resumes); terminal: x undo, q quit"

    def __init__(self, *args):
        super().__init__(*args)
        # "waiting" until calibrated and "." is pressed; then announce -> rest -> go ... -> done
        self.phase, self.phase_end, self.prompt = "waiting", None, None
        self.prompts_done, self.paused = 0, False
        self.windows = []        # (label, start, end) of every punch prompt, to tell punches from NONE
        self.pending = []        # (label, start, end) prompts waiting for their data
        self.free = []           # (start, end) of MOVE, DON'T PUNCH rounds
        self.none_pending = []   # Segments to save as NONE once their data has arrived

    def command(self, word):
        word = word.strip(".")  # "." works in every window, so it may also be typed into the terminal
        if word == "p":
            self.toggle()
        else:
            super().command(word)

    def not_ready_reason(self):
        if not self.sensors.glove_ok():
            return "no glove data"
        if not self.sensors.segmenter.ready:
            return "hold the glove still for a moment"
        if not self.calibrated:
            return "press C to calibrate (get into guard during the countdown)"
        return None

    def toggle(self):
        """The "." key: start, pause or resume."""
        now = time.time()
        if self.phase == "done":
            return
        if self.phase == "waiting" or self.paused:
            reason = self.not_ready_reason()
            if reason:
                self.say(f"cannot start yet: {reason}", ORANGE)
                return
            self.paused = False
            self.phase, self.phase_end = "announce", now + ANNOUNCE_S
            self.say("starting" if not self.prompts_done else "carrying on", GREEN)
            return
        # pause: a prompt that is showing right now is cancelled, so it does not count as missed
        if self.phase == "go" and self.pending and self.pending[-1][1] >= now - GO_S:
            self.pending.pop()
            self.windows.pop()
            self.prompts_done -= 1
        if self.phase == "free":
            self.free.append((self.free_start, now))
        self.paused = True
        self.say("PAUSED - press . to carry on", ORANGE)

    def on_calibrated(self):
        self.calibrated = True
        if self.phase == "waiting":
            self.say("calibrated. Press . to start", GREEN)

    def next_prompt(self):
        left = self.remaining()
        for label, _, _ in self.pending:  # prompts still waiting for their data count as asked
            left[label] -= 1
        bag = [k for k, n in left.items() for _ in range(n)]
        return random.choice(bag) if bag else None

    def advance(self, now):
        if self.phase in ("announce", "go", "free"):
            if self.phase == "free":
                self.free.append((self.free_start, now))
            if self.prompts_done and self.prompts_done % FREE_EVERY == 0 and self.phase == "go":
                self.phase, self.phase_end, self.free_start = "free", now + FREE_S, now
                beep("FREE")
                return
            self.prompt = self.next_prompt()
            if self.prompt is None:
                self.phase = "done"
                self.say("ALL DONE. Train with python punch_train.py", GREEN)
                return
            self.phase, self.phase_end = "rest", now + REST_S
        elif self.phase == "rest":
            self.phase, self.phase_end = "go", now + GO_S
            self.windows.append((self.prompt, now, now + GO_S))
            self.pending.append((self.prompt, now, now + GO_S))
            self.prompts_done += 1
            beep(self.prompt)

    def in_prompt_window(self, t):
        return any(start - 0.2 <= t <= end + LATE_S for _, start, end in self.windows)

    def on_frame(self, keys=None):
        now = time.time()
        if keys is not None:
            if keys.period():
                self.toggle()
            keys.poll_fullscreen()
        # NONE examples only while a session is running: not while waiting, paused or finished
        recording = self.phase in ("announce", "rest", "go", "free") and not self.paused
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "end" and value.reason != "gap" and recording and not self.in_prompt_window(value.start_t):
                self.none_pending.append(value)
        if not self.paused and self.phase not in ("waiting", "done") and now >= self.phase_end:
            self.advance(now)
        latest = self.sensors.latest_imu_t() or 0.0
        wait = self.sensors.settings.timeout_s + POST_S
        for item in [p for p in self.pending if latest >= p[2] + wait]:
            self.pending.remove(item)
            label, start, end = item
            seg = self.save_block(label, start, end, quiet=True)
            if seg is None:
                self.say(f"MISSED {label}: no punch in the 1.6 s after the beep. It will come up again.", RED)
            else:
                self.say(f"saved {label} {self.counts[label]}/{self.target}: {1000 * seg.duration:.0f} ms, "
                         f"{seg.peak_g:.1f} g", GREEN)
        for seg in [s for s in self.none_pending if latest >= s.end_t + POST_S]:
            self.none_pending.remove(seg)
            if self.save_block(NONE, seg.start_t - 0.05, seg.start_t + 0.05, quiet=True) is not None:
                where = "while moving freely" if any(a <= seg.start_t <= b for a, b in self.free) or self.phase == "free" else "between prompts"
                print(f"saved NONE {self.counts[NONE]} (a movement {where})")
        return self.quit or self.phase == "done" and not self.pending and not self.none_pending

    def on_draw(self, frame):
        h, now = frame.shape[0], time.time()
        top = 150
        if self.phase == "waiting" or self.paused:
            reason = self.not_ready_reason()
            title = "PAUSED" if self.paused else "READY TO RECORD"
            ps.put(frame, title, top, ORANGE if self.paused else YELLOW, 1.1, thick=3)
            ps.put(frame, reason or "press . to " + ("carry on" if self.paused else "start"), top + 32,
                   ORANGE if reason else (255, 255, 255), 0.7)
        elif self.phase == "announce":
            ps.put(frame, f"GET IN GUARD - starting in {max(0.0, self.phase_end - now):.0f}", top, YELLOW, 1.0, thick=3)
        elif self.phase == "rest":
            ps.put(frame, "guard...", top, GREY, 1.1, thick=3)
            ps.put(frame, f"next: {self.prompt}", top + 32, GREY, 0.6)
        elif self.phase == "go":
            ps.put(frame, f"{self.prompt}!", top, GREEN, 2.0, thick=5)
            ps.put(frame, HOW[self.prompt], top + 40, (255, 255, 255), 0.7)
        elif self.phase == "free":
            ps.put(frame, f"MOVE, DON'T PUNCH  {max(0.0, self.phase_end - now):.0f}", top, ORANGE, 1.1, thick=3)
            ps.put(frame, "shuffle, adjust your guard, bob, feint", top + 32, (255, 255, 255), 0.6)
        elif self.phase == "done":
            ps.put(frame, "ALL DONE - train with python punch_train.py", top, GREEN, 0.8)
        draw_common(self, frame, top + 70, False)
        ps.put(frame, ". start/pause  |  F11 full screen  |  terminal: x undo, q quit", h - ps.TEXT_BOTTOM, GREY, 0.45)


def draw_common(cap, frame, row, button_held):
    h = frame.shape[0]
    totals = "  ".join(f"{k} {cap.counts.get(k, 0)}/{cap.target}" for k in ps.LABELS) + f"  NONE {cap.counts.get(NONE, 0)}"
    ps.put(frame, totals, row, GREY, 0.55)
    if not cap.sensors.glove_ok():
        ps.put(frame, "NO GLOVE DATA", row + 30, RED, 0.7)
    elif button_held:
        ps.put(frame, "BUTTON HELD - recording", row + 30, RED, 0.8, thick=3)
    if time.time() - cap.result_t < 3:
        ps.put(frame, cap.result[:75], h - ps.TEXT_BOTTOM - 25, cap.result_colour, 0.55)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", type=int, default=100, help="punches wanted of each kind")
    parser.add_argument("--person", default="", help="who is punching (saved with each block)")
    parser.add_argument("--button", action="store_true", help="hold the glove button around each punch")
    parser.add_argument("--data", default=ps.DATA_DIR, help="where to save blocks")
    parser.add_argument("--simulate", action="store_true", help="fake glove (no hardware)")
    parser.add_argument("--windowed", action="store_true", help="start in a normal window, not full screen")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", type=int, default=5005, help="UDP port the glove sends to")
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    args = parser.parse_args()

    config = ps.load_config()
    sensors = ps.LiveSensors(config["segment"], simulate=args.simulate, port=args.port)
    cls = ButtonCapture if args.button else PromptedCapture
    cap = cls(sensors, config, args.target, args.data, args.person)
    print("Recording to", os.path.abspath(args.data), "| already have:", cap.counts)
    print(cap.HELP)
    threading.Thread(target=cap.read_terminal, daemon=True).start()
    keys = ps.ToolKeys(fullscreen=not args.windowed)
    started = time.time()

    def on_frame():
        return cap.on_frame(keys) or bool(args.max_seconds and time.time() - started > args.max_seconds)

    import pose_tracker
    try:
        pose_tracker.main(on_frame=on_frame, on_draw=cap.on_draw, on_calibrated=cap.on_calibrated,
                          camera_index=args.camera, fullscreen=not args.windowed, **sensors.pose_callbacks())
    finally:
        sensors.close()
    print("Saved this session:", len(cap.saved), "| totals:", cap.counts)
    return cap


if __name__ == "__main__":
    main()
