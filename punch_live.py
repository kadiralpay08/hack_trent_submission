"""Play a boxing game: the trained model classifies each left-hand punch and presses its key.

  python punch_live.py                dry run: prints the keys it would press, presses nothing
  python punch_live.py --live         really presses keys in whichever window has focus
  python punch_live.py --simulate     fake glove (no hardware), to try the tool

Punches (glove and camera together, via the model from punch_train.py): JAB = J, HOOK = H, UPPERCUT = U.
Defence (camera only, as in controller.py): BLOCK = W, DUCK = S, SLIP LEFT = A, SLIP RIGHT = D.
Keys come from mappings/browser_boxing.json. Movement the model calls NONE (not a punch), and any punch
it is unsure about, presses nothing.

Hotkeys work from any window: F7 calibrate (5 s countdown to get into guard; needed for duck and slip), F8 key output
on or off (with --live it starts OFF), F9 quit.

Sensitivity, with no retraining:
  [ / ]             confidence cutoff down / up by 5 points while playing (shown on screen). Lower = presses
                    more keys, including more wrong ones. Higher = fewer wrong keys, more missed punches.
  --confidence 0.7  start at this cutoff (otherwise min_confidence in punch_config.json, default 0.6)
  --start-g 3       how hard a movement must be to count as a punch at all (the model was trained at
                    its own value, usually 2.5 g). Small changes are fine; for big ones, retrain.

The key is pressed when the punch ENDS (it stops, or the timeout runs out), because the model needs
the whole punch. A shorter timeout in punch_tune.py means less delay, then retrain.
"""
import argparse
import os
import time

import punch_segment
import punch_session as ps

# [ and ] change the confidence cutoff. Not F5/F6: these keys also reach the focused window, and in a
# browser game F5 reloads the page and F6 jumps to the address bar. Browsers and the games ignore [ ].
VK_LOWER, VK_RAISE = 0xDB, 0xDD  # [ and ]

GREEN, ORANGE, GREY, YELLOW = (0, 220, 0), (0, 150, 255), (190, 190, 190), (0, 255, 255)


class LivePlay:
    """One arm: its glove, its model, and the keys its punches press. punch_live_both.py runs two."""

    def __init__(self, sensors, classifier, game, log=print, name="", x=10, scale=1.0, footer=True):
        self.sensors, self.clf, self.game, self.log = sensors, classifier, game, log
        self.name, self.x, self.scale, self.footer = name, x, scale, footer  # where this arm draws its text
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
        if label == "NONE":
            self.show(f"not a punch ({100 * conf:.0f}%): no key", GREY)
            self.log(f"{self.name}movement {self.count}: not a punch {100 * conf:.0f}% | {timing}")
            return
        if label is None or conf < self.clf.min_confidence:
            self.show(f"unsure ({label} {100 * conf:.0f}%): no key", ORANGE)
            self.log(f"{self.name}punch {self.count}: unsure, best guess {label} {100 * conf:.0f}% | {timing}")
            return
        if not self.game.enabled:
            note = "output OFF, press F8"
        elif self.game.punch(label):
            note = f"key {self.game.mapping.taps[label]}"
        else:
            note = "no key mapped, or key already down"
        self.show(f"{label} {100 * conf:.0f}%  ({note})", GREEN)
        self.log(f"{self.name}punch {self.count}: {label} {100 * conf:.0f}% -> {note} | {timing}")

    def show(self, text, colour):
        self.shown, self.shown_colour, self.shown_t = text, colour, time.time()

    def on_draw(self, frame):
        top, x, s = 150, self.x, self.scale
        if self.name:
            ps.put(frame, self.name.strip(" :"), top - 30, YELLOW, 0.6 * s + 0.1, x=x)
        if not self.sensors.glove_ok():
            ps.put(frame, "NO GLOVE DATA", top, (0, 0, 255), 0.8 * s, x=x)
        elif not self.sensors.segmenter.ready:
            ps.put(frame, "hold the glove still...", top, ORANGE, 0.7 * s, x=x)
        if time.time() - self.shown_t < 1.5:
            ps.put(frame, self.shown, top + 40, self.shown_colour, 1.0 * s, thick=3 if s >= 1 else 2, x=x)
        if not self.footer:
            return
        ps.put(frame, f"key output {'ON' if self.game.enabled else 'OFF (F8)'}  |  F7 calibrate  F9 quit  F11 full screen",
               frame.shape[0] - ps.TEXT_BOTTOM, YELLOW if self.game.enabled else GREY, 0.5)
        ps.put(frame, f"confidence cutoff {100 * self.clf.min_confidence:.0f}%  ([ looser, ] stricter)"
               f"  |  start {self.sensors.settings.start_g:g} g", frame.shape[0] - ps.TEXT_BOTTOM - 25, GREY, 0.5)

    def adjust_confidence(self, step):
        self.clf.min_confidence = round(min(0.95, max(0.35, self.clf.min_confidence + step)), 2)
        self.show(f"confidence cutoff {100 * self.clf.min_confidence:.0f}%", YELLOW)
        self.log(f"confidence cutoff now {100 * self.clf.min_confidence:.0f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    ps.add_arm_argument(parser)
    parser.add_argument("--model", help="trained model (default: the arm's model file)")
    parser.add_argument("--mapping", default=os.path.join(ps.ROOT, "mappings", "browser_boxing.json"))
    parser.add_argument("--simulate", action="store_true", help="fake glove (no hardware)")
    parser.add_argument("--windowed", action="store_true", help="start in a normal window, not full screen")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", type=int, help="UDP port the glove sends to (default: the arm's, 5005 or 5006)")
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    parser.add_argument("--confidence", type=float, help="how sure the model must be to press a key, 0 to 1 "
                        "(default: min_confidence in punch_config.json)")
    parser.add_argument("--start-g", type=float, help="how hard a movement must be to count as a punch, in g "
                        "(default: what the model was trained with)")
    args = parser.parse_args()
    arm = ps.ARMS[args.arm]
    args.model, args.port = args.model or arm.model_path, args.port or arm.port

    if not ps.Classifier.exists(args.model):
        print(f"No model at {args.model}. Record with punch_capture.py --arm {arm.name}, then run punch_train.py --arm {arm.name}.")
        return 1
    clf = ps.Classifier(args.model, args.confidence)
    current = ps.load_config()["segment"]
    if current.to_dict() != clf.segment.to_dict():
        print("Note: punch_config.json differs from the settings the model was trained with. Using the model's"
              " settings; retrain to use the new ones.")
    if args.start_g is not None:
        trained = clf.segment.start_g
        clf.segment = punch_segment.SegmentSettings.from_dict({**clf.segment.to_dict(), "start_g": args.start_g})
        if abs(args.start_g - trained) > 1.0:
            print(f"Warning: start {args.start_g:g} g is far from the {trained:g} g the model was trained with, so it "
                  "may be less accurate. Retrain with this value for best results.")

    import controller
    import game_input
    mapping = game_input.Mapping.load(args.mapping)
    keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
    game = game_input.GameInput(mapping, keyboard)
    game.set_enabled(not args.live)  # a dry run is harmless, so it starts on; live starts off

    sensors = ps.LiveSensors(clf.segment, simulate=args.simulate, port=args.port, arm=arm)
    play = LivePlay(sensors, clf, game)

    # Defence keys, calibration and the F7/F8/F9 hotkeys come from controller.py; punches come from the model.
    callbacks = controller.make_callbacks(game, controller.Hotkeys(), detector=False)
    callbacks.pop("on_punch")
    hotkey_frame = callbacks["on_frame"]
    sensitivity_keys = controller.Hotkeys()
    screen_keys = ps.ToolKeys(fullscreen=not args.windowed)
    started_confidence, started = clf.min_confidence, time.time()

    def on_frame():
        play.poll()
        screen_keys.poll_fullscreen()
        if sensitivity_keys.pressed(VK_LOWER):
            play.adjust_confidence(-0.05)
        if sensitivity_keys.pressed(VK_RAISE):
            play.adjust_confidence(+0.05)
        return hotkey_frame() or bool(args.max_seconds and time.time() - started > args.max_seconds)

    callbacks.update(sensors.pose_callbacks())
    callbacks.update(on_frame=on_frame, on_draw=play.on_draw, camera_index=args.camera, fullscreen=not args.windowed)

    print(f"Model trained {clf.info['trained_at']} on {clf.info['counts']}, "
          f"cross-validated accuracy {100 * clf.info['cv_accuracy']:.0f}%.")
    print(f"Timeout {1000 * clf.segment.timeout_s:.0f} ms, start {clf.segment.start_g:g} g, "
          f"confidence cutoff {100 * clf.min_confidence:.0f}% ([ and ] to change). Keys: {mapping.taps}")
    print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed.")
    print(f"Key output is {'ON' if game.enabled else 'OFF'}. F7 calibrates, F8 toggles output, F9 quits.")
    import pose_tracker
    try:
        pose_tracker.main(**callbacks)
    finally:
        game.close()
        sensors.close()
    if clf.min_confidence != started_confidence:
        print(f"Confidence cutoff ended at {100 * clf.min_confidence:.0f}%. To start there next time, use "
              f"--confidence {clf.min_confidence:g}, or set min_confidence to {clf.min_confidence:g} in punch_config.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
