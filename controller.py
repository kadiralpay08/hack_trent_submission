"""Play a game with your body: camera punches and poses become key presses.

  python controller.py          dry run: prints the keys it would press, presses nothing
  python controller.py --live   really presses keys in whichever window has focus

Hotkeys work from any window, so you can click the game and still control this:
  F7  calibrate or recalibrate (stand in your guard first; you hear a beep when it worked)
  F8  turn key output on or off (with --live it starts OFF, so nothing is typed by accident)
  F9  quit
In the camera window: c = calibrate, a/d = rotate the 3D view, q = quit.
"""
import argparse
import ctypes
import os
import sys

import game_input
import punch_detector

VK_F7, VK_F8, VK_F9 = 0x76, 0x77, 0x78


def beep():
    """A short system sound, so you know something happened while you are looking at the game."""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBeep(0)


def key_is_down(vk):
    """True while a key is held, whichever window has focus (Windows only)."""
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Hotkeys:
    """Reports a key once when it goes down, not every frame while it is held."""

    def __init__(self, read=key_is_down):
        self.read, self.was_down = read, {}

    def pressed(self, vk):
        down = self.read(vk)
        fired = down and not self.was_down.get(vk, False)
        self.was_down[vk] = down
        return fired


def make_callbacks(game, hotkeys, log=print, glove=None, sound=beep, detector=None):
    """Build the callbacks for pose_tracker.main(), as a dict of keyword arguments.

    Kept separate from main() so it can be tested without a camera.
    detector is a punch_detector.PunchDetector; pass False to use pose_tracker's own punch check.

    glove is an optional glove_punch.GlovePunches. When given, a camera punch only counts if that
    hand's glove also felt one. A hand with no glove connected is never blocked.
    """
    last_states = []

    def on_punch(kind, side, speed):
        if glove is not None:
            glove.poll()
            if not glove.confirms(side):
                log(f"{side} {kind} ignored (the {side} glove felt no punch)")
                return
        if not game.enabled:
            log(f"{side} {kind} (output OFF, press F8)")
        elif game.punch(kind):
            log(f"{side} {kind} -> {game.mapping.taps[kind.upper()]}")
        else:
            log(f"{side} {kind} (no key mapped, or key already down)")

    def on_states(states):
        nonlocal last_states
        game.set_states(states)
        if list(states) != last_states and game.enabled:
            log("state: " + (", ".join(states) or "none"))
        last_states = list(states)

    def on_frame():
        if glove is not None:
            glove.poll()
        if hotkeys.pressed(VK_F8):
            game.set_enabled(not game.enabled)
            log("key output " + ("ON" if game.enabled else "OFF"))
        return hotkeys.pressed(VK_F9)

    def want_calibrate():
        return hotkeys.pressed(VK_F7)

    def on_calibrated():
        log("calibrated")
        sound()

    callbacks = dict(on_punch=on_punch, on_states=on_states, on_frame=on_frame,
                     want_calibrate=want_calibrate, on_calibrated=on_calibrated)
    if detector is not False:
        detector = detector or punch_detector.PunchDetector()
        callbacks["on_features"] = detector.update
        callbacks["builtin_punches"] = False
    return callbacks


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mapping", default=os.path.join(os.path.dirname(__file__), "mappings", "browser_boxing.json"))
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    parser.add_argument("--glove", action="store_true",
                        help="listen for the glove on UDP port 5005 and only accept punches the glove also felt")
    parser.add_argument("--builtin-punches", action="store_true",
                        help="use pose_tracker's original punch check instead of punch_detector.py")
    parser.add_argument("--simulate-glove", action="store_true",
                        help="like --glove, but with a fake glove that punches every 1.5 s (for testing)")
    args = parser.parse_args()

    mapping = game_input.Mapping.load(args.mapping)
    keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
    game = game_input.GameInput(mapping, keyboard)
    game.set_enabled(not args.live)  # a dry run is harmless, so it starts on; live starts off

    print(f"Game: {mapping.game}")
    print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed.")
    print(f"Key output is {'ON' if game.enabled else 'OFF'}. F7 calibrates, F8 toggles output, F9 quits.")
    print("Stand in your guard in front of the camera and press F7 to calibrate (any time, from any window).")
    if not args.builtin_punches:
        print("Camera punches: HOOK and UPPERCUT are detected. Straight punches (jab, cross) are not: a webcam")
        print("facing you cannot see them. Use a glove for those, or press the jab/cross keys by hand.")

    glove = None
    if args.glove or args.simulate_glove:
        import threading
        import glove_punch
        import glove_receiver
        receiver = glove_receiver.GloveReceiver()
        if args.simulate_glove:
            threading.Thread(target=glove_receiver.simulate, daemon=True).start()
        glove = glove_punch.GlovePunches(receiver)
        print("Glove: listening on UDP port 5005. Camera punches need a matching glove punch.")

    import pose_tracker  # imported here so the rest of this file works without OpenCV or MediaPipe
    callbacks = make_callbacks(game, Hotkeys(), glove=glove, detector=False if args.builtin_punches else None)
    try:
        pose_tracker.main(**callbacks)
    finally:
        game.close()  # lets go of every key


if __name__ == "__main__":
    main()
