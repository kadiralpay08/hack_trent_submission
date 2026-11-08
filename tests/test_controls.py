"""Tracking mode (glove button + right-hand steering), the three-set controls file and its editor.

Run with: python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import controls_editor  # noqa: E402
import game_input as gi  # noqa: E402
import hand_aim  # noqa: E402
import punch_live  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RecordingKeyboard(gi.Keyboard):
    def __init__(self):
        super().__init__()
        self.log, self.moved = [], np.zeros(2)

    def _send(self, key, is_down):
        self.log.append((key, is_down, time.time()))

    def _move(self, dx, dy):
        self.moved += (dx, dy)


def game(data=None):
    mapping = gi.Mapping(data or gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json")).to_dict())
    return gi.GameInput(mapping, RecordingKeyboard())


def body(wrist_right=0.0, wrist_up=0.0):
    """MediaPipe-style world landmarks for a boxer facing the camera (left shoulder at +x, y down),
    with the right wrist moved by (right, up) shoulder widths from the right shoulder."""
    world = np.zeros((33, 3))
    world[11], world[12] = [0.2, -0.5, 0.0], [-0.2, -0.5, 0.0]   # shoulder width 0.4 m
    world[16] = world[12] + 0.4 * np.array([-wrist_right, -wrist_up, 0.0])  # boxer's right = -x, up = -y
    return world, np.ones(33)


class ControlsFile(unittest.TestCase):
    def test_old_format_still_loads(self):
        m = gi.Mapping.load(os.path.join(ROOT, "mappings", "browser_boxing.json"))
        self.assertEqual(m.taps["JAB"], "j")
        self.assertEqual(m.holds["BLOCK"], "w")
        self.assertEqual(m.tracking_taps, {})

    def test_default_controls_have_three_sets(self):
        m = gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json"))
        self.assertEqual(m.tracking_taps, {"JAB": "mouse_left", "HOOK": "mouse_right", "UPPERCUT": "mouse_middle"})
        self.assertAlmostEqual(m.tracking_s["JAB"], 0.15)
        self.assertEqual(m.aim["mode"], "speed")
        self.assertFalse(m.aim["block_while_tracking"])

    def test_save_and_load_round_trip_and_unbound(self):
        m = gi.Mapping({"normal": {"JAB": "j", "HOOK": ""}, "tracking": {"JAB": {"bind": "mouse_left", "hold_ms": 200}}})
        self.assertNotIn("HOOK", m.taps)  # an empty bind is unbound
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            m.save(path)
            back = gi.Mapping.load(path)
        self.assertEqual(back.tracking_taps["JAB"], "mouse_left")
        self.assertAlmostEqual(back.tracking_s["JAB"], 0.2)

    def test_mouse_buttons_are_bindable_and_unknown_keys_are_not(self):
        self.assertEqual(gi.check_key("Mouse_Left"), "mouse_left")
        self.assertEqual(gi.check_key("f12"), "f12")
        with self.assertRaises(ValueError):
            gi.check_key("mouse_x9")


class TrackingSet(unittest.TestCase):
    def test_punch_uses_normal_or_tracking_set(self):
        g = game()
        g.punch("JAB")
        g.set_tracking(True)
        g.punch("UPPERCUT")
        time.sleep(0.3)
        downs = [k for k, d, _ in g.keyboard.log if d]
        self.assertEqual(downs, ["j", "mouse_middle"])

    def test_tracking_click_is_held_for_its_hold_time(self):
        g = game()
        g.set_tracking(True)
        g.punch("JAB")
        time.sleep(0.3)
        (_, _, t_down), (_, _, t_up) = [e for e in g.keyboard.log if e[0] == "mouse_left"]
        self.assertGreater(t_up - t_down, 0.12)  # 150 ms by default

    def test_block_is_off_while_tracking_but_duck_and_slip_work(self):
        g = game()
        g.set_states({"BLOCK"})
        self.assertIn("w", g.keyboard.down)
        g.set_tracking(True)              # steering starts: the held block lets go at once
        self.assertNotIn("w", g.keyboard.down)
        g.set_states({"BLOCK", "DUCK", "SLIP LEFT"})
        self.assertEqual(g.keyboard.down, {"s", "a"})
        g.set_tracking(False)             # back to normal: the block that is still showing comes back
        self.assertIn("w", g.keyboard.down)

    def test_block_while_tracking_option(self):
        data = gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json")).to_dict()
        data["aim"]["block_while_tracking"] = True
        g = game(data)
        g.set_tracking(True)
        g.set_states({"BLOCK"})
        self.assertIn("w", g.keyboard.down)

    def test_mouse_only_moves_with_output_on_and_new_controls_release_keys(self):
        g = game()
        g.set_enabled(False)
        g.move(10, 5)
        self.assertEqual(list(g.keyboard.moved), [0, 0])
        g.set_enabled(True)
        g.move(10, 5)
        self.assertEqual(list(g.keyboard.moved), [10, 5])
        g.set_states({"DUCK"})
        data = g.mapping.to_dict()
        data["defence"]["DUCK"] = "c"
        g.set_mapping(gi.Mapping(data))
        self.assertEqual(g.keyboard.down, {"c"})  # the old duck key let go, the new one held


class Steering(unittest.TestCase):
    def test_hand_offset_is_in_the_boxers_frame(self):
        right, up = hand_aim.hand_offset(*body(wrist_right=0.3, wrist_up=0.2))
        self.assertAlmostEqual(right, 0.3, places=6)
        self.assertAlmostEqual(up, 0.2, places=6)
        world, vis = body()
        vis[16] = 0.1
        self.assertIsNone(hand_aim.hand_offset(world, vis))  # wrist not seen

    def test_speed_mode_dead_zone_direction_and_invert(self):
        aim = hand_aim.Aim(gi.AIM_DEFAULTS)
        aim.start(np.zeros(2))
        aim.update(np.array([0.03, 0.0]))      # inside the dead zone
        self.assertEqual(aim.take(1.0), (0, 0))
        aim.update(np.array([0.06 + 0.5, 0.0]))  # full stick to the right
        dx, dy = aim.take(1.0)
        self.assertAlmostEqual(dx, 1200, delta=2)
        self.assertEqual(dy, 0)
        aim.update(np.array([0.0, 0.3]))       # hand up -> mouse up (screen y is down)
        self.assertLess(aim.take(0.5)[1], 0)
        aim.settings["invert_y"] = True
        aim.update(np.array([0.0, 0.3]))
        self.assertGreater(aim.take(0.5)[1], 0)

    def test_speed_mode_keeps_fractions_so_slow_turns_still_move(self):
        aim = hand_aim.Aim(gi.AIM_DEFAULTS)
        aim.start(np.zeros(2))
        aim.update(np.array([0.1, 0.0]))
        total = sum(aim.take(1 / 250)[0] for _ in range(250))
        self.assertGreater(total, 5)  # under 1 pixel per step, but it adds up

    def test_position_mode_follows_the_hand_and_stops(self):
        aim = hand_aim.Aim({**gi.AIM_DEFAULTS, "mode": "position", "position_gain": 1000})
        aim.start(np.array([0.5, 0.5]))
        aim.update(np.array([0.6, 0.5]))       # 0.1 shoulder widths right -> 100 px
        total = np.sum([aim.take(1 / 250) for _ in range(250)], axis=0)
        self.assertAlmostEqual(total[0], 100, delta=2)
        self.assertEqual(aim.take(1 / 250), (0, 0))  # the hand stays: no more movement

    def test_losing_the_hand_stops_speed_mode(self):
        aim = hand_aim.Aim(gi.AIM_DEFAULTS)
        aim.start(np.zeros(2))
        aim.update(np.array([0.4, 0.0]))
        aim.update(None)
        self.assertEqual(aim.take(0.5), (0, 0))


class FakeSensors:
    def __init__(self):
        self.button, self.pose = False, None

    def glove_ok(self, within=0.5):
        return True

    def latest_pose(self):
        return self.pose


class ButtonSwitchesModes(unittest.TestCase):
    def test_button_press_starts_steering_from_where_the_hand_is(self):
        g, sensors = game(), FakeSensors()
        tracking = hand_aim.Tracking(sensors, g, log=lambda *_: None)
        try:
            sensors.pose = (time.time(), *body(wrist_right=0.2, wrist_up=0.1))
            tracking.poll()
            self.assertFalse(g.tracking)
            sensors.button = True
            tracking.poll()
            self.assertTrue(g.tracking)
            np.testing.assert_allclose(tracking.aim.centre, [0.2, 0.1], atol=1e-6)
            sensors.pose = (time.time(), *body(wrist_right=0.2 + 0.4, wrist_up=0.1))  # hand moves right
            tracking.poll()
            time.sleep(0.2)
            self.assertGreater(g.keyboard.moved[0], 50)   # the driver thread moved the mouse right
            sensors.button = False
            tracking.poll()
            self.assertFalse(g.tracking)
            moved = g.keyboard.moved.copy()
            time.sleep(0.1)
            np.testing.assert_array_equal(g.keyboard.moved, moved)  # let go: no more movement
        finally:
            tracking.close()


class Editor(unittest.TestCase):
    def test_tk_keys_map_to_game_keys(self):
        self.assertEqual(controls_editor.key_from_tk("A"), "a")
        self.assertEqual(controls_editor.key_from_tk("space"), "space")
        self.assertEqual(controls_editor.key_from_tk("Shift_L"), "shift")
        self.assertEqual(controls_editor.key_from_tk("F5"), "f5")
        self.assertEqual(controls_editor.key_from_tk("Prior"), "pageup")
        self.assertIsNone(controls_editor.key_from_tk("Num_Lock"))
        for key in controls_editor.TK_KEYS.values():
            gi.check_key(key)  # everything the editor can produce can be sent

    def test_warnings_for_clashes_and_hotkeys(self):
        data = gi.Mapping.load(os.path.join(ROOT, "mappings", "controls.json")).to_dict()
        self.assertEqual(controls_editor.problems(gi.Mapping(data)), [])
        data["normal"]["JAB"]["bind"] = "w"           # same key as BLOCK, both active together
        data["tracking"]["HOOK"]["bind"] = "f8"       # the key-output hotkey
        found = " | ".join(controls_editor.problems(gi.Mapping(data)))
        self.assertIn("W is bound to both", found)
        self.assertIn("key output", found)

    def test_editor_window_saves_binds_and_steering(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as e:  # no display
            self.skipTest(f"no Tk display: {e}")
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "mine.json")
                ed = controls_editor.Editor(root, path)        # a missing file starts from the defaults
                ed.set_bind("tracking", "HOOK", "e")
                ed.aim_vars["mode"].set("position")
                ed.aim_changed()
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(saved["tracking"]["HOOK"]["bind"], "e")
                self.assertEqual(saved["aim"]["mode"], "position")
                self.assertEqual(saved["normal"]["JAB"]["bind"], "j")
        finally:
            root.destroy()


class LiveReload(unittest.TestCase):
    def test_watcher_loads_a_saved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            gi.Mapping({"normal": {"JAB": "j"}}).save(path)
            w = punch_live.ControlsWatcher(path, every_s=0)
            self.assertIsNone(w.poll())
            time.sleep(0.05)
            gi.Mapping({"normal": {"JAB": "q"}}).save(path)
            os.utime(path, (time.time() + 5, time.time() + 5))  # make sure the time stamp changes
            self.assertEqual(w.poll().taps["JAB"], "q")


if __name__ == "__main__":
    unittest.main()
