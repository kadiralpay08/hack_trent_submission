"""Clean on-screen panels for the camera windows: the recording screen and the live recognition overlay.

Everything here only DRAWS. It holds no recording or recognition logic: the tools (punch_capture.py, punch_live.py,
pose_tracker.py) decide what to show and this module decides how it looks, so the look is the same everywhere.

The look: soft dark panels over the camera picture, one weight of real font (see overlay.py), a small set of calm
colours that each mean one thing (green = good or go, amber = wait or careful, red = a problem, blue = information),
and nothing drawn on the person except the skeleton. Colours are never the only signal: every panel says it in words.

Sizes are in "design pixels", a picture 480 high. A taller picture is the same layout, larger (and a wider one has
more room to the right), so it looks the same on a 640x480 webcam and on a 1080p one.
"""
import cv2
import numpy as np

import overlay

# colours are BGR, as OpenCV wants them
INK = (30, 24, 20)        # panel background
TEXT = (248, 246, 244)    # main text
MUTED = (206, 200, 193)   # secondary text
GOOD = (150, 222, 134)    # green: done, go, keys on
WARN = (92, 188, 244)     # amber: wait, careful, unsure
BAD = (126, 130, 255)     # red: a problem
INFO = (255, 204, 128)    # blue: information, getting ready
TRACK = (88, 80, 74)      # the empty part of a bar

MARGIN = 14               # from the edge of the picture
TOP_ROW = 12              # the small status row at the top
CARD_TOP = 54             # the main card, under the status row
CARD_H = 100
BOTTOM = 480              # design height


# The tools were written with a different set of colours. They still pass those, and this turns them into the calm ones.
_OLD = {(0, 220, 0): GOOD, (0, 200, 0): GOOD, (0, 255, 0): GOOD, (0, 0, 255): BAD, (0, 150, 255): WARN,
        (0, 255, 255): WARN, (255, 255, 255): TEXT, (255, 170, 0): INFO, (255, 0, 0): INFO}


def tone(colour):
    """The calm colour for one of the tools' older colours; any other colour is left as it is."""
    return _OLD.get(tuple(int(v) for v in colour), colour)


def scale_of(frame):
    return frame.shape[0] / 480.0


def width_of(frame):
    """The picture's width in design pixels (640 for a 4:3 picture, more for a wide one)."""
    return frame.shape[1] / scale_of(frame)


# ---------------------------------------------------------------------------- shapes
def card(frame, x, y, w, h, accent=None, alpha=0.8, radius=10):
    """A soft dark rounded panel over the picture, optionally with a coloured strip down its left edge."""
    k = scale_of(frame)
    x, y, w, h, r = (int(round(v * k)) for v in (x, y, w, h, radius))
    if w < 2 or h < 2:
        return
    r = max(1, min(r, w // 2, h // 2))
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (r, 0), (w - r - 1, h - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (w - 1, h - r - 1), 255, -1)
    for cx, cy in ((r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)):
        cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    m = mask[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)[:, :, None] / 255.0
    roi = frame[y0:y1, x0:x1]
    roi[:] = (roi * (1 - alpha * m) + np.array(INK, np.float32) * alpha * m).astype(np.uint8)
    if accent is not None:
        strip = max(3, int(round(5 * k)))
        sx1 = min(x1, x + strip)
        if sx1 > x0:
            sm = m[:, :sx1 - x0]
            roi[:, :sx1 - x0] = (roi[:, :sx1 - x0] * (1 - 0.95 * sm) + np.array(accent, np.float32) * 0.95 * sm).astype(np.uint8)


def bar(frame, x, y, w, h, fraction, colour=GOOD):
    """A thin progress bar: the track, then the filled part."""
    k = scale_of(frame)
    x0, y0, x1, y1 = (int(round(v * k)) for v in (x, y, x + w, y + h))
    if x1 <= x0 or y1 <= y0:
        return
    cv2.rectangle(frame, (x0, y0), (x1, y1), TRACK, -1)
    fill = int(round((x1 - x0) * min(1.0, max(0.0, fraction))))
    if fill > 0:
        cv2.rectangle(frame, (x0, y0), (x0 + fill, y1), colour, -1)


# ---------------------------------------------------------------------------- text
def text(frame, s, x, y, size=0.6, colour=TEXT, bold=False, align="left", max_width=None, over_video=False):
    """Text with its baseline at y. On a panel it needs no outline; drawn straight on the camera picture
    (over_video=True) it gets a thin dark one so it can still be read."""
    k = scale_of(frame)
    draw = overlay.outlined_text if over_video else overlay.fit_text
    draw(frame, s, x * k, y * k, size * k, colour, 1.0 * k, max_width and max_width * k, align, bold)


def text_width(frame, s, size=0.6, bold=False):
    """How wide the text is, in design pixels."""
    k = scale_of(frame)
    return overlay.measure(s, size * k, bold) / k


def pill(frame, s, x, y, colour=TEXT, size=0.55, bold=True, align="left", accent=None, max_width=None):
    """A short label on its own small panel. (x, y) is its top left, top right or top middle, by align. A label wider
    than max_width is made smaller to fit. Returns the pill's width, so labels can be put side by side."""
    pad = 12
    w = text_width(frame, s, size, bold) + 2 * pad + (6 if accent is not None else 0)
    if max_width and w > max_width:
        size *= (max_width - 2 * pad) / (w - 2 * pad)
        w = max_width
    h = size * 34 + 8
    left = {"left": x, "right": x - w, "centre": x - w / 2}[align]
    card(frame, left, y, w, h, accent=accent, radius=h / 2 if accent is None else 8)
    text(frame, s, left + pad + (6 if accent is not None else 0), y + h / 2 + size * 10.5, size, colour, bold)
    return w


# ---------------------------------------------------------------------------- standard layouts
def status_row(frame, left=None, right=None):
    """The small row along the top: what the camera sees (left) and one thing to note (right), as (text, colour)."""
    W = width_of(frame)
    if left:
        pill(frame, left[0], MARGIN, TOP_ROW, left[1], size=0.5, bold=False)
    if right:
        pill(frame, right[0], W - MARGIN, TOP_ROW, right[1], size=0.5, bold=True, align="right")


def main_card(frame, kicker, title, detail=None, accent=INFO, title_colour=TEXT, right=None, right_colour=TEXT,
              x=MARGIN, width=None, y=CARD_TOP):
    """The one big panel that says what is happening and what to do. kicker is a small heading, title the big words,
    detail a line of help; right is a number (a countdown, or a count) at the far end."""
    W = width_of(frame)
    width = (W - 2 * MARGIN) if width is None else width
    card(frame, x, y, width, CARD_H, accent=accent)
    inner = x + 20
    room = width - 40
    right_w = 0.0
    if right:
        right_w = text_width(frame, right, 1.5, True) + 16
        text(frame, right, x + width - 18, y + 68, 1.5, right_colour, True, align="right")
    # a short word is shown big; a sentence is smaller, so it never crowds the line under it
    size = 1.45 if len(title) <= 12 else 1.2 if len(title) <= 24 else 1.0
    text(frame, kicker.upper(), inner, y + 27, 0.48, MUTED, True, max_width=room - right_w)
    text(frame, title, inner, y + 64, size, title_colour, True, max_width=room - right_w)
    if detail:
        text(frame, detail, inner, y + 90, 0.55, MUTED, False, max_width=room - right_w)


def steps_card(frame, kicker, steps, accent=GOOD, width=None, x=MARGIN, y=CARD_TOP):
    """Like main_card, with two or three lines of big words, one per step: [(text, colour), ...]."""
    W = width_of(frame)
    width = (W - 2 * MARGIN) if width is None else width
    card(frame, x, y, width, CARD_H, accent=accent)
    text(frame, kicker.upper(), x + 20, y + 27, 0.48, MUTED, True, max_width=width - 40)
    for i, (s, colour) in enumerate(steps[:2]):
        text(frame, s, x + 20, y + 61 + 33 * i, 1.0 if i == 0 else 0.9, colour, True, max_width=width - 40)


def countdown(frame, title, number, colour=INFO):
    """A big number in the middle of the picture (calibration), with a few words above it."""
    W = width_of(frame)
    w, h = 300, 210
    x, y = (W - w) / 2, max((BOTTOM - h) / 2, CARD_TOP + CARD_H + 14)   # under the main card, never over it
    card(frame, x, y, w, h, accent=colour)
    text(frame, title.upper(), W / 2, y + 48, 0.8, colour, True, align="centre", max_width=w - 40)
    text(frame, str(number), W / 2, y + 170, 4.2, TEXT, True, align="centre")


def progress_top(rows):
    """Where the progress panel for this many rows starts, so a message can be put above it."""
    return BOTTOM - 34 - (rows * 34 + 12)


def progress(frame, rows, target, note=None):
    """Recordings so far, along the bottom. rows is [(who, [(label, count), ...]), ...]; who is "" for one arm.
    Each count gets a small bar that turns green when it reaches the target. note is a quiet line in the corner."""
    W = width_of(frame)
    top = progress_top(len(rows))
    card(frame, MARGIN, top, W - 2 * MARGIN, len(rows) * 34 + 12)
    if note:
        text(frame, note, W - MARGIN - 18, top - 6, 0.42, MUTED, False, align="right", max_width=W / 2, over_video=True)
    for r, (who, items) in enumerate(rows):
        base = top + 30 + 34 * r
        left = MARGIN + 18
        if who:
            text(frame, who, left, base, 0.55, MUTED, True)
            left += 62
        cell = (W - MARGIN - 18 - left) / max(1, len(items))
        for i, (label, count) in enumerate(items):
            cx = left + i * cell
            done = target and count >= target
            tag = f"{count}/{target}" if label != "NONE" else f"{count}"
            tag_w = text_width(frame, tag, 0.5, True)
            text(frame, label, cx, base - 4, 0.45, MUTED, False, max_width=max(20, cell - 24 - tag_w))
            text(frame, tag, cx + cell - 16, base - 4, 0.5, GOOD if done else TEXT, True, align="right")
            if label != "NONE" and target:
                bar(frame, cx, base + 2, cell - 16, 3, count / target, GOOD if done else INFO)


def toast(frame, s, colour=TEXT, rows=1, y=None):
    """A short message in the middle, above the progress panel (the most recent thing that happened)."""
    W = width_of(frame)
    y = progress_top(rows) - 36 if y is None else y
    pill(frame, s[:100], W / 2, y, colour, size=0.55, bold=False, align="centre", max_width=W - 2 * MARGIN)


def hint(frame, s, align="right", colour=MUTED):
    """One quiet line of keys, on a small panel in the bottom corner."""
    W = width_of(frame)
    x = W - MARGIN if align == "right" else MARGIN
    pill(frame, s, x, BOTTOM - 30, colour, size=0.45, bold=False, align=align, max_width=W / 2 - 24)
