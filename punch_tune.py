"""Choose the punch timeout: type it in, punch, and watch where each punch starts and ends.

  python punch_tune.py              real glove (UDP port 5005) and the webcam, full screen
  python punch_tune.py --windowed   in a normal window instead
  python punch_tune.py --simulate   a fake glove that throws punches by itself (no hardware)

Type in THIS TERMINAL, then press Enter (F11 leaves full screen so you can see the terminal):
  350               set the timeout to 350 ms (a punch ends at the timeout, or earlier if it stops)
  start_g 3         change any other punch setting the same way (show lists them)
  show              print the current settings
  save              save them to punch_config.json, which capture, training and live play all use
  test 10 60 jab    set up a timed test: 10 jabs in 60 seconds (any number, time and punch; the punch
                    is optional). It waits until you have calibrated (C) and you press "." to start.
                    Afterwards it says how many punches it found with the current timeout and, once a
                    model is trained, how many it classified as jabs, and compares other timeouts.
  q                 quit
Keys that work in any window, no Enter needed:
  .                 start the timed test, and pause or resume it at any time
  F11               full screen on or off
The camera window shows the glove's acceleration, a green line where each punch started, and a blue
(stopped) or orange (timed out) line where it ended. Hold still for a moment first, so gravity is known.

Change the timeout BEFORE recording training data, or retrain afterwards, because the model learns from
punches cut at this timeout.
"""
import argparse
import sys
import threading
import time

import punch_segment
import punch_session as ps

GREEN, BLUE, ORANGE, RED, GREY, YELLOW = (0, 220, 0), (255, 170, 0), (0, 150, 255), (0, 0, 255), (180, 180, 180), (0, 255, 255)
COUNTDOWN_S = 3.0
GRACE_S = 0.7   # after the time runs out, wait this long so a punch in progress can finish
COMPARE_TIMEOUTS_MS = (250, 300, 350, 400, 450, 500, 600)
KINDS = {"jab": "JAB", "jabs": "JAB", "hook": "HOOK", "hooks": "HOOK", "uppercut": "UPPERCUT", "uppercuts": "UPPERCUT"}


def score_test(imu, pose, intervals, settings, classifier=None, kind=None, timeouts_ms=COMPARE_TIMEOUTS_MS):
    """Cut a recorded test at each timeout, counting punches that started inside the active intervals
    (the test minus any pauses). Returns [(timeout_ms, found, Counter of labels, unsure)]."""
    from collections import Counter
    rows = []
    for ms in sorted(set(list(timeouts_ms) + [round(1000 * settings.timeout_s)])):
        s = punch_segment.SegmentSettings.from_dict({**settings.to_dict(), "timeout_s": ms / 1000})
        segs = [g for g in punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], s)
                if g.reason != "gap" and any(a <= g.start_t <= b for a, b in intervals)]
        labels, unsure = Counter(), 0
        if classifier is not None:
            for g in segs:
                label, conf = classifier.classify(imu, pose, g, s.pre_s)
                if label is None or conf < classifier.min_confidence:
                    unsure += 1
                else:
                    labels[label] += 1
        rows.append((ms, len(segs), labels, unsure))
    return rows


class Tuner:
    def __init__(self, sensors, config, arm=None):
        self.sensors, self.config = sensors, config
        self.marks = []          # (t, colour) for the trace
        self.active = None       # Segment in progress
        self.last = None         # last finished Segment
        self.count = 0
        self.quit = False
        self.calibrated = False  # set when pose_tracker calibrates (C, then a 5 s countdown)
        self.message, self.message_t = "", 0.0
        self.test = None         # the timed test, if one is set up
        self.report, self.report_t = [], 0.0
        self.arm = arm or ps.ARMS["left"]
        path = self.arm.model_path
        self.classifier = ps.Classifier(path) if ps.Classifier.exists(path) else None

    # --- typed commands ---
    def command(self, line):
        line = line.strip().lstrip("\ufeff").strip()  # a byte-order mark PowerShell can add
        dots_only = bool(line) and set(line) == {"."}
        # The "." key works in every window, so pressing it while the terminal has focus also types a dot
        # there. Ignore dots at the ends of a line (inner ones, as in 0.35, are kept).
        line = line.strip(".").strip()
        if not line:
            if dots_only and sys.platform != "win32":
                self.toggle_test()  # without Windows global keys, "." then Enter does the same job
            return
        settings = self.sensors.settings.to_dict()
        word, *rest = line.replace("=", " ").split()
        try:
            if word in ("q", "quit", "exit"):
                self.quit = True
            elif word == "show":
                for k, v in settings.items():
                    print(f"  {k} = {v:g}")
            elif word == "save":
                ps.save_config(self.config)
                self.say(f"saved to {ps.CONFIG_PATH}")
            elif word == "test":
                self.set_up_test(rest)
            elif word in settings and rest:
                settings[word] = float(rest[0])
                self.apply(settings, f"{word} = {float(rest[0]):g}")
            else:
                value = float(word)
                ms = value if value > 10 else value * 1000  # "350" or "0.35"
                if not 100 <= ms <= 1500:
                    self.say(f"{ms:.0f} ms is outside 100 to 1500 ms; timeout not changed")
                    return
                settings["timeout_s"] = ms / 1000
                self.apply(settings, f"timeout = {ms:.0f} ms")
        except ValueError:
            self.say(f"did not understand {line!r}; type a timeout in ms, a setting name and value, show, save or q")

    # --- timed test ---
    def set_up_test(self, words):
        """test [punches] [seconds] [jab|hook|uppercut]"""
        numbers = [float(w) for w in words if w.replace(".", "", 1).isdigit()]
        kind = next((KINDS[w.lower()] for w in words if w.lower() in KINDS), None)
        n = int(numbers[0]) if numbers else 10
        seconds = numbers[1] if len(numbers) > 1 else 60.0
        if seconds > 120:
            raise ValueError("a test can be at most 120 s")
        what = f"{n} {kind + 'S' if kind else 'punches'}"
        # state: waiting -> countdown -> running <-> paused (-> countdown) ... -> finishing
        self.test = {"n": n, "seconds": seconds, "left": seconds, "kind": kind, "what": what, "state": "waiting",
                     "intervals": [], "seen": 0}
        self.say(f"TEST SET UP: {what} in {seconds:g} s. Calibrate (C), then press . to start")

    def not_ready_reason(self):
        if not self.sensors.glove_ok():
            return "no glove data"
        if not self.sensors.segmenter.ready:
            return "hold the glove still for a moment"
        if not self.calibrated:
            return "press C to calibrate first (stand in guard)"
        return None

    def toggle_test(self):
        test, now = self.test, time.time()
        if test is None:
            self.say("no test set up: type e.g. test 10 60 jab in the terminal first")
        elif test["state"] in ("waiting", "paused"):
            reason = self.not_ready_reason()
            if reason:
                self.say(f"cannot start yet: {reason}")
                return
            test["state"], test["go_wall"] = "countdown", now + COUNTDOWN_S
        elif test["state"] == "countdown":
            test["state"] = "paused" if test["intervals"] else "waiting"
            self.say("countdown cancelled")
        elif test["state"] == "running":
            test["intervals"].append((test["run_t"], self.sensors.latest_imu_t() or test["run_t"]))
            test["left"] -= now - test["run_wall"]
            test["state"] = "paused"
            self.say(f"PAUSED with {test['left']:.0f} s left. Punches now do not count. Press . to resume.")

    def update_test(self):
        test, now = self.test, time.time()
        if test is None:
            return
        if test["state"] == "countdown" and now >= test["go_wall"]:
            test["state"], test["run_wall"] = "running", now
            test["run_t"] = self.sensors.latest_imu_t() or 0.0
            self.say("GO!")
        elif test["state"] == "running" and now - test["run_wall"] >= test["left"]:
            test["intervals"].append((test["run_t"], self.sensors.latest_imu_t() or test["run_t"]))
            test["state"], test["finish_wall"] = "finishing", now + GRACE_S
        elif test["state"] == "finishing" and now >= test["finish_wall"]:
            self.test = None
            threading.Thread(target=self.finish_test, args=(test,), daemon=True).start()

    def counts_for_test(self, start_t):
        test = self.test
        if not test:
            return False
        if test["state"] == "running" and start_t >= test["run_t"]:
            return True
        return any(a <= start_t <= b for a, b in test["intervals"])

    def finish_test(self, test):
        settings = self.sensors.settings
        intervals = test["intervals"]
        imu, pose = self.sensors.window(intervals[0][0] - 1.0, intervals[-1][1] + GRACE_S)
        clf = self.classifier
        rows = score_test(imu, pose, intervals, settings, clf, test["kind"])
        current = round(1000 * settings.timeout_s)
        lines = [f"TEST RESULT: asked for {test['what']} in {test['seconds']:g} s"]
        if clf is None:
            lines.append("(no trained model yet, so punches are counted but not classified)")
        elif round(1000 * clf.segment.timeout_s) != current:
            lines.append(f"(the model was trained at {1000 * clf.segment.timeout_s:.0f} ms; other timeouts are"
                         " only a rough guide until you retrain)")
        best, best_score = None, -1
        for ms, found, labels, unsure in rows:
            text = f"{ms:4d} ms: found {found}/{test['n']}"
            score = found
            if clf is not None:
                if test["kind"]:
                    score = labels.get(test["kind"], 0)
                    text += f", classified as {test['kind']}: {score}/{test['n']}"
                others = ", ".join(f"{k} {v}" for k, v in sorted(labels.items()) if k != test["kind"])
                text += (f" | other: {others}" if others else "") + (f" | unsure {unsure}" if unsure else "")
            if ms == current:
                text += " <- current"
            if score > best_score:  # the shortest timeout with the best score, as rows go up in time
                best, best_score = text, score
            lines.append(text)
        for line in lines:
            print(line)
        # the camera window has room for a short version; the full table is in the terminal
        current_line = next(l for l in lines if "<- current" in l)
        short = [lines[0], "now:  " + current_line.strip()]
        if best != current_line:
            short.append("best: " + best.strip())
        short.append("(full table in the terminal)")
        self.report, self.report_t = short, time.time()

    def apply(self, settings, what):
        new = punch_segment.SegmentSettings.from_dict(settings)
        self.config["segment"] = new
        self.sensors.set_settings(new)
        self.say(what + "  (type save to keep it)")

    def say(self, text):
        print(text)
        self.message, self.message_t = text, time.time()

    def read_terminal(self):
        while not self.quit:
            try:
                self.command(input())
            except EOFError:
                return

    # --- camera loop callbacks ---
    def on_calibrated(self):
        self.calibrated = True
        if self.test and self.test["state"] == "waiting":
            self.say("calibrated. Press . to start the test")

    def on_frame(self, keys=None):
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "start":
                self.active = value
                self.marks.append((value.start_t, GREEN))
            elif kind == "end":
                self.active, self.last = None, value
                self.count += 1
                self.marks.append((value.end_t, BLUE if value.reason == "decel" else ORANGE))
                if self.counts_for_test(value.start_t):
                    self.test["seen"] += 1
                print(f"punch {self.count}: {1000 * value.duration:.0f} ms, ended by "
                      f"{'stopping' if value.reason == 'decel' else value.reason}, peak {value.peak_g:.1f} g")
        self.marks = self.marks[-40:]
        if keys is not None:
            if keys.period():
                self.toggle_test()
            keys.poll_fullscreen()
        self.update_test()
        return self.quit

    def on_draw(self, frame):
        h, w = frame.shape[:2]
        s = self.sensors.settings
        now_t, now = self.sensors.latest_imu_t() or 0.0, time.time()
        top = 150
        if not self.sensors.glove_ok():
            ps.put(frame, "NO GLOVE DATA (is it on, and on the same WiFi?)", top, RED, 0.7)
        elif not self.sensors.segmenter.ready:
            ps.put(frame, "hold the glove still for a moment...", top, ORANGE, 0.7)
        elif self.active is not None:
            ps.put(frame, f"PUNCH STARTED  {1000 * (now_t - self.active.start_t):.0f} ms", top, GREEN, 1.0, thick=3)
        elif self.last is not None:
            how = "STOPPED" if self.last.reason == "decel" else "TIMED OUT" if self.last.reason == "timeout" else "DATA GAP"
            ps.put(frame, f"PUNCH ENDED: {how} after {1000 * self.last.duration:.0f} ms",
                   top, BLUE if self.last.reason == "decel" else ORANGE, 0.8)
        ps.put(frame, f"timeout {1000 * s.timeout_s:.0f} ms | start {s.start_g:g} g | brake {s.decel_g:g} g | punches {self.count}",
               top + 28, GREY, 0.55)
        if now - self.message_t < 3:
            ps.put(frame, self.message[:75], top + 52, YELLOW, 0.5)

        test, row = self.test, top + 85
        if test:
            state = test["state"]
            if state == "waiting":
                reason = self.not_ready_reason()
                if reason == "no glove data":  # already shown in red at the top
                    reason = "waiting"
                text, colour = f"TEST {test['what']}: " + (reason if reason else "press . to start"), (ORANGE if reason else YELLOW)
            elif state == "countdown":
                text, colour = f"TEST {test['what']}: starting in {max(0.0, test['go_wall'] - now):.0f}", YELLOW
            elif state == "running":
                left = max(0.0, test["left"] - (now - test["run_wall"]))
                text, colour = f"TEST {test['what']}: {left:.0f} s left  seen {test['seen']}  (. pauses)", GREEN
            elif state == "paused":
                text, colour = f"TEST PAUSED: {test['left']:.0f} s left  seen {test['seen']}  (. resumes)", ORANGE
            else:
                text, colour = "TEST: finishing...", YELLOW
            ps.put(frame, text, row, colour, 0.75, thick=2)
        elif self.report and now - self.report_t < 30:
            for i, line in enumerate(self.report):
                ps.put(frame, line[:80], row + 20 * i, YELLOW if line.startswith(("now", "best")) else GREY, 0.45, thick=1)

        # The trace sits above the bottom strip, where pose_tracker shows calibration messages.
        trace_bottom = h - ps.TEXT_BOTTOM - 22
        ps.put(frame, "terminal: type a timeout in ms + Enter  |  . test start/pause  |  F11 full screen",
               h - ps.TEXT_BOTTOM, GREY, 0.45, thick=1)
        times, values = self.sensors.recent_linear_g(3.0)
        ps.draw_trace(frame, times, values, now_t, 3.0, 12.0, self.marks,
                      origin=(10, trace_bottom - 90), size=(w - 20, 90), threshold_g=s.start_g)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ps.add_arm_argument(parser)
    ps.add_phone_argument(parser)
    parser.add_argument("--simulate", action="store_true", help="use a fake glove (no hardware)")
    parser.add_argument("--windowed", action="store_true", help="start in a normal window, not full screen")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", type=int, help="UDP port the glove sends to (default: the arm's, 5005 or 5006)")
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    args = parser.parse_args()

    config = ps.load_config()
    arm = ps.ARMS[args.arm]
    args.port = args.port or arm.port
    print(f"{arm.name.upper()} ARM: " + (f"phone at {args.phone}" if args.phone else f"glove on port {args.port}"))
    sensors = ps.LiveSensors(config["segment"], seconds=150, simulate=args.simulate, port=args.port, arm=arm,
                             phone=ps.phone_for(args))  # room for a 2-minute test
    tuner = Tuner(sensors, config, arm)
    if tuner.classifier:
        print(f"Model loaded (trained {tuner.classifier.info['trained_at']}), so timed tests classify punches.")
    threading.Thread(target=tuner.read_terminal, daemon=True).start()
    print(__doc__.split("Type in")[1].split("Change the")[0].strip())
    keys = ps.ToolKeys(fullscreen=not args.windowed)
    started = time.time()

    def on_frame():
        return tuner.on_frame(keys) or bool(args.max_seconds and time.time() - started > args.max_seconds)

    import pose_tracker
    try:
        pose_tracker.main(on_frame=on_frame, on_draw=tuner.on_draw, on_calibrated=tuner.on_calibrated,
                          camera_index=args.camera, fullscreen=not args.windowed, **sensors.pose_callbacks())
    finally:
        sensors.close()
    print(f"{tuner.count} punches seen. Settings are only kept if you typed save.")
    return tuner


if __name__ == "__main__":
    main()
