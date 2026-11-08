"""Play with BOTH gloves: each arm has its own glove, its own punch finder and its own trained model,
running side by side, with one camera for both.

  python punch_live_both.py                    dry run: prints the keys it would press, presses nothing
  python punch_live_both.py --live --windowed  really presses keys, with the game visible beside it
  python punch_live_both.py --simulate         fake gloves on both arms (no hardware)

Left arm (port 5005, models/punch_rf.joblib):        JAB = J, HOOK = H, UPPERCUT = U
Right arm (port 5006, models/punch_rf_right.joblib): CROSS = K, HOOK = H, UPPERCUT = U
Defence (camera): BLOCK = W, DUCK = S, SLIP LEFT = A, SLIP RIGHT = D, GUARD DOWN = G. Keys from mappings/controls.json (python controls_editor.py).
If one arm has no trained model yet, the other still plays on its own.

Hotkeys from any window: F8 key output on or off (with --live it starts OFF), F9 quit, [ and ] confidence
cutoff down or up (both arms). Calibrate by pressing C in the camera window (5 s countdown into guard).
F7 also calibrates, but in a browser game F7 opens a dialog, so use C there.

Each arm still trains on its own: python punch_train.py --arm left, then --arm right.

The keybind window (controls_editor.py) opens beside it; saved changes apply straight away. --no-editor skips it.
Tracking mode: hold the LEFT glove's button and your right hand (camera) steers the mouse; punches then use the
tracking set of keys. --no-tracking turns it off.
"""
import argparse
import os
import subprocess
import sys
import time

import punch_live
import punch_session as ps


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    parser.add_argument("--mapping", default=os.path.join(ps.ROOT, "mappings", "controls.json"),
                        help="controls file (edit it with controls_editor.py)")
    parser.add_argument("--simulate", action="store_true", help="fake gloves on both arms (no hardware)")
    parser.add_argument("--right-phone", metavar="ADDRESS", help="use a phone running phyphox (remote access on) as the "
                        "right arm's sensor instead of a glove on port 5006: the address it shows")
    parser.add_argument("--windowed", action="store_true", help="start in a normal window, not full screen")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--confidence", type=float, help="how sure a model must be to press a key, 0 to 1")
    parser.add_argument("--max-seconds", type=float, help="stop by itself (for testing)")
    parser.add_argument("--no-editor", action="store_true", help="don't open the keybind window")
    parser.add_argument("--no-tracking", action="store_true", help="ignore the glove button: no mouse steering")
    args = parser.parse_args()

    import controller
    import game_input
    mapping = game_input.Mapping.load(args.mapping)
    keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
    game = game_input.GameInput(mapping, keyboard)
    game.set_enabled(not args.live)  # a dry run is harmless, so it starts on; live starts off

    arms = []  # (arm, LiveSensors, LivePlay)
    for name, x in (("left", 10), ("right", 330)):
        arm = ps.ARMS[name]
        if not ps.Classifier.exists(arm.model_path):
            print(f"{name} arm: no model at {os.path.relpath(arm.model_path)} yet, so it is off. "
                  f"Record with punch_capture.py --arm {name}, then punch_train.py --arm {name}.")
            continue
        clf = ps.Classifier(arm.model_path, args.confidence)
        phone = args.right_phone if name == "right" else None
        sensors = ps.LiveSensors(clf.segment, simulate=args.simulate, port=arm.port, arm=arm, phone=phone)
        play = punch_live.LivePlay(sensors, clf, game, name=f"{name.upper()}: ", x=x, scale=0.65, footer=not arms)
        arms.append((arm, sensors, play))
        print(f"{name} arm: model trained {clf.info['trained_at']} on {clf.info['counts']}, "
              f"timeout {1000 * clf.segment.timeout_s:.0f} ms, "
              + (f"phone at {phone}" if phone else f"glove on port {arm.port}"))
    if not arms:
        return 1

    tracking = None
    left = next((s for a, s, _ in arms if a.name == "left"), None)
    if left is not None and not args.no_tracking:  # the button is on the left glove
        import hand_aim
        tracking = hand_aim.Tracking(left, game)
    watcher = punch_live.ControlsWatcher(args.mapping)
    editor = None
    if not args.no_editor:
        editor = subprocess.Popen([sys.executable, os.path.join(ps.ROOT, "controls_editor.py"), "--mapping", args.mapping])

    # Defence keys, calibration and F7/F8/F9 come from controller.py; punches come from the two models.
    callbacks = controller.make_callbacks(game, controller.Hotkeys(), detector=False)
    callbacks.pop("on_punch")
    hotkey_frame = callbacks["on_frame"]
    sensitivity_keys = controller.Hotkeys()
    screen_keys = ps.ToolKeys(fullscreen=not args.windowed)
    started = time.time()

    def on_frame():
        if tracking:
            tracking.poll()  # before the punches, so one thrown with the button held uses the tracking set
        for _, _, play in arms:
            play.poll()
        changed = watcher.poll()
        if changed:
            game.set_mapping(changed)
            if tracking:
                tracking.set_settings(changed.aim)
            print("controls reloaded from " + os.path.basename(args.mapping))
        for vk, step in ((punch_live.VK_LOWER, -0.05), (punch_live.VK_RAISE, +0.05)):
            if sensitivity_keys.pressed(vk):
                for _, _, play in arms:
                    play.adjust_confidence(step)
        screen_keys.poll_fullscreen()
        return hotkey_frame() or bool(args.max_seconds and time.time() - started > args.max_seconds)

    def on_draw(frame):
        for _, _, play in arms:
            play.on_draw(frame)
        if tracking:
            tracking.draw(frame, ps.put)

    callbacks.update(ps.shared_pose_callbacks(*[s for _, s, _ in arms]))  # one camera feeds both arms
    callbacks.update(on_frame=on_frame, on_draw=on_draw, camera_index=args.camera, fullscreen=not args.windowed,
                     state_keys=game.key_for_state)

    print(f"Keys: {mapping.taps}")
    print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed.")
    print(f"Key output is {'ON' if game.enabled else 'OFF'}. F8 toggles output, F9 quits, C (camera window) calibrates.")
    import pose_tracker
    try:
        pose_tracker.main(**callbacks)
    finally:
        if tracking:
            tracking.close()
        game.close()
        for _, sensors, _ in arms:
            sensors.close()
        if editor and editor.poll() is None:
            editor.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
