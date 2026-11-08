"""The trainer screen and the recognition overlay: every state draws, fits the picture at every size, and the
font fallback works. Nothing here needs a camera, a glove or a window: the real screen code is run on a picture of
plain grey with stand-in sensors.

render_all(folder) saves every screen as a picture, to look at when changing the design:
    python -c "import tests.test_hud as t; t.render_all('hud_preview')"
"""
import os
import sys
import time
import types
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hud  # noqa: E402
import overlay  # noqa: E402
import punch_capture  # noqa: E402
import punch_live  # noqa: E402
import punch_session as ps  # noqa: E402

SIZES = [(640, 480), (1280, 720), (1920, 1080), (960, 480)]


# ---------------------------------------------------------------- stand-ins for the sensors and the recorders
class FakeSensors:
    def __init__(self, glove=True, ready=True):
        self.glove, self.segmenter = glove, types.SimpleNamespace(ready=ready)
        self.settings = types.SimpleNamespace(start_g=1.5, timeout_s=0.35, decel_g=3.0)

    def glove_ok(self):
        return self.glove

    def latest_imu_t(self):
        return 100.0

    def recent_linear_g(self, seconds):
        t = np.linspace(97.0, 100.0, 150)
        return t, 0.4 + 6 * np.exp(-((t - 98.6) / 0.15) ** 2)    # one punch in the last three seconds


def photo(width, height):
    """A stand-in for the camera picture: a soft gradient with a person-shaped blob."""
    img = np.zeros((height, width, 3), np.uint8)
    img[:] = (np.linspace(90, 140, height)[:, None, None] + np.array([0, 8, 12])).astype(np.uint8)
    import cv2
    cv2.rectangle(img, (width // 2 - width // 9, height // 3), (width // 2 + width // 9, height), (70, 60, 55), -1)
    cv2.circle(img, (width // 2, height // 3 - height // 20), height // 11, (115, 125, 155), -1)
    return img


def one_arm(phase="go", paused=False, glove=True, reason="", label="JAB", result="", result_colour=punch_capture.GREEN,
            arm="left", counts=None, button=False):
    arm = ps.ARMS[arm]
    cap = punch_capture.PromptedCapture.__new__(punch_capture.PromptedCapture)
    cap.arm, cap.how, cap.target = arm, arm.how, 25
    cap.names = types.SimpleNamespace(waiting=False)
    cap.sensors = FakeSensors(glove)
    cap.phase, cap.paused, cap.prompt = phase, paused, label
    cap.phase_end = time.time() + 4.2
    cap.counts = counts or {"JAB": 12, "HOOK": 25, "UPPERCUT": 3, "NONE": 6, "CROSS": 4}
    cap.result, cap.result_colour, cap.result_t = result, result_colour, time.time() if result else 0.0
    cap.not_ready_reason = lambda: reason
    return cap


def both_arms(phase="go", reason="", result="", glove=(True, True)):
    cap = punch_capture.BothCapture.__new__(punch_capture.BothCapture)
    cap.names = types.SimpleNamespace(waiting=False)
    cap.target, cap.phase, cap.paused = 25, phase, False
    cap.phase_end = time.time() + 7.0
    cap.combo = [("left", "HOOK"), ("right", "UPPERCUT")]
    cap.first_counts = {"left": 12, "right": 11}
    cap.result, cap.result_colour, cap.result_t = result, punch_capture.RED, time.time() if result else 0.0
    cap.not_ready_reason = lambda: reason
    cap.recorders = {}
    for name, ok in zip(("left", "right"), glove):
        arm = ps.ARMS[name]
        counts = {k: 5 + 7 * i for i, k in enumerate(arm.labels)}
        counts["NONE"] = 3
        cap.recorders[name] = types.SimpleNamespace(arm=arm, counts=counts, sensors=FakeSensors(ok))
    return cap


def live(name="", scale=1.0, x=10, footer=True, glove=True, ready=True, enabled=True, shown=None):
    clf = types.SimpleNamespace(min_confidence=0.6, segment=None)
    game = types.SimpleNamespace(enabled=enabled, tracking=False)
    play = punch_live.LivePlay(FakeSensors(glove, ready), clf, game, name=name, x=x, scale=scale, footer=footer)
    if shown:
        play.show(*shown)
    return play


def tuner(glove=True, ready=True, state="idle", test=None, report=None, message=""):
    import punch_segment
    import punch_tune
    t = punch_tune.Tuner.__new__(punch_tune.Tuner)
    t.sensors = FakeSensors(glove, ready)
    t.count, t.marks = 7, [(98.4, punch_tune.GREEN), (98.8, punch_tune.BLUE)]
    seg = types.SimpleNamespace(start_t=99.9, duration=0.31, reason="decel")
    t.active = seg if state == "active" else None
    t.last = seg if state == "ended" else None
    t.message, t.message_t = message, time.time() if message else 0.0
    t.test, t.report, t.report_t = test, report or [], time.time() if report else 0.0
    t.not_ready_reason = lambda: ""
    return t


def steering(held=True, offset=(0.05, 0.03), mode="speed"):
    """Tracking mode's joystick panel, drawn by the real Tracking.draw on stand-in state."""
    import hand_aim
    fake = types.SimpleNamespace(held=held, aim=types.SimpleNamespace(offset=offset, settings={"mode": mode, "dead_zone": 0.06}))
    return lambda frame: hand_aim.Tracking.draw(fake, frame, None)


def draw(on_draw, size=(640, 480), window=None):
    """Run a screen's drawing the way pose_tracker does: on a held text layer, then enlarged to the window."""
    frame = photo(*size)
    overlay.begin_layer(frame)
    on_draw(frame)
    return overlay.finish_layer(frame, *(window or (size[0] * 2, size[1] * 2)))


def pose_screen(frame):
    """The camera view's own parts: status row, hints, skeleton, slip labels."""
    import pose_tracker as pt
    h, w = frame.shape[:2]
    img = np.full((33, 2), 0.5)
    img[pt.NOSE] = (0.5, 0.25); img[pt.L_SH] = (0.58, 0.4); img[pt.R_SH] = (0.42, 0.4)
    img[pt.L_EL] = (0.62, 0.52); img[pt.R_EL] = (0.38, 0.52); img[pt.L_WR] = (0.58, 0.32); img[pt.R_WR] = (0.42, 0.32)
    img[pt.L_HIP] = (0.56, 0.7); img[pt.R_HIP] = (0.44, 0.7)
    vis = np.ones(33)
    pt.draw(frame, img, vis, {"L": ("JAB 4.2", time.time()), "R": ("", 0.0)}, time.time())
    pt.draw_slips(frame, img, vis, ["SLIP LEFT"], lambda state: "a")
    hud.status_row(frame, left=("BLOCK   speed L 1.2  R 0.4", hud.TEXT))
    hud.pill(frame, "CALIBRATED", hud.width_of(frame) / 2, hud.TOP_ROW, hud.GOOD, size=0.55, align="centre")
    hud.hint(frame, "C = recalibrate", "left")


def screens():
    """name -> a function that draws one screen onto a picture."""
    s = {}
    for phase in ("waiting", "announce", "rest", "go", "free", "done"):
        s[f"capture_{phase}"] = one_arm(phase).on_draw
    s["capture_paused"] = one_arm("go", paused=True).on_draw
    s["capture_not_ready"] = one_arm("waiting", reason="press C to calibrate first").on_draw
    s["capture_no_glove"] = one_arm("go", glove=False).on_draw
    s["capture_saved"] = one_arm("go", result="saved JAB 12/25: 310 ms, 6.1 g").on_draw
    s["capture_missed"] = one_arm("go", result="MISSED JAB: no punch in the 1.6 s after the beep. It will come up again.",
                                  result_colour=punch_capture.RED).on_draw
    s["capture_right_arm"] = one_arm("go", arm="right", label="CROSS").on_draw
    for phase in ("waiting", "rest", "go", "free", "done"):
        s[f"both_{phase}"] = both_arms(phase).on_draw
    s["both_no_right"] = both_arms("go", glove=(True, False), result="RIGHT: MISSED UPPERCUT: no punch in 1.6 s").on_draw
    s["live_ready"] = live().on_draw
    s["live_jab"] = live(shown=("JAB 91%  (j)", punch_live.GREEN, "JAB", "key j", "91%")).on_draw
    s["live_unsure"] = live(shown=("unsure", punch_live.ORANGE, "Unsure", "best guess HOOK: no key pressed", "48%")).on_draw
    s["live_not_a_punch"] = live(shown=("not a punch", punch_live.WHITE, "Not a punch", "no key pressed", "88%")).on_draw
    s["live_no_glove"] = live(glove=False).on_draw
    s["live_calibrating"] = live(ready=False).on_draw
    s["live_keys_off"] = live(enabled=False).on_draw
    left = live(name="LEFT: ", scale=0.65, x=10, footer=True, shown=("HOOK", punch_live.GREEN, "HOOK", "key k", "77%"))
    right = live(name="RIGHT: ", scale=0.65, x=330, footer=False, glove=False)

    def two_arms(frame):
        left.on_draw(frame)
        right.on_draw(frame)
    s["live_both_arms"] = two_arms
    s["pose_parts"] = pose_screen
    s["steering_on"] = steering()
    s["steering_no_hand"] = steering(offset=None)
    s["steering_position_mode"] = steering(mode="position")
    s["tune_idle"] = tuner().on_draw
    s["tune_no_glove"] = tuner(glove=False).on_draw
    s["tune_still"] = tuner(ready=False).on_draw
    s["tune_started"] = tuner(state="active").on_draw
    s["tune_ended"] = tuner(state="ended", message="timeout set to 350 ms").on_draw
    s["tune_test_running"] = tuner(test={"what": "10 JAB", "state": "running", "left": 60, "run_wall": time.time(), "seen": 4,
                                         "go_wall": 0}).on_draw
    s["tune_test_report"] = tuner(state="ended", report=["now 350 ms: found 12, jabs 9", "best 300 ms: found 12, jabs 11",
                                                          "250 ms: found 11, jabs 8"]).on_draw
    return s


def render_all(folder):
    import cv2
    os.makedirs(folder, exist_ok=True)
    for name, fn in screens().items():
        cv2.imwrite(os.path.join(folder, name + ".png"), draw(fn, (640, 480), (1280, 960)))
    cv2.imwrite(os.path.join(folder, "capture_go_1080p.png"), draw(screens()["capture_go"], (1280, 720), (1920, 1080)))
    return sorted(screens())


# ---------------------------------------------------------------- the tests
class EveryScreenDraws(unittest.TestCase):
    def test_every_screen_draws_at_every_size(self):
        for name, fn in screens().items():
            for size in SIZES:
                with self.subTest(screen=name, size=size):
                    out = draw(fn, size)
                    self.assertEqual(out.shape[:2], (size[1] * 2, size[0] * 2))
                    plain = draw(lambda f: None, size)
                    self.assertGreater(int(np.abs(out.astype(int) - plain.astype(int)).sum()), 0, "the screen drew nothing")

    def test_screens_look_different_in_different_states(self):
        shots = {phase: draw(one_arm(phase).on_draw) for phase in ("waiting", "announce", "rest", "go", "free", "done")}
        names = list(shots)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                self.assertFalse(np.array_equal(shots[a], shots[b]), f"{a} and {b} look the same")

    def test_the_prompt_is_what_the_screen_says(self):
        """Different punches, different pictures: the screen must not show the same thing for JAB and HOOK."""
        a, b = draw(one_arm("go", label="JAB").on_draw), draw(one_arm("go", label="HOOK").on_draw)
        self.assertFalse(np.array_equal(a, b))

    def test_a_missing_glove_changes_the_picture(self):
        self.assertFalse(np.array_equal(draw(one_arm("go").on_draw), draw(one_arm("go", glove=False).on_draw)))
        self.assertFalse(np.array_equal(draw(live().on_draw), draw(live(glove=False).on_draw)))

    def test_the_recognition_card_shows_a_new_result_then_goes_back(self):
        play = live()
        idle = draw(play.on_draw)
        play.show("JAB 91%", punch_live.GREEN, "JAB", "key j", "91%")
        self.assertFalse(np.array_equal(idle, draw(play.on_draw)))
        play.shown_t -= 2.0   # more than the 1.5 s a result stays up
        self.assertTrue(np.array_equal(idle, draw(play.on_draw)))

    def test_the_steering_panel_only_shows_while_tracking(self):
        self.assertTrue(np.array_equal(draw(steering(held=False)), draw(lambda f: None)))
        self.assertFalse(np.array_equal(draw(steering(held=True)), draw(lambda f: None)))

    def test_the_popup_draws(self):
        play = live(shown=("JAB 91%", punch_live.GREEN, "JAB", "key j", "91%"))
        for size in ((640, 480), (1280, 720)):
            frame = photo(*size)
            play.on_draw_popup(frame)
            self.assertGreater(int(np.abs(frame.astype(int) - photo(*size).astype(int)).sum()), 0)

    def test_old_colours_become_the_calm_ones(self):
        self.assertEqual(hud.tone(punch_capture.GREEN), hud.GOOD)
        self.assertEqual(hud.tone(punch_capture.RED), hud.BAD)
        self.assertEqual(hud.tone(punch_capture.ORANGE), hud.WARN)
        self.assertEqual(hud.tone((1, 2, 3)), (1, 2, 3))


class Layout(unittest.TestCase):
    def test_panels_stay_inside_the_picture(self):
        """Nothing may be drawn past the edge: the border of the picture is left untouched by every screen."""
        for name, fn in screens().items():
            if name in ("pose_parts", "live_not_a_punch"):
                continue
            for size in SIZES:
                with self.subTest(screen=name, size=size):
                    frame = photo(*size)
                    before = frame.copy()
                    fn(frame)
                    m = max(2, size[1] // 120)
                    self.assertTrue(np.array_equal(frame[:, :m], before[:, :m]), "drew on the left edge")
                    self.assertTrue(np.array_equal(frame[:m], before[:m]), "drew on the top edge")

    def test_card_and_message_do_not_overlap(self):
        """The main card ends above where the message pill starts, so a long result never covers the prompt."""
        self.assertLess(hud.CARD_TOP + hud.CARD_H, hud.progress_top(2) - 36)
        self.assertLess(hud.progress_top(1) + 46, hud.BOTTOM - 30)

    def test_long_text_is_shrunk_not_cut_off_or_wider_than_its_panel(self):
        long_title = "A VERY LONG REASON THE SCREEN MAY HAVE TO SHOW HERE " * 2
        frame = photo(640, 480)
        overlay.begin_layer(frame)
        hud.main_card(frame, "Ready", long_title, "detail " * 30)
        out = overlay.finish_layer(frame, 1280, 960)
        card_right = int((hud.width_of(frame) - hud.MARGIN) * 2)
        outside = out[int(hud.CARD_TOP * 2):int((hud.CARD_TOP + hud.CARD_H) * 2), card_right + 6:card_right + 40]
        self.assertTrue(np.array_equal(outside, draw(lambda f: None)[int(hud.CARD_TOP * 2):int((hud.CARD_TOP + hud.CARD_H) * 2),
                                                                     card_right + 6:card_right + 40]))

    def test_the_wide_picture_uses_its_width(self):
        self.assertGreater(hud.width_of(photo(1280, 720)), hud.width_of(photo(640, 480)))
        self.assertAlmostEqual(hud.width_of(photo(640, 480)), 640.0)


class Fonts(unittest.TestCase):
    def tearDown(self):
        overlay.USE_TRUETYPE = True

    def test_there_is_a_real_font_on_this_computer(self):
        self.assertIsNotNone(overlay.font(False, 30), "no TrueType font found: text falls back to OpenCV's thin font")
        self.assertIsNotNone(overlay.font(True, 30))

    def test_every_screen_still_draws_with_the_old_font(self):
        overlay.USE_TRUETYPE = False
        for name, fn in screens().items():
            with self.subTest(screen=name):
                draw(fn)

    def test_both_fonts_agree_roughly_on_how_wide_text_is(self):
        """The layout is measured with the font in use, so neither may be wildly off for the same words."""
        words = "UPPERCUT 25/25"
        new = overlay.measure(words, 1.0)
        overlay.USE_TRUETYPE = False
        old = overlay.measure(words, 1.0)
        self.assertTrue(0.5 < new / old < 1.5, f"{new} vs {old}")

    def test_text_is_held_until_the_picture_is_enlarged_and_drawn_sharp(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        overlay.begin_layer(frame)
        hud.text(frame, "HELLO", 100, 100, 1.0, hud.TEXT, True)
        self.assertEqual(int(frame.sum()), 0)
        out = overlay.finish_layer(frame, 1920, 1440)
        self.assertGreater(int(out[250:320].sum()), 0)

    def test_measure_matches_what_is_drawn(self):
        """A pill is as wide as the measured text, so the measurement has to be close to the drawn width."""
        frame = np.zeros((480, 640, 3), np.uint8)
        text = "saved JAB 12/25: 310 ms, 6.1 g"
        overlay.begin_layer(frame)
        hud.text(frame, text, 20, 100, 0.55, (255, 255, 255))
        out = overlay.finish_layer(frame, 1280, 960)
        cols = np.where(out.max(axis=(0, 2)) > 60)[0]
        drawn = (cols.max() - cols.min()) / 2
        self.assertAlmostEqual(drawn, hud.text_width(frame, text, 0.55), delta=0.06 * drawn + 3)


if __name__ == "__main__":
    unittest.main()
