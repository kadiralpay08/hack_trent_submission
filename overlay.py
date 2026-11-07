"""Drawing helpers for on-screen text that has to be readable from across a room.

Sizes are given in the camera picture's own pixels. Callers scale them with the picture's height
(height / 480), so the layout looks the same whatever resolution the webcam delivers.
"""
import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def fit_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left"):
    """Draw text with its baseline at y. Text wider than max_width is shrunk to fit.

    x is the left edge, the middle or the right edge of the text, depending on align
    ("left", "centre" or "right"). Returns the scale used.
    """
    thickness = max(1, int(round(thickness)))
    (width, _), _ = cv2.getTextSize(text, FONT, scale, thickness)
    if max_width and width > max_width:
        ratio = max_width / width
        scale *= ratio
        thickness = max(1, int(round(thickness * ratio)))
        (width, _), _ = cv2.getTextSize(text, FONT, scale, thickness)
    left = {"left": x, "centre": x - width / 2, "right": x - width}[align]
    cv2.putText(frame, text, (int(left), int(y)), FONT, scale, colour, thickness, cv2.LINE_AA)
    return scale


def outlined_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left"):
    """Like fit_text, with a black outline, so it can be read over a busy picture such as a person."""
    used = fit_text(frame, text, x, y, scale, (0, 0, 0), thickness + 6, max_width, align)
    return fit_text(frame, text, x, y, used, colour, thickness, None, align)


def border(frame, colour, thickness):
    """A coloured frame around the whole picture. It is the first thing seen from a distance."""
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), colour, max(2, int(2 * thickness)))  # half of it is off-screen


def shade(frame, top, bottom, alpha=0.65):
    """Darken the rows from top to bottom so text drawn on them stands out."""
    band = frame[max(0, int(top)):int(bottom)]
    if band.size:
        cv2.addWeighted(band, 1 - alpha, np.zeros_like(band), alpha, 0, band)
