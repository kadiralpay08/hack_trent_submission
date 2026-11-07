"""Record labelled training punches.

  python punch_capture.py --person andre               prompted (default): the screen calls the punch
  python punch_capture.py --target 20 --person andre   a smaller set, for a first test
  python punch_capture.py --button                     hold the glove button around each punch instead
  python punch_capture.py --simulate                   fake glove (no hardware), to try the tool
  python punch_capture.py --both --right-phone 172.20.10.1 --person andre
                                                       BOTH ARMS at once, in combos (see below)

BOTH ARMS (--both): one session records everything for both arms. Each prompt is a combo, one punch per
arm, such as "1. LEFT HOOK  2. RIGHT UPPERCUT": throw them in that order within 2.4 s of the beep. Half the
combos start with the left arm and half with the right, in random order. Each punch is labelled from its own
arm's sensor and saved to that arm's folder (punch_data/ or punch_data_right/), so each arm still trains on
its own. NONE examples are collected for both arms (between prompts and in MOVE rounds); if an arm still has
fewer NONE than --target when the punches are done, extra MOVE rounds top it up (at most 6). The right arm is
the glove on port 5006, or a phone with --right-phone ADDRESS.

When any session ends, each arm that recorded something is retrained on everything in its folder, NONE
included, and its old model is kept as models/<name>.joblib.bak. --no-train skips that.

PROMPTED MODE (default). Nothing starts until you have calibrated (press C, get into guard during the
5 s countdown) and pressed "." (works from any window). "." also pauses and resumes at any time.
The screen shows "JAB!", "HOOK!" or "UPPERCUT!" with a beep, in random order.
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
  x       undo the last saved punch (moves it to the arm's _rejected/ folder)
  discard throw away this whole session and quit without training (punch_sessions.py --restore brings it back)
  j/h/u   button mode only: switch to JAB / HOOK / UPPERCUT
  q       quit (everything saved so far is kept); q in the camera window also works

The data stays on this computer in punch_data/<LABEL>/ (or punch_data_right/ with --arm right).
Train with python punch_train.py (add --arm right for the right arm).
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

NONE = "NONE"
PRE_S, POST_S = 1.0, 0.3       # data kept before and after each block
MIN_HOLD_S, MAX_HOLD_S = 0.15, 8.0
REST_S, GO_S, LATE_S = 1.6, 1.6, 0.6   # prompted: guard, punch window, grace after the window
FREE_EVERY, FREE_S, ANNOUNCE_S = 15, 15.0, 4.0
GREEN, RED, ORANGE, WHITE, YELLOW = (0, 220, 0), (0, 0, 255), (0, 150, 255), (255, 255, 255), (0, 255, 255)
TONES = {"JAB": 880, "CROSS": 880, "HOOK": 660, "UPPERCUT": 520, "FREE": 300}


def beep(kind):
    if sys.platform != "win32":
        return
    import winsound
    threading.Thread(target=winsound.Beep, args=(TONES.get(kind, 700), 120), daemon=True).start()


class Capture:
    """Shared by both modes: counting, saving and checking blocks, undo, and the terminal."""

    def __init__(self, sensors, config, target, root, person, arm=None):
        self.sensors, self.config, self.target, self.root, self.person = sensors, config, target, root, person
        self.arm = arm or ps.ARMS["left"]
        self.how = self.arm.how
        self.keys = {label[0].lower(): label for label in self.arm.labels}  # j or c, h, u
        self.counts = ps.count_blocks(root, self.arm.labels)
        self.counts.setdefault(NONE, 0)
        self.saved = []              # (path, label) saved this session, for undo
        self.session = time.strftime("%Y%m%d_%H%M%S")
        self.result, self.result_colour, self.result_t = "", WHITE, 0.0
        self.quit = False
        self.calibrated = False  # set by pose_tracker after C and its 5 s countdown

    def on_calibrated(self):
        self.calibrated = True

    def remaining(self):
        return {k: max(0, self.target - self.counts.get(k, 0)) for k in self.arm.labels}

    def say(self, text, colour=WHITE):
        print(text)
        self.result, self.result_colour, self.result_t = text, colour, time.time()

    def read_terminal(self):
        while not self.quit:
            try:
                self.command(input().strip().lstrip("\ufeff").lower())  # a byte-order mark
            except EOFError:
                return

    def command(self, word):
        if word == "x":
            self.undo()
        elif word == "discard":
            self.discard()
            self.quit = True
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

    def discard(self):
        """Throw away everything this session saved (recoverable: punch_sessions.py --restore <session>)."""
        for path, label in self.saved:
            folder = os.path.join(self.root, "_deleted", self.session, label)
            os.makedirs(folder, exist_ok=True)
            shutil.move(path, os.path.join(folder, os.path.basename(path)))
            self.counts[label] -= 1
        n, self.saved = len(self.saved), []
        self.say(f"discarded this session ({n} recordings). Bring it back with: "
                 f"python punch_sessions.py --restore {self.session}", ORANGE)

    def save_block(self, label, t0, t1, quiet=False, extra=None):
        """Check and save the data around [t0, t1] as one block. Returns the punch Segment or None.
        extra is added to the block's meta (two-arm mode records the combo it came from)."""
        imu, pose = self.sensors.window(t0 - PRE_S, t1 + POST_S)
        settings = self.sensors.settings
        problem, segment, found, gravity = None, None, 0, None
        # At least 60% of the samples the sensor normally sends: its real rate (about 500 Hz for the glove, about
        # 100 Hz for a phone), so a slower sensor is not mistaken for dropped packets.
        if len(imu["t"]) < 0.6 * self.sensors.sample_rate_hz() * (t1 - t0 + PRE_S + POST_S):
            problem = "too little glove data arrived (WiFi dropping?)"
        else:
            gravity = self.sensors.gravity_at(imu["t"][0])
            segments = punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], settings, gravity)
            segment, found = punch_segment.pick_block_segment(segments, t0, t1)
            if segment is None:
                problem = "no punch found"
            elif segment.reason == "gap":
                problem = "the glove data had a gap during the punch"
        meta = {"label": label, "arm": self.arm.name, "press_t": t0, "release_t": t1, "session": self.session, "person": self.person,
                "mode": self.MODE, "segment_settings": settings.to_dict(), "problem": problem,
                "segments_found": found, "pose_frames": int(len(pose["t"])),
                "start_gravity": None if gravity is None else [float(v) for v in gravity]}
        meta.update(extra or {})
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
    HELP = "type the first letter of a punch to switch to it (j or c, h, u), x to undo, q to quit"

    def __init__(self, *args):
        super().__init__(*args)
        self.label = self.next_label()
        self.press_t, self.pending = None, None

    def next_label(self):
        return next((k for k, n in self.remaining().items() if n > 0), None)

    def command(self, word):
        if word in self.keys:
            self.label = self.keys[word]
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
            ps.put(frame, f"hold button, punch {self.how[self.label]}, release", top + 32, (255, 255, 255), 0.6)
        else:
            ps.put(frame, "ALL DONE - train with python punch_train.py", top, GREEN, 0.8)
        draw_common(self, frame, top + 58, self.press_t is not None)
        ps.put(frame, "terminal: j/h/u switch punch, x undo, q quit  |  F11 full screen", h - ps.TEXT_BOTTOM, WHITE, 0.45)


class PromptedCapture(Capture):
    MODE = "prompted"
    HELP = "calibrate with C, then press . to start (. pauses and resumes); terminal: x undo, discard, q quit"

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
            if reason != "no glove data":  # that one already shows in red below
                ps.put(frame, reason or "press . to " + ("carry on" if self.paused else "start"), top + 32,
                       ORANGE if reason else (255, 255, 255), 0.7)
        elif self.phase == "announce":
            ps.put(frame, f"GET IN GUARD - starting in {max(0.0, self.phase_end - now):.0f}", top, YELLOW, 1.0, thick=3)
        elif self.phase == "rest":
            ps.put(frame, "guard...", top, WHITE, 1.1, thick=3)
            ps.put(frame, f"next: {self.prompt}", top + 32, WHITE, 0.6)
        elif self.phase == "go":
            ps.put(frame, f"{self.prompt}!", top, GREEN, 2.0, thick=5)
            ps.put(frame, self.how[self.prompt], top + 40, (255, 255, 255), 0.7)
        elif self.phase == "free":
            ps.put(frame, f"MOVE, DON'T PUNCH  {max(0.0, self.phase_end - now):.0f}", top, ORANGE, 1.1, thick=3)
            ps.put(frame, "shuffle, adjust your guard, bob, feint", top + 32, (255, 255, 255), 0.6)
        elif self.phase == "done":
            ps.put(frame, "ALL DONE - train with python punch_train.py", top, GREEN, 0.8)
        draw_common(self, frame, top + 70, False)
        ps.put(frame, ". start/pause  |  F11 full screen  |  terminal: x undo, q quit", h - ps.TEXT_BOTTOM, WHITE, 0.45)


COMBO_S = 2.4   # two-arm mode: time to throw both punches of a combo, after the beep
MAX_EXTRA_FREE = 6   # two-arm mode: at most this many MOVE rounds at the end to top up NONE examples (90 s)


class ArmRecorder(Capture):
    """One arm's half of two-arm mode: its own sensor, folder, counts and undo, checked and saved exactly as in
    one-arm mode, so its recordings train with punch_train.py --arm <name> like any others."""
    MODE = "prompted_both"
    HELP = ""

    def __init__(self, owner, *args):
        super().__init__(*args)
        self.owner = owner
        self.none_pending = []   # this arm's Segments to save as NONE once their data has arrived

    def say(self, text, colour=WHITE):
        self.owner.say(f"{self.arm.name.upper()}: {text}", colour)


def combo_text(combo):
    return " > ".join(f"{name.upper()} {label}" for name, label in combo)


class BothCapture:
    """Two-arm prompted mode (--both). Each prompt is a combo, one punch per arm, such as "LEFT HOOK > RIGHT UPPERCUT":
    throw them in that order after the beep. Each punch is labelled and saved by its own arm's recorder, from its own
    sensor, into that arm's folder, so the two arms still train separately. Half the combos start with the left arm
    and half with the right, shuffled. Movement outside the prompts is saved as NONE for the arm that moved, which
    includes the small jolt one arm gets while the other punches."""
    MODE = "prompted_both"
    HELP = PromptedCapture.HELP

    def __init__(self, recorders, target, rng=None):
        self.recorders = recorders          # {"left": ArmRecorder, "right": ArmRecorder}
        self.target = target
        self.rng = rng or random.Random()
        self.phase, self.phase_end, self.combo = "waiting", None, None
        self.prompts_done, self.paused, self.calibrated = 0, False, False
        self.windows = []        # (combo, start, end) of every prompt, to tell punches from NONE
        self.pending = []        # (combo, start, end) prompts waiting for their data
        self.free = []           # (start, end) of MOVE, DON'T PUNCH rounds
        self.firsts = []         # which arm goes first, drawn without putting back: half left, half right
        self.first_counts = dict.fromkeys(sorted(ps.ARMS), 0)
        self.order = []          # arm of every block saved this session, newest last, for undo
        self.extra_free = 0      # MOVE rounds added at the end to collect enough NONE examples
        self.result, self.result_colour, self.result_t = "", WHITE, 0.0
        self.quit = False

    # --- terminal, messages and undo ---
    def say(self, text, colour=WHITE):
        print(text)
        self.result, self.result_colour, self.result_t = text, colour, time.time()

    read_terminal = Capture.read_terminal

    def command(self, word):
        word = word.strip(".")
        if word == "p":
            self.toggle()
        elif word == "x":
            if self.order:
                self.recorders[self.order.pop()].undo()
            else:
                self.say("nothing to undo")
        elif word == "discard":
            for rec in self.recorders.values():
                rec.discard()
            self.order, self.quit = [], True
        elif word in ("q", "quit"):
            self.quit = True
        elif word:
            self.say(self.HELP)

    @property
    def saved(self):
        return [item for rec in self.recorders.values() for item in rec.saved]

    # --- start and pause, as in one-arm prompted mode ---
    def on_calibrated(self):
        self.calibrated = True
        if self.phase == "waiting":
            self.say("calibrated. Press . to start", GREEN)

    def not_ready_reason(self):
        for name, rec in self.recorders.items():
            if not rec.sensors.glove_ok():
                return f"no {name} arm data"
            if not rec.sensors.segmenter.ready:
                return f"hold the {name} arm still for a moment"
        if not self.calibrated:
            return "press C to calibrate (get into guard during the countdown)"
        return None

    def toggle(self):
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
        if self.phase == "go" and self.pending and self.pending[-1][1] >= now - COMBO_S:  # cancel the showing combo
            combo, _, _ = self.pending.pop()
            self.windows.pop()
            self.prompts_done -= 1
            self.first_counts[combo[0][0]] -= 1
            self.firsts.append(combo[0][0])  # its turn to go first is not used up
        if self.phase == "free":
            self.free.append((self.free_start, now))
        self.paused = True
        self.say("PAUSED - press . to carry on", ORANGE)

    # --- choosing combos ---
    def still_to_ask(self):
        """Per arm, how many of each punch are still wanted, not counting prompts waiting for their data."""
        left = {name: rec.remaining() for name, rec in self.recorders.items()}
        for combo, _, _ in self.pending:
            for name, label in combo:
                left[name][label] -= 1
        return left

    def next_combo(self):
        bags = {name: [k for k, n in want.items() for _ in range(n)] for name, want in self.still_to_ask().items()}
        if not any(bags.values()):
            return None
        if not self.firsts:
            # Enough turns for every combo still needed, exactly half each, shuffled. A missed punch comes up again,
            # and if that needs more combos the bag is refilled the same way, so the split stays within one.
            needed = max(len(b) for b in bags.values())
            names = sorted(self.recorders)
            self.firsts = [names[i % 2] for i in range(needed + needed % 2)]
            self.rng.shuffle(self.firsts)
        first = self.firsts.pop()
        second = next(name for name in self.recorders if name != first)
        pick = {name: self.rng.choice(bags[name]) if bags[name] else self.rng.choice(self.recorders[name].arm.labels)
                for name in self.recorders}  # an arm that is done still punches, so the other can finish
        return [(first, pick[first]), (second, pick[second])]

    def advance(self, now):
        if self.phase in ("announce", "go", "free"):
            if self.phase == "free":
                self.free.append((self.free_start, now))
            if self.prompts_done and self.prompts_done % FREE_EVERY == 0 and self.phase == "go":
                self.phase, self.phase_end, self.free_start = "free", now + FREE_S, now
                beep("FREE")
                return
            self.combo = self.next_combo()
            if self.combo is None:
                short = self.none_short()
                if short and self.extra_free < MAX_EXTRA_FREE:  # punches done, but too few NONE examples
                    self.extra_free += 1
                    self.phase, self.phase_end, self.free_start = "free", now + FREE_S, now
                    self.say(f"punches done; more NONE examples wanted for {' and '.join(short)}", ORANGE)
                    beep("FREE")
                    return
                self.phase = "done"
                self.say("ALL DONE", GREEN)
                return
            self.phase, self.phase_end = "rest", now + REST_S
        elif self.phase == "rest":
            self.phase, self.phase_end = "go", now + COMBO_S
            self.windows.append((self.combo, now, now + COMBO_S))
            self.pending.append((self.combo, now, now + COMBO_S))
            self.prompts_done += 1
            self.first_counts[self.combo[0][0]] += 1
            beep(self.combo[0][1])

    def in_prompt_window(self, t):
        return any(start - 0.2 <= t <= end + LATE_S for _, start, end in self.windows)

    def none_short(self):
        """Arms with fewer NONE examples than the target (NONE examples still being saved count)."""
        return [name for name, rec in self.recorders.items()
                if rec.counts.get(NONE, 0) + len(rec.none_pending) < self.target]

    # --- saving ---
    def data_arrived(self, end):
        return all((rec.sensors.latest_imu_t() or 0.0) >= end + rec.sensors.settings.timeout_s + POST_S
                   for rec in self.recorders.values())

    def save_combo(self, combo, start, end):
        text, segs, saved, missed = combo_text(combo), {}, [], []
        for position, (name, label) in enumerate(combo, 1):
            rec = self.recorders[name]
            seg = rec.save_block(label, start, end, quiet=True, extra={"combo": text, "combo_position": position})
            if seg is None:
                missed.append(f"{name.upper()} {label}")
            else:
                segs[name] = seg
                self.order.append(name)
                saved.append(f"{name.upper()} {label} {rec.counts[label]}/{self.target}")
        (first, _), (second, _) = combo
        swapped = first in segs and second in segs and segs[second].start_t < segs[first].start_t - 0.05
        if missed:
            self.say(f"MISSED {' and '.join(missed)}: no punch after the beep. It will come up again."
                     + (f" Saved {', '.join(saved)}." if saved else ""), RED)
        else:
            self.say("saved " + " + ".join(saved) + ("  (thrown in the other order; kept)" if swapped else ""),
                     ORANGE if swapped else GREEN)

    def on_frame(self, keys=None):
        now = time.time()
        if keys is not None:
            if keys.period():
                self.toggle()
            keys.poll_fullscreen()
        recording = self.phase in ("announce", "rest", "go", "free") and not self.paused
        for rec in self.recorders.values():
            while not rec.sensors.events.empty():
                kind, value = rec.sensors.events.get()
                if kind == "end" and value.reason != "gap" and recording and not self.in_prompt_window(value.start_t):
                    rec.none_pending.append(value)
        if not self.paused and self.phase not in ("waiting", "done") and now >= self.phase_end:
            self.advance(now)
        for item in [p for p in self.pending if self.data_arrived(p[2])]:
            self.pending.remove(item)
            self.save_combo(*item)
        for name, rec in self.recorders.items():
            latest = rec.sensors.latest_imu_t() or 0.0
            for seg in [s for s in rec.none_pending if latest >= s.end_t + POST_S]:
                rec.none_pending.remove(seg)
                if rec.save_block(NONE, seg.start_t - 0.05, seg.start_t + 0.05, quiet=True) is not None:
                    self.order.append(name)
                    print(f"saved {name.upper()} NONE {rec.counts[NONE]}")
        waiting = self.pending or any(rec.none_pending for rec in self.recorders.values())
        return self.quit or self.phase == "done" and not waiting

    # --- screen ---
    def on_draw(self, frame):
        h, now = frame.shape[0], time.time()
        top = 150
        if self.phase == "waiting" or self.paused:
            reason = self.not_ready_reason()
            ps.put(frame, "PAUSED" if self.paused else "READY TO RECORD (BOTH ARMS)", top,
                   ORANGE if self.paused else YELLOW, 1.1, thick=3)
            ps.put(frame, reason or "press . to " + ("carry on" if self.paused else "start"), top + 32,
                   ORANGE if reason else (255, 255, 255), 0.7)
        elif self.phase == "announce":
            ps.put(frame, f"GET IN GUARD - starting in {max(0.0, self.phase_end - now):.0f}", top, YELLOW, 1.0, thick=3)
        elif self.phase == "rest":
            ps.put(frame, "guard...", top, WHITE, 1.1, thick=3)
            ps.put(frame, f"next: {combo_text(self.combo)}", top + 32, WHITE, 0.6)
        elif self.phase == "go":
            for i, (name, label) in enumerate(self.combo):
                ps.put(frame, f"{i + 1}. {name.upper()} {label}!", top + 50 * i, GREEN if i == 0 else YELLOW, 1.4, thick=4)
        elif self.phase == "free":
            ps.put(frame, f"MOVE, DON'T PUNCH  {max(0.0, self.phase_end - now):.0f}", top, ORANGE, 1.1, thick=3)
            ps.put(frame, "shuffle, adjust your guard, bob, feint", top + 32, (255, 255, 255), 0.6)
        elif self.phase == "done":
            ps.put(frame, "ALL DONE - train each arm with punch_train.py --arm left / right", top, GREEN, 0.7)
        row = top + 110
        for name, rec in self.recorders.items():
            totals = "  ".join(f"{k} {rec.counts.get(k, 0)}/{self.target}" for k in rec.arm.labels)
            ps.put(frame, f"{name.upper():5}  {totals}  NONE {rec.counts.get(NONE, 0)}", row, WHITE, 0.55)
            row += 24
        ps.put(frame, "  ".join(f"{name} first: {n}" for name, n in self.first_counts.items()), row, WHITE, 0.5)
        missing = [name.upper() for name, rec in self.recorders.items() if not rec.sensors.glove_ok()]
        if missing:
            ps.put(frame, f"NO {' / '.join(missing)} ARM DATA", row + 30, RED, 0.7)
        if now - self.result_t < 3:
            ps.put(frame, self.result[:80], h - ps.TEXT_BOTTOM - 25, self.result_colour, 0.55)
        ps.put(frame, ". start/pause  |  F11 full screen  |  terminal: x undo, q quit", h - ps.TEXT_BOTTOM, WHITE, 0.45)


def draw_common(cap, frame, row, button_held):
    h = frame.shape[0]
    totals = "  ".join(f"{k} {cap.counts.get(k, 0)}/{cap.target}" for k in cap.arm.labels) + f"  NONE {cap.counts.get(NONE, 0)}"
    ps.put(frame, totals, row, WHITE, 0.55)
    if not cap.sensors.glove_ok():
        ps.put(frame, "NO GLOVE DATA", row + 30, RED, 0.7)
    elif button_held:
        ps.put(frame, "BUTTON HELD - recording", row + 30, RED, 0.8, thick=3)
    if time.time() - cap.result_t < 3:
        ps.put(frame, cap.result[:75], h - ps.TEXT_BOTTOM - 25, cap.result_colour, 0.55)


def train_arm(arm, recorded):
    """One and done: train the arm's model on everything in its folder (NONE included) when a session saved
    anything. The model it replaces is kept beside it as .bak."""
    if not recorded:
        print(f"{arm.name.upper()}: nothing new recorded, so its model is unchanged.")
        return None
    import punch_train
    if os.path.exists(arm.model_path):
        shutil.copy2(arm.model_path, arm.model_path + ".bak")
    print()
    print(f"=== Training the {arm.name} arm (the previous model is kept as {os.path.relpath(arm.model_path)}.bak) ===")
    return punch_train.main(["--arm", arm.name])


def main_both(args):
    """--both: one camera, both arms' sensors, combo prompts; each arm records into its own folder."""
    clash = [flag for flag, used in (("--button", args.button), ("--arm", args.arm != "left"), ("--phone", args.phone),
                                     ("--data", args.data), ("--port", args.port)) if used]
    if clash:
        raise SystemExit(f"--both records each arm to its own folder and port, so it can't be used with {', '.join(clash)}."
                         " For a phone as the right arm, use --right-phone ADDRESS.")
    config = ps.load_config()
    recorders = {}
    cap = BothCapture(recorders, args.target)
    for name in ("left", "right"):
        arm = ps.ARMS[name]
        phone = args.right_phone if name == "right" else None
        sensors = ps.LiveSensors(config["segment"], simulate=args.simulate, port=arm.port, arm=arm, phone=phone)
        recorders[name] = ArmRecorder(cap, sensors, config, args.target, arm.data_dir, args.person, arm)
        source = f"phone at {phone}" if phone else f"glove on port {arm.port}"
        print(f"{name.upper()} ARM: punches {', '.join(arm.labels)}, {source}, recording to "
              f"{os.path.relpath(arm.data_dir)} | already have: {recorders[name].counts}")
    for rec in recorders.values():  # one session name for both arms, so it is listed and deleted as one
        rec.session = recorders["left"].session
    print(f"Session {recorders['left'].session}. " + cap.HELP)
    threading.Thread(target=cap.read_terminal, daemon=True).start()
    keys = ps.ToolKeys(fullscreen=not args.windowed)
    started = time.time()

    def on_frame():
        return cap.on_frame(keys) or bool(args.max_seconds and time.time() - started > args.max_seconds)

    import pose_tracker
    try:
        pose_tracker.main(on_frame=on_frame, on_draw=cap.on_draw, on_calibrated=cap.on_calibrated,
                          camera_index=args.camera, fullscreen=not args.windowed,
                          **ps.shared_pose_callbacks(*[rec.sensors for rec in recorders.values()]))
    finally:
        for rec in recorders.values():
            rec.sensors.close()
    for name, rec in recorders.items():
        print(f"{name.upper()}: saved this session {len(rec.saved)} | totals: {rec.counts}")
    print("first punch of each combo:", cap.first_counts)
    if not args.no_train:
        for name, rec in recorders.items():
            train_arm(ps.ARMS[name], bool(rec.saved))
    return cap


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", type=int, default=100, help="punches wanted of each kind")
    parser.add_argument("--person", default="", help="who is punching (saved with each block)")
    parser.add_argument("--button", action="store_true", help="hold the glove button around each punch")
    ps.add_arm_argument(parser)
    ps.add_phone_argument(parser)
    parser.add_argument("--data", help="where to save blocks (default: the arm's folder, punch_data or punch_data_right)")
    parser.add_argument("--simulate", action="store_true", help="fake glove (no hardware)")
    parser.add_argument("--windowed", action="store_true", help="start in a normal window, not full screen")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", type=int, help="UDP port the glove sends to (default: the arm's, 5005 or 5006)")
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    parser.add_argument("--no-train", action="store_true",
                        help="don't train when the session ends (by default each recorded arm is retrained, NONE included)")
    parser.add_argument("--both", action="store_true",
                        help="record both arms at once, in combos such as LEFT HOOK > RIGHT UPPERCUT; each arm is still "
                             "saved to its own folder and trained on its own")
    parser.add_argument("--right-phone", metavar="ADDRESS",
                        help="with --both: a phone running phyphox as the right arm's sensor, instead of a glove on port 5006")
    args = parser.parse_args(argv)
    if args.both:
        return main_both(args)
    if args.right_phone:
        raise SystemExit("--right-phone is only for --both. For one arm, use --arm right --phone ADDRESS.")
    arm = ps.ARMS[args.arm]
    args.data, args.port = args.data or arm.data_dir, args.port or arm.port

    config = ps.load_config()
    sensors = ps.LiveSensors(config["segment"], simulate=args.simulate, port=args.port, arm=arm, phone=ps.phone_for(args))
    cls = ButtonCapture if args.button else PromptedCapture
    cap = cls(sensors, config, args.target, args.data, args.person, arm)
    source = f"phone at {args.phone}" if args.phone else f"glove on port {args.port}"
    print(f"{arm.name.upper()} ARM: punches {', '.join(arm.labels)}, {source}")
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
    if not args.no_train:
        if args.data == arm.data_dir:
            train_arm(arm, bool(cap.saved))
        elif cap.saved:
            print(f"Recorded to a different folder, so no model was trained. Train it with: "
                  f"python punch_train.py --arm {arm.name} --data {args.data} --out <model file>")
    return cap


if __name__ == "__main__":
    main()
