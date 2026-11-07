"""What every product variant provides, and the standard way to run one.

A variant says three things: what it is and needs (name, title, summary, needs), what punch-detection
settings suit it (settings()), and how to run it (run()). The standard run() is the single-camera
pipeline: webcam, pose tracking, the burst-based punch detector, and key output. A variant that needs
something different (two cameras, a glove) overrides run() and leaves the others untouched.

Everything a variant owns lives in its own folder: settings.json, notes, and (on your machine only)
recorded tuning sessions in tuning_data/<name>/.
"""
import json
import os

import punch_detector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAPPING = os.path.join(ROOT, "mappings", "browser_boxing.json")


class Variant:
    name = ""      # used on the command line, and as the folder name under tuning_data/
    title = ""     # a short human name
    summary = ""   # one or two sentences: what makes this variant different
    needs = ""     # hardware and setup, in one line

    # --- where this variant keeps its files ---
    @property
    def folder(self):
        import sys
        return os.path.dirname(os.path.abspath(sys.modules[type(self).__module__].__file__))

    @property
    def data_dir(self):
        return os.path.join(ROOT, "tuning_data", self.name)

    # --- punch detection settings ---
    def settings(self):
        """Detector settings for this variant, from its settings.json (defaults if there is none)."""
        path = os.path.join(self.folder, "settings.json")
        if not os.path.exists(path):
            return punch_detector.Settings()
        with open(path, encoding="utf-8") as f:
            return punch_detector.Settings.from_dict(json.load(f))

    # --- hooks a variant can use to add its own behaviour to the camera loop ---
    def extra_callbacks(self, args, text_y=95):
        """Extra keyword arguments for pose_tracker.main (on_pose, on_draw, ...). Default: none.

        text_y is the screen row where this variant may draw its own text, so it stays clear of
        whatever else is on the camera window (the tuning recorder puts a banner at the top).
        """
        return {}

    tuning_short = "Hands up in guard. Head and both hands in view."  # shown on the recording screen (60 characters)

    def tuning_prompt(self):
        """Longer instructions, printed in the console before a tuning session."""
        return "Stand where you will play, hands up in guard, head and both hands in view."

    def session_note(self):
        """A line describing the conditions of the session just recorded, saved beside it. Default: none."""
        return ""

    def add_arguments(self, parser):
        """Add options that only this variant has."""

    # --- running ---
    def run(self, args):
        """The standard single-camera pipeline. Returns when the boxer presses F9 or the time is up."""
        import time

        import controller
        import game_input
        import pose_tracker

        mapping = game_input.Mapping.load(args.mapping)
        keyboard = game_input.SendInputKeyboard() if args.live else game_input.DryRunKeyboard()
        game = game_input.GameInput(mapping, keyboard)
        game.set_enabled(not args.live)  # a dry run is harmless, so it starts on; live starts off

        detector = punch_detector.PunchDetector(self.settings())
        callbacks = controller.make_callbacks(game, controller.Hotkeys(), detector=detector)
        callbacks.update(self.extra_callbacks(args))
        callbacks["camera_index"] = args.camera

        if args.max_seconds:  # lets a test stop the run by itself
            started, hotkey_quit = time.time(), callbacks["on_frame"]
            callbacks["on_frame"] = lambda: hotkey_quit() or time.time() - started > args.max_seconds

        print(f"Variant: {self.title}  ({self.name})")
        print(f"Game: {mapping.game}")
        print("LIVE: keys go to the focused window." if args.live else "Dry run: nothing is pressed.")
        print(f"Key output is {'ON' if game.enabled else 'OFF'}. F7 calibrates, F8 toggles output, F9 quits.")
        print(self.tuning_prompt())
        try:
            pose_tracker.main(**callbacks)
        finally:
            game.close()  # lets go of every key
