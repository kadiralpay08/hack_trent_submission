import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import popup_window


class CornerRectTests(unittest.TestCase):
    WORK = (0, 0, 1280, 752)  # a 1280 x 800 screen minus a 48 px taskbar

    def test_each_corner_sits_inside_the_margin(self):
        size = (360, 270)
        self.assertEqual(popup_window.corner_rect(self.WORK, size, "bottom-right", 16), (904, 466, 360, 270))
        self.assertEqual(popup_window.corner_rect(self.WORK, size, "bottom-left", 16), (16, 466, 360, 270))
        self.assertEqual(popup_window.corner_rect(self.WORK, size, "top-right", 16), (904, 16, 360, 270))
        self.assertEqual(popup_window.corner_rect(self.WORK, size, "top-left", 16), (16, 16, 360, 270))

    def test_work_area_that_does_not_start_at_zero(self):
        # a second monitor to the left, or a taskbar on the left edge
        self.assertEqual(popup_window.corner_rect((-1920, 40, 0, 1080), (320, 240), "top-left", 10),
                         (-1910, 50, 320, 240))

    def test_unknown_corner_is_rejected(self):
        with self.assertRaises(ValueError):
            popup_window.Popup(corner="middle")


if __name__ == "__main__":
    unittest.main()
