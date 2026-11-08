"""Drawing helpers for on-screen text that has to be readable from across a room.

Sizes are given in the camera picture's own pixels. Callers scale them with the picture's height
(height / 480), so the layout looks the same whatever resolution the webcam delivers.

Text is drawn in a real TrueType font (Segoe UI on Windows, with Arial, DejaVu and Liberation as fallbacks), smoothed at
the edges. OpenCV's own vector font is thin and spidery on a big screen, so it is kept only as a last resort for a
computer where none of those fonts can be found. A "scale" of 1.0 is a font about 30 pixels tall either way.
"""
import functools
import math

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
MAX_UPSCALE = 3.0
USE_TRUETYPE = True    # False draws everything with OpenCV's font: for tests, and the same as before this existed
FONT_PX = 30.0         # font size in pixels for a scale of 1.0
BOLD_FROM = 2.5        # callers ask for stroke "thickness"; this thick or more means the bold face
_REGULAR = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "Helvetica.ttc")
_BOLD = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "Helvetica.ttc")


@functools.lru_cache(maxsize=64)
def _load_font(bold, px):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for name in (_BOLD if bold else _REGULAR):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return None


def font(bold, px):
    """The TrueType font at this pixel size, or None when there is none to use (text then falls back to OpenCV's font)."""
    return _load_font(bool(bold), int(px)) if USE_TRUETYPE else None


def measure(text, scale, bold=False):
    """The width in pixels the text takes at this scale, in the same units as the scale."""
    px = scale * FONT_PX
    big = max(8, round(px * 4))   # measured at four times the size, then scaled back: rounding the size then costs nothing
    f = font(bold, big)
    if f is not None:
        return float(f.getlength(text)) * px / big
    return float(cv2.getTextSize(text, FONT, scale, 1)[0][0])

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


def finish_layer(frame, width, height, keep=False):
    """The picture enlarged to fit width x height (at most MAX_UPSCALE times), with the held text drawn on it.
    keep=True leaves the frame untouched and the text held, so the same picture can be finished again at another
    size (the control panel page and the corner popup both show it)."""
    k = min(width / frame.shape[1], height / frame.shape[0], MAX_UPSCALE) if width > 0 and height > 0 else 1.0
    out = cv2.resize(frame, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR) if k > 1.05 else (frame.copy() if keep else frame)
    k = out.shape[0] / frame.shape[0]
    items = list(_layer["items"])
    if not keep:
        _layer["frame"], _layer["items"] = None, []
    for kind, (text, x, y, scale, colour, thickness, max_width, align, bold) in items:
        draw = outlined_text if kind == "outlined" else fit_text
        draw(out, text, x * k, y * k, scale * k, colour, thickness * k, max_width and max_width * k, align, bold)
    return out


def fit_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left", bold=None):
    """Draw text with its baseline at y. Text wider than max_width is shrunk to fit.

    x is the left edge, the middle or the right edge of the text, depending on align
    ("left", "centre" or "right"). bold chooses the bold face; left out, a thickness of BOLD_FROM or more means bold.
    Returns the scale used.
    """
    bold = thickness >= BOLD_FROM if bold is None else bold
    if _held(frame, "plain", (text, x, y, scale, colour, thickness, max_width, align, bold)):
        return scale
    return _draw(frame, text, x, y, scale, colour, thickness, 0, max_width, align, bold)


def outlined_text(frame, text, x, y, scale, colour, thickness, max_width=None, align="left", bold=None):
    """Like fit_text, with a black outline, so it can be read over a busy picture such as a person."""
    bold = thickness >= BOLD_FROM if bold is None else bold
    if _held(frame, "outlined", (text, x, y, scale, colour, thickness, max_width, align, bold)):
        return scale
    return _draw(frame, text, x, y, scale, colour, thickness, max(2.0, 0.6 * min(thickness, 2.0 * scale)), max_width,
                 align, bold)


def _disc(radius):
    size = 2 * int(round(radius)) + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _draw(frame, text, x, y, scale, colour, thickness, outline, max_width, align, bold=False):
    """Text with an optional dark outline, smoothed at the edges: in the TrueType font when there is one (_draw_tt),
    otherwise in OpenCV's font as below.

    cv2.putText can't be trusted with thickness: OpenCV 5 draws every thickness above about 2 the same, so a
    black outline drawn "thicker" underneath was exactly covered by the text and never showed. Instead the
    text is drawn thin into a mask, which is widened to the thickness asked for, and widened again for the
    outline."""
    if font(bold, max(8, round(scale * FONT_PX))) is not None:
        return _draw_tt(frame, text, x, y, scale, colour, bold, outline, max_width, align)
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


def _place(frame, mult, add, x, y, left, width, top, pad):
    """Blend a prepared text shape onto the picture with its baseline at y. Anything off the picture is cut off."""
    x0, y0 = int(left) - pad, int(y) - top
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(frame.shape[1], x0 + mult.shape[1]), min(frame.shape[0], y0 + mult.shape[0])
    if fx1 <= fx0 or fy1 <= fy0:
        return
    view = frame[fy0:fy1, fx0:fx1]
    cut = (slice(fy0 - y0, fy1 - y0), slice(fx0 - x0, fx1 - x0))
    view[:] = (view * mult[cut] + add[cut] + 32512) // 65025


def _draw_tt(frame, text, x, y, scale, colour, bold, outline, max_width, align):
    """TrueType text. The outline (when asked for) is thin and scales with the letters: with a panel behind the
    text it is not needed, and over the camera picture it only has to keep the letters apart from it."""
    px = max(8, int(round(scale * FONT_PX)))
    width = font(bold, px).getlength(text)
    if max_width and width > max_width:
        px = max(8, int(px * max_width / width))
    ring = round(min(3.5, 1.2 + 0.035 * px), 1) if outline > 0 else 0.0
    mult, add, width, top, pad = _shape_tt(text, px, bold, ring, tuple(int(v) for v in colour))
    left = {"left": x, "centre": x - width / 2, "right": x - width}[align]
    _place(frame, mult, add, x, y, left, width, top, pad)
    return px / FONT_PX


@functools.lru_cache(maxsize=512)
def _shape_tt(text, px, bold, ring, colour):
    """The letters as blend arrays, like _shape, from the TrueType font. Worked out once per text, size and colour."""
    from PIL import Image, ImageDraw
    f = font(bold, px)
    ascent, descent = f.getmetrics()
    width = int(math.ceil(f.getlength(text)))
    pad = int(ring) + 3
    img = Image.new("L", (width + 2 * pad, ascent + descent + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad, pad), text, font=f, fill=255)
    mask = np.asarray(img).copy()
    edge = cv2.dilate(mask, _disc(ring)) if ring >= 0.5 else np.zeros_like(mask)
    a_text = mask.astype(np.uint32)[:, :, None]
    a_ring = np.maximum(edge, mask).astype(np.uint32)[:, :, None]
    mult = (255 - a_ring) * (255 - a_text)
    add = a_text * np.array(colour, np.uint32) * 255
    return mult, add, width, pad + ascent, pad


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
