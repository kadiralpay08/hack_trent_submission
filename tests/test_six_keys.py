"""Six punches, six different keys: the left arm's JAB, HOOK, UPPERCUT and the right arm's CROSS, HOOK, UPPERCUT.

Before this, both arms' HOOK and UPPERCUT looked up the same name in the controls file and pressed the same key.

Run with: python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import controls_editor  # noqa: E402
import game_input as gi  # noqa: E402
import punch_live  # noqa: E402
import punch_session as ps  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIX = ("JAB", "HOOK", "UPPERCUT", "CROSS", "RIGHT HOOK", "RIGHT UPPERCUT")


class Keys(gi.Keyboard):
    def __init__(self):
        super().__init__()
        self.pressed = []

    def _send(self, key, is_down):
        if is_down:
            self.pressed.append(key)

    def _move(self, dx, dy):
        pass


def play_of(arm_name, mapping):
    """A LivePlay for one arm over a real GameInput; the sensors and model are not needed to choose a key."""
    game = gi.GameInput(mapping, Keys())
    return punch_live.LivePlay(None, None, game, arm=ps.ARMS[arm_name]), game


class SixDistinctKeys(unittest.TestCase):
    def check(self, mapping):
        keys = [mapping.taps[name] for name in SIX]  # a KeyError means a punch has no key
        self.assertEqual(len(set(keys)), 6, f"two punches share a key: {dict(zip(SIX, keys))}")
        self.assertFalse(set(keys) & set(mapping.holds.values()), "a punch key is also a defence key")

    def test_the_shipped_controls_file(self):
        self.check(gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json")))

    def test_the_editor_defaults(self):
        self.check(gi.Mapping(controls_editor.DEFAULTS))

    def test_the_editor_lists_all_six(self):
        self.assertEqual([p for p in gi.PUNCHES if p in SIX], list(SIX))
        for name in SIX:
            self.assertIn(name, controls_editor.LABELS)

    def test_no_clashes_are_reported_for_the_defaults(self):
        self.assertEqual(controls_editor.problems(gi.Mapping(controls_editor.DEFAULTS)), [])


class ArmActions(unittest.TestCase):
    def test_names(self):
        left, right = ps.ARMS["left"], ps.ARMS["right"]
        self.assertEqual([left.action(x) for x in left.labels], ["JAB", "HOOK", "UPPERCUT"])
        self.assertEqual([right.action(x) for x in right.labels], ["CROSS", "RIGHT HOOK", "RIGHT UPPERCUT"])

    def test_every_punch_of_both_arms_is_one_of_the_six(self):
        names = [arm.action(label) for arm in ps.ARMS.values() for label in arm.labels]
        self.assertEqual(sorted(names), sorted(SIX))


class PlayPressesTheRightKey(unittest.TestCase):
    def setUp(self):
        self.mapping = gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json"))

    def press(self, arm, label):
        play, game = play_of(arm, self.mapping)
        self.assertTrue(game.punch(play.action(label)))
        return game.keyboard.pressed[-1]

    def test_each_punch_presses_its_own_key(self):
        got = {(arm, label): self.press(arm, label) for arm in ps.ARMS for label in ps.ARMS[arm].labels}
        self.assertEqual(len(set(got.values())), 6, got)
        self.assertEqual(got[("left", "HOOK")], "k")
        self.assertEqual(got[("right", "HOOK")], "i")
        self.assertEqual(got[("left", "UPPERCUT")], "l")
        self.assertEqual(got[("right", "UPPERCUT")], "o")

    def test_a_controls_file_without_the_new_names_still_works(self):
        # Browser Boxing's file has no RIGHT HOOK or RIGHT UPPERCUT: the right arm falls back to HOOK and UPPERCUT
        self.mapping = gi.Mapping.load(os.path.join(ROOT, "mappings", "browser_boxing.json"))
        play, _ = play_of("right", self.mapping)
        self.assertEqual(play.action("HOOK"), "HOOK")
        self.assertEqual(self.press("right", "HOOK"), "h")
        self.assertEqual(self.press("right", "CROSS"), "k")

    def test_a_play_without_an_arm_uses_the_plain_name(self):
        game = gi.GameInput(self.mapping, Keys())
        self.assertEqual(punch_live.LivePlay(None, None, game).action("HOOK"), "HOOK")


if __name__ == "__main__":
    unittest.main()
