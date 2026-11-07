"""Choose the punch timeout: type it in, punch, and watch where each punch starts and ends.

  python punch_tune.py              real glove (UDP port 5005) and the webcam
  python punch_tune.py --simulate   a fake glove that throws punches by itself (no hardware)

Type in THIS TERMINAL, then press Enter:
  350               set the timeout to 350 ms (a punch ends at the timeout, or earlier if it stops)
  start_g 3         change any other punch setting the same way (show lists them)
  show              print the current settings
  save              save them to punch_config.json, which capture, training and live play all use
  test 10 60 jab    a timed test: throw 10 jabs in 60 seconds (any number, time and punch; the
                    punch is optional). Afterwards it says how many punches it found with the
                    current timeout and, once a model is trained, how many it classified as jabs.
                    It also re-cuts the same recording at other timeouts, to compare them.
  q                 quit
The camera window shows the glove's acceleration, a green line where each punch started, and a blue
(stopped) or orange (timed out) line where it ended. Hold still for a moment first, so gravity is known.

Change the timeout BEFORE recording training data, or retrain afterwards, because the model learns from
punches cut at this timeout.
"""
import argparse
import threading
import time

import punch_segment
import punch_session as ps

GREEN, BLUE, ORANGE, RED, GREY = (0, 220, 0), (255, 170, 0), (0, 150, 255), (0, 0, 255), (180, 180, 180)
COUNTDOWN_S = 3.0
COMPARE_TIMEOUTS_MS = (250, 300, 350, 400, 450, 500, 600)
KINDS = {"jab": "JAB", "jabs": "JAB", "hook": "HOOK", "hooks": "HOOK", "uppercut": "UPPERCUT", "uppercuts": "UPPERCUT"}


def score_test(imu, pose, t0, t1, settings, classifier=None, kind=None, timeouts_ms=COMPARE_TIMEOUTS_MS):
    """Cut a recorded test at each timeout. Returns [(timeout_ms, found, Counter of labels, unsure)]."""
    from collections import Counter
    rows = []
    for ms in sorted(set(list(timeouts_ms) + [round(1000 * settings.timeout_s)])):
        s = punch_segment.SegmentSettings.from_dict({**settings.to_dict(), "timeout_s": ms / 1000})
        segs = [g for g in punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], s)
                if t0 <= g.start_t <= t1 and g.reason != "gap"]
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
    def __init__(self, sensors, config):
        self.sensors, self.config = sensors, config
        self.marks = []          # (t, colour) for the trace
        self.active = None       # Segment in progress
        self.last = None         # last finished Segment
        self.count = 0
        self.quit = False
        self.message, self.message_t = "", 0.0
        self.test = None         # the timed test in progress, if any
        self.report, self.report_t = [], 0.0
        self.classifier = ps.Classifier() if ps.Classifier.exists() else None

    # --- typed commands ---
    def command(self, line):
        line = line.strip().lstrip("﻿")  # PowerShell can prefix piped text with a byte-order mark
        if not line:
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
                self.start_test(rest)
            elif word in settings and rest:
                settings[word] = float(rest[0])
                self.apply(settings, f"{word} = {float(rest[0]):g}")
            else:
                value = float(word)
                ms = value if value > 10 else value * 1000  # "350" or "0.35"
                settings["timeout_s"] = ms / 1000
                self.apply(settings, f"timeout = {ms:.0f} ms")
        except ValueError:
            self.say(f"did not understand {line!r}; type a timeout in ms, a setting name and value, show, save or q")

    # --- timed test ---
    def start_test(self, words):
        """test [punches] [seconds] [jab|hook|uppercut]"""
        numbers = [float(w) for w in words if w.replace(".", "", 1).isdigit()]
        kind = next((KINDS[w.lower()] for w in words if w.lower() in KINDS), None)
        n = int(numbers[0]) if numbers else 10
        seconds = numbers[1] if len(numbers) > 1 else 60.0
        if seconds > 120:
            raise ValueError("a test can be at most 120 s")
        what = f"{n} {kind + 'S' if kind else 'punches'}"
        self.test = {"n": n, "seconds": seconds, "kind": kind, "what": what, "go_wall": time.time() + COUNTDOWN_S,
                     "t0": None, "seen": 0}
        self.say(f"TEST: throw {what} in {seconds:g} s, starting in {COUNTDOWN_S:g} s. "
                 f"Timeout {1000 * self.sensors.settings.timeout_s:.0f} ms.")

    def finish_test(self, test, t1):
        settings = self.sensors.settings
        imu, pose = self.sensors.window(test["t0"] - 1.0, t1 + 0.7)
        clf = self.classifier
        rows = score_test(imu, pose, test["t0"], t1, settings, clf, test["kind"])
        current = round(1000 * settings.timeout_s)
        lines = [f"TEST RESULT: asked for {test['what']} in {test['seconds']:g} s"]
        if clf is None:
            lines.append("(no trained model yet, so punches are counted but not classified)")
        elif round(1000 * clf.segment.timeout_s) != current:
            lines.append(f"(the model was trained at {1000 * clf.segment.timeout_s:.0f} ms; other timeouts are"
                         " only a rough guide until you retrain)")
        for ms, found, labels, unsure in rows:
            mark = " <- current" if ms == current else ""
            text = f"{ms:4d} ms: found {found}/{test['n']}"
            if clf is not None:
                if test["kind"]:
                    text += f", classified as {test['kind']}: {labels.get(test['kind'], 0)}/{test['n']}"
                others = ", ".join(f"{k} {v}" for k, v in sorted(labels.items()) if k != test["kind"])
                text += (f" | other: {others}" if others else "") + (f" | unsure {unsure}" if unsure else "")
            lines.append(text + mark)
        for line in lines:
            print(line)
        self.report, self.report_t = lines, time.time()

    def update_test(self):
        test = self.test
        if test is None:
            return
        now = time.time()
        if test["t0"] is None and now >= test["go_wall"]:
            test["t0"] = self.sensors.latest_imu_t() or 0.0
            test["end_wall"] = now + test["seconds"]
            self.say("GO!")
        elif test["t0"] is not None and now >= test["end_wall"] + 0.7:  # let the last punch finish
            self.test = None
            t1 = (self.sensors.latest_imu_t() or 0.0) - 0.7
            threading.Thread(target=self.finish_test, args=(test, t1), daemon=True).start()

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
    def on_frame(self):
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "start":
                self.active = value
                self.marks.append((value.start_t, GREEN))
            elif kind == "end":
                self.active, self.last = None, value
                self.count += 1
                self.marks.append((value.end_t, BLUE if value.reason == "decel" else ORANGE))
                if self.test and self.test["t0"] is not None and value.start_t >= self.test["t0"]:
                    self.test["seen"] += 1
                print(f"punch {self.count}: {1000 * value.duration:.0f} ms, ended by "
                      f"{'stopping' if value.reason == 'decel' else value.reason}, peak {value.peak_g:.1f} g")
        self.marks = self.marks[-40:]
        self.update_test()
        return self.quit

    def on_draw(self, frame):
        h, w = frame.shape[:2]
        s = self.sensors.settings
        now_t = self.sensors.latest_imu_t() or 0.0
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
               top + 30, GREY, 0.55)
        if time.time() - self.message_t < 3:
            ps.put(frame, self.message[:75], top + 55, (0, 255, 255), 0.55)
        test, now = self.test, time.time()
        if test and test["t0"] is None:
            ps.put(frame, f"TEST: {test['what']} - get ready {test['go_wall'] - now:.0f}", top + 90, (0, 255, 255), 0.9, thick=3)
        elif test:
            ps.put(frame, f"TEST: {test['what']}  {max(0.0, test['end_wall'] - now):.0f} s left  seen {test['seen']}",
                   top + 90, (0, 255, 255), 0.9, thick=3)
        elif self.report and now - self.report_t < 30:
            for i, line in enumerate(self.report[:9]):
                ps.put(frame, line[:80], top + 85 + 22 * i, (0, 255, 255) if "current" in line else GREY, 0.5, thick=1)
        ps.put(frame, "type a timeout in ms in the terminal, then Enter", h - 40, GREY, 0.5)
        times, values = self.sensors.recent_linear_g(3.0)
        ps.draw_trace(frame, times, values, now_t, 3.0, 12.0, self.marks,
                      origin=(10, h - 150), size=(w - 20, 100), threshold_g=s.start_g)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--simulate", action="store_true", help="use a fake glove (no hardware)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    args = parser.parse_args()

    config = ps.load_config()
    sensors = ps.LiveSensors(config["segment"], seconds=150, simulate=args.simulate)  # room for a 2-minute test
    tuner = Tuner(sensors, config)
    if tuner.classifier:
        print(f"Model loaded (trained {tuner.classifier.info['trained_at']}), so timed tests classify punches.")
    threading.Thread(target=tuner.read_terminal, daemon=True).start()
    print(__doc__.split("Type in")[1].split("Change the")[0].strip())
    started = time.time()

    def on_frame():
        return tuner.on_frame() or bool(args.max_seconds and time.time() - started > args.max_seconds)

    import pose_tracker
    try:
        pose_tracker.main(on_frame=on_frame, on_draw=tuner.on_draw, camera_index=args.camera,
                          **sensors.pose_callbacks())
    finally:
        sensors.close()
    print(f"{tuner.count} punches seen. Settings are only kept if you typed save.")
    return tuner


if __name__ == "__main__":
    main()
