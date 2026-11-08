"""Drawing helpers for on-screen text that has to be readable from across a room.

Sizes are given in the camera picture's own pixels. Callers scale them with the picture's height
(height / 480), so the layout looks the same whatever resolution the webcam delivers.
"""
import functools

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
MAX_UPSCALE = 3.0

# Sharp text on a big screen. The camera picture is small (often 640x480) and the window stretches it to fit,
# which blurs and pixelates text drawn on it. So pose_tracker opens a "text layer" on the picture it is about
# to show: text drawn on that picture with these helpers (or punch_session.put) is held back, and finish_layer()
# enlarges the picture to the window's real size and only then draws the text, at that size. Callers keep
# using the small picture's coordinates. Text on any other picture (a popup) is drawn straight away.
_layer = {"frame": None, "items": []}


def begin_layer(frame):
    _layer["frame"], _layer["items"] = frame, []


def _held(frame, kind, args):
    if frame is not None and frame is _layer["frame"]:
        _layer["items"].append((kind, args))
        return True
    return False


def finish_layer(frame, width, height):
    """The picture enlarged to fit width x height (at most MAX_UPSCALE times), with the held text drawn on it."""
    k = min(width / frame.shape[1], height / frame.shape[0], MAX_UPSCALE) if width > 0 and height > 0 else 1.0
    out = cv2.resize(frame, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR) if k > 1.05 else frame
    k = out.shape[0] / frame.shape[0]
    items, _layer["frame"], _layer["items"] = _layer["items"], None, []
    for kind, (text, x, y, scale, colour, thickness, max_width, align) in items:
        draw = outlined_text if kind == "outlined" else fit_text
        draw(out, text, x * k, y * k, scale * k, colour, thickness * k, max_width and max_width * k, align)
    return out


def fit_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left"):
    """Draw text with its baseline at y. Text wider than max_width is shrunk to fit.

    x is the left edge, the middle or the right edge of the text, depending on align
    ("left", "centre" or "right"). Returns the scale used.
    """
    if _held(frame, "plain", (text, x, y, scale, colour, thickness, max_width, align)):
        return scale
    return _draw(frame, text, x, y, scale, colour, thickness, 0, max_width, align)


def outlined_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left"):
    """Like fit_text, with a black outline, so it can be read over a busy picture such as a person."""
    if _held(frame, "outlined", (text, x, y, scale, colour, thickness, max_width, align)):
        return scale
    return _draw(frame, text, x, y, scale, colour, thickness, max(2.0, 0.6 * min(thickness, 2.0 * scale)), max_width, align)


def _disc(radius):
    size = 2 * int(round(radius)) + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _draw(frame, text, x, y, scale, colour, thickness, outline, max_width, align):
    """Text of any stroke thickness, with an optional black outline, smoothed at the edges.

    cv2.putText can't be trusted with thickness: OpenCV 5 draws every thickness above about 2 the same, so a
    black outline drawn "thicker" underneath was exactly covered by the text and never showed. Instead the
    text is drawn thin into a mask, which is widened to the thickness asked for, and widened again for the
    outline."""
    (width, _), _ = cv2.getTextSize(text, FONT, scale, 1)
    thickness = min(thickness, 2.0 * scale)  # heavier than this and the letters fill in
    grow = max(0.0, thickness - 1.0) / 2
    if max_width and width + 2 * grow > max_width:
        ratio = max_width / (width + 2 * grow)
        scale, grow, outline = scale * ratio, grow * ratio, outline * ratio
    mult, add, width, top, pad = _shape(text, round(scale, 2), round(grow, 1), round(outline, 1),
                                        tuple(int(v) for v in colour))
    left = {"left": x, "centre": x - width / 2, "right": x - width}[align]
    x0, y0 = int(left) - pad, int(y) - top
    # the part of the text that lands on the picture
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(frame.shape[1], x0 + mult.shape[1]), min(frame.shape[0], y0 + mult.shape[0])
    if fx1 <= fx0 or fy1 <= fy0:
        return scale
    view = frame[fy0:fy1, fx0:fx1]
    cut = (slice(fy0 - y0, fy1 - y0), slice(fx0 - x0, fx1 - x0))
    view[:] = (view * mult[cut] + add[cut] + 32512) // 65025
    return scale


@functools.lru_cache(maxsize=512)
def _shape(text, scale, grow, outline, colour):
    """The drawn letters, ready to blend: picture * mult + add, over 255 * 255. Text on screen barely changes
    from frame to frame, so this is worked out once per text, size and colour."""
    (width, height), base = cv2.getTextSize(text, FONT, scale, 1)
    pad = int(grow + outline + 3)
    mask = np.zeros((height + base + 2 * pad, width + 2 * pad), np.uint8)
    cv2.putText(mask, text, (pad, pad + height), FONT, scale, 255, 1, cv2.LINE_AA)
    if grow >= 0.5:
        mask = cv2.dilate(mask, _disc(grow))
    ring = cv2.dilate(mask, _disc(outline)) if outline >= 0.5 else np.zeros_like(mask)
    a_text = mask.astype(np.uint32)[:, :, None]
    a_ring = np.maximum(ring, mask).astype(np.uint32)[:, :, None]
    # black outline first, then the text: p * (255 - ring) * (255 - text) + colour * text * 255
    mult = (255 - a_ring) * (255 - a_text)
    add = a_text * np.array(colour, np.uint32) * 255
    return mult, add, width, pad + height, pad


def border(frame, colour, thickness):
    """A coloured frame around the whole picture. It is the first thing seen from a distance."""
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), colour, max(2, int(2 * thickness)))  # half of it is off-screen


def shade(frame, top, bottom, alpha=0.65):
    """Darken the rows from top to bottom so text drawn on them stands out."""
    band = frame[max(0, int(top)):int(bottom)]
    if band.size:
        cv2.addWeighted(band, 1 - alpha, np.zeros_like(band), alpha, 0, band)
