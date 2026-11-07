"""Play a boxing game: the trained model classifies each left-hand punch and presses its key.

  python punch_live.py                dry run: prints the keys it would press, presses nothing
  python punch_live.py --live         really presses keys in whichever window has focus
  python punch_live.py --simulate     fake glove (no hardware), to try the tool

Punches (glove and camera together, via the model from punch_train.py): JAB = J, HOOK = H, UPPERCUT = U.
Defence (camera only, as in controller.py): BLOCK = W, DUCK = S, SLIP LEFT = A, SLIP RIGHT = D.
Keys come from mappings/browser_boxing.json. A punch the model is unsure about presses nothing.

Hotkeys work from any window: F7 calibrate (5 s countdown to get into guard; needed for duck and slip), F8 key output
on or off (with --live it starts OFF), F9 quit.

The key is pressed when the punch ENDS (it stops, or the timeout runs out), because the model needs
the whole punch. A shorter timeout in punch_tune.py means less delay, then retrain.
"""
import argparse
import os
import time

import punch_session as ps

GREEN, ORANGE, GREY, YELLOW = (0, 220, 0), (0, 150, 255), (190, 190, 190), (0, 255, 255)


class LivePlay:
    def __init__(self, sensors, classifier, game, log=print):
        self.sensors, self.clf, self.game, self.log = sensors, classifier, game, log
        self.waiting = []        # finished punches waiting for their last camera frame
        self.shown, self.shown_t = "", 0.0
        self.count = 0

    def poll(self):
        while not self.sensors.events.empty():
            kind, value = self.sensors.events.get()
            if kind == "end" and value.reason != "gap":
                self.waiting.append((value, time.time()))
        ready = []
        latest_pose = self.sensors.latest_pose_t() or 0.0
        for seg, seen_at in self.waiting:
            # wait for a camera frame from after the punch, or give up on the camera after 0.1 s
            if latest_pose + self.clf.camera_lag_s >= seg.end_t or time.time() - seen_at > 0.1:
                ready.append((seg, seen_at))
        for item in ready:
            self.waiting.remove(item)
            self.decide(*item)

    def decide(self, seg, seen_at):
        pre = self.clf.segment.pre_s
        imu, pose = self.sensors.window(seg.start_t - pre - 0.05, seg.end_t + 0.01)
        label, conf = self.clf.classify(imu, pose, seg)
        self.count += 1
        delay = 1000 * (time.time() - seen_at)
        timing = f"{1000 * seg.duration:.0f} ms punch ({'stopped' if seg.reason == 'decel' else 'timeout'}), +{delay:.0f} ms to decide"
        if label is None or conf < self.clf.min_confidence:
            self.show(f"unsure ({label} {100 * conf:.0f}%): no key", ORANGE)
            self.log(f"punch {self.count}: unsure, best guess {label} {100 * conf:.0f}% | {timing}")
            return
        if not self.game.enabled:
            note = "output OFF, press F8"
        elif self.game.punch(label):
            note = f"key {self.game.mapping.taps[label]}"
        else:
            note = "no key mapped, or key already down"
        self.show(f"{label} {100 * conf:.0f}%  ({note})", GREEN)
        self.log(f"punch {self.count}: {label} {100 * conf:.0f}% -> {note} | {timing}")

    def show(self, text, colour):
        self.shown, self.shown_colour, self.shown_t = text, colour, time.time()

    def on_draw(self, frame):
        top = 150
        if not self.sensors.glove_ok():
            ps.put(frame, "NO GLOVE DATA", top, (0, 0, 255), 0.8)
        elif not self.sensors.segmenter.ready:
            ps.put(frame, "hold the glove still for a moment...", top, ORANGE, 0.7)
        if time.time() - self.shown_t < 1.5:
            ps.put(frame, self.shown, top + 40, self.shown_colour, 1.0, thick=3)
        ps.put(frame, f"key output {'ON' if self.game.enabled else 'OFF (F8)'}  |  F7 calibrate  F9 quit",
               frame.shape[0] - 40, YELLOW if self.game.enabled else GREY, 0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    parser.add_argument("--model", default=ps.MODEL_PATH)
    parser.add_argument("--mapping", default=os.path.join(ps.ROOT, "mappings", "browser_boxing.json"))
    parser.add_argument("--simulate", action="store_true", help="fake glove (no hardware)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    args = parser.parse_args()

    if not ps.Classifier.exists(args.model):
        print(f"No model at {args.model}. Record with punch_capture.py, then run punch_train.py.")
        return 1
    clf = ps.Classifier(args.model)
    current = ps.load_config()["segment"]
    if current.to_dict() != clf.segment.to_dict():
        print("Note: punch_config.json differs from the settings the model was trained with. Using the model's"
              " settings; retrain to use the new ones.")

    import controller
    import game_input
    mapping = game_input.Mapping.load(args.mapping)
    keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
    game = game_input.GameInput(mapping, keyboard)
    game.set_enabled(not args.live)  # a dry run is harmless, so it starts on; live starts off

    sensors = ps.LiveSensors(clf.segment, simulate=args.simulate)
    play = LivePlay(sensors, clf, game)

    # Defence keys, calibration and the F7/F8/F9 hotkeys come from controller.py; punches come from the model.
    callbacks = controller.make_callbacks(game, controller.Hotkeys(), detector=False)
    callbacks.pop("on_punch")
    hotkey_frame = callbacks["on_frame"]
    started = time.time()

    def on_frame():
        play.poll()
        return hotkey_frame() or bool(args.max_seconds and time.time() - started > args.max_seconds)

    callbacks.update(sensors.pose_callbacks())
    callbacks.update(on_frame=on_frame, on_draw=play.on_draw, camera_index=args.camera)

    print(f"Model trained {clf.info['trained_at']} on {clf.info['counts']}, "
          f"cross-validated accuracy {100 * clf.info['cv_accuracy']:.0f}%.")
    print(f"Timeout {1000 * clf.segment.timeout_s:.0f} ms. Keys: {mapping.taps}")
    print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed.")
    print(f"Key output is {'ON' if game.enabled else 'OFF'}. F7 calibrates, F8 toggles output, F9 quits.")
    import pose_tracker
    try:
        pose_tracker.main(**callbacks)
    finally:
        game.close()
        sensors.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
