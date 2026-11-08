"""Webcam pose tracking: skeleton overlay, guard/dodge state and punch types.

Punches are measured in a body frame (forward / sideways / up) built from MediaPipe's
3D world landmarks, so they work at any angle to the camera.
Keys: c = calibrate or recalibrate (press it, then get in guard: it waits a few seconds), a/d = rotate the
3D view, q = quit.
"""
import math
import os
import time
import urllib.request
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

import overlay

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "pose_landmarker_full.task")

NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP = 0, 11, 12, 13, 14, 15, 16, 23, 24
CONNECTIONS = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28), (0, 11), (0, 12),
]

# Tunables. Distances are in shoulder-widths so they work at any range.
GUARD_DIST = 1.0        # wrist this close to the nose => hand is up
GUARD_DROP = 0.15       # GUARD DOWN needs both wrists this far below their elbows (shoulder widths): arms hanging
DUCK_DROP = 0.45        # nose below baseline by this much => duck
SLIP_SHIFT = 0.5        # nose sideways from baseline by this much => slip
PUNCH_SPEED = 4.0       # wrist speed (shoulder-widths/s) that starts a punch
MIN_TRAVEL = 0.4        # wrist travel (shoulder-widths) needed to call a punch type
PUNCH_COOLDOWN = 0.25   # seconds
LEAD = "L"              # lead hand throws the jab (orthodox = "L")
CALIBRATE_DELAY_S = 5.0  # seconds from pressing C (or F7) to calibrating, to get into guard first
BOTTOM_RESERVED = 70     # pixels at the bottom of the camera image kept for the calibration messages


def set_fullscreen(on, window="Pose"):
    """Switch the camera window between full screen and a normal window."""
    cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN if on else cv2.WINDOW_NORMAL)


def ensure_model():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print("Downloading pose model...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)


class Smoother:
    """Exponential moving average over a landmark array."""

    def __init__(self, alpha):
        self.alpha, self.prev = alpha, None

    def __call__(self, x):
        self.prev = x if self.prev is None else self.alpha * x + (1 - self.alpha) * self.prev
        return self.prev


def punch_travel(delta, start, end, world, sw):
    """Per hand (extension, right, up) in shoulder-widths.

    delta = wrist movement relative to the shoulder; start/end = wrist-to-shoulder vectors.
    Extension is how much the arm straightened, so it needs no facing direction.
    """
    right = world[R_SH] - world[L_SH]
    right[1] = 0
    right /= np.linalg.norm(right) + 1e-6
    extension = (np.linalg.norm(end, axis=1) - np.linalg.norm(start, axis=1)) / sw
    return np.stack([extension, delta @ right, -delta[:, 1]], axis=1)


def classify_punch(side, travel, from_low):
    """travel = (extension, right, up); from_low = wrist started below the elbow.

    An uppercut is an upward sweep from a low hand. None = too small or a retraction.
    """
    extension, right, up = travel
    if extension < -MIN_TRAVEL:
        return None  # arm folding back to guard
    inward = right if side == "L" else -right  # toward the body's centre line
    moves = {"STRAIGHT": extension, "HOOK": inward}
    if from_low:
        moves["UPPERCUT"] = up
    kind, amount = max(moves.items(), key=lambda kv: kv[1])
    if amount < MIN_TRAVEL:
        return None  # too small, or a retraction (negative travel)
    return ("JAB" if side == LEAD else "CROSS") if kind == "STRAIGHT" else kind


def body_state(img, world, sw_world, baseline):
    """Guard / dodge labels from the current pose."""
    states = []
    if baseline:
        drop = (img[NOSE][1] - baseline["nose"][1]) / baseline["sw"]
        shift = (img[NOSE][0] - baseline["nose"][0]) / baseline["sw"]
        if drop > DUCK_DROP:
            states.append("DUCK")
        if abs(shift) > SLIP_SHIFT:
            states.append("SLIP " + ("RIGHT" if shift > 0 else "LEFT"))
    up = [np.linalg.norm(world[i] - world[NOSE]) / sw_world < GUARD_DIST for i in (L_WR, R_WR)]
    if all(up):
        states.append("BLOCK")
    elif not any(up) and hands_dropped(world, sw_world):
        states.append("GUARD DOWN")
    return states


def hands_dropped(world, sw_world):
    """Both arms really down: each wrist below its elbow (world y points down). Hands that only leave the face,
    as when loading up or throwing a punch, keep the wrist at or above the elbow, so they don't count."""
    return all((world[wr][1] - world[el][1]) / sw_world > GUARD_DROP for wr, el in ((L_WR, L_EL), (R_WR, R_EL)))


def draw(frame, img, vis, flash, now):
    h, w = frame.shape[:2]
    px = (img * [w, h]).astype(int)
    for a, b in CONNECTIONS:
        if vis[a] > 0.4 and vis[b] > 0.4:
            cv2.line(frame, tuple(px[a]), tuple(px[b]), (0, 255, 0), 3)
    for i in (NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR):
        cv2.circle(frame, tuple(px[i]), 6, (0, 200, 255), -1)
    for side, idx in (("L", L_WR), ("R", R_WR)):
        text, t = flash[side]
        if now - t < 0.6:
            overlay.outlined_text(frame, text, px[idx][0] + 10, px[idx][1] - 10, 0.9, (0, 0, 255), 2)


def draw_slips(frame, img, vis, states, state_keys=None):
    """SLIP LEFT / SLIP RIGHT beside the torso, on the side the boxer moved to, with the key it is pressing."""
    if not any(s.startswith("SLIP") for s in states) or min(vis[L_SH], vis[R_SH]) < 0.4:
        return
    h, w = frame.shape[:2]
    k = h / 480
    px = img * [w, h]
    body = [i for i in (L_SH, R_SH, L_HIP, R_HIP) if vis[i] > 0.4]
    y = float(np.mean([px[i][1] for i in body]))
    edges = {"SLIP LEFT": (min(px[i][0] for i in body) - 25 * k, "right"),
             "SLIP RIGHT": (max(px[i][0] for i in body) + 25 * k, "left")}
    for state, (x, align) in edges.items():
        if state not in states:
            continue
        x = min(max(x, 10 * k), w - 10 * k)
        overlay.outlined_text(frame, state, x, y, 1.1 * k, (0, 255, 255), 3 * k, max_width=w / 2.5, align=align)
        key = state_keys(state) if state_keys else None
        if key:
            overlay.outlined_text(frame, f"key {str(key).upper()}", x, y + 45 * k, 1.3 * k, (0, 255, 0), 3 * k,
                                  align=align)


def level(world):
    """Rotate the skeleton so the torso is vertical, undoing camera pitch and roll."""
    torso = (world[L_SH] + world[R_SH]) / 2 - (world[L_HIP] + world[R_HIP]) / 2
    a, b = torso / (np.linalg.norm(torso) + 1e-6), np.array([0.0, -1.0, 0.0])
    v, c = np.cross(a, b), np.dot(a, b)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return world @ (np.eye(3) + k + k @ k / (1 + c)).T


def draw_3d(world, vis, yaw, size=480, scale=250):
    """Orthographic render of the world-space skeleton, rotated by yaw around the vertical axis."""
    canvas = np.zeros((size, size, 3), np.uint8)
    if min(vis[L_HIP], vis[R_HIP]) > 0.5:  # need the hips to find the torso
        world = level(world)
    c, s = np.cos(yaw), np.sin(yaw)
    x = -(world[:, 0] * c + world[:, 2] * s)  # negated to match the mirrored camera view
    px = (np.stack([x, world[:, 1]], axis=1) * scale + [size / 2, size * 0.55]).astype(int)
    for a, b in CONNECTIONS:
        if vis[a] > 0.4 and vis[b] > 0.4:
            cv2.line(canvas, tuple(px[a]), tuple(px[b]), (0, 255, 0), 3)
    for i in (NOSE, L_SH, R_SH, L_EL, R_EL):
        cv2.circle(canvas, tuple(px[i]), 6, (0, 200, 255), -1)
    for i in (L_WR, R_WR):
        cv2.circle(canvas, tuple(px[i]), 10, (0, 0, 255), -1)
    return canvas


def screen_size():
    """(width, height) of the main screen in pixels. Windows only; elsewhere a safe guess."""
    try:
        import ctypes
        return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except (AttributeError, OSError):
        return 1280, 720


def window_size(name, frame):
    """(width, height) the window shows its picture at, for drawing text at that size."""
    try:
        _, _, w, h = cv2.getWindowImageRect(name)
    except cv2.error:
        w = h = 0
    return (w, h) if w > 0 and h > 0 else (frame.shape[1], frame.shape[0])


def fit_window(name, frame_w, frame_h):
    """Make the named window as large as the screen allows (leaving room for title bar and taskbar),
    keeping the picture's shape."""
    screen_w, screen_h = screen_size()
    scale = min(screen_w * 0.98 / frame_w, screen_h * 0.85 / frame_h)
    cv2.resizeWindow(name, int(frame_w * scale), int(frame_h * scale))
    cv2.moveWindow(name, int((screen_w - frame_w * scale) / 2), 0)


def main(on_punch=None, on_states=None, on_frame=None, want_calibrate=None, on_calibrated=None,
         on_features=None, on_draw=None, builtin_punches=True, camera_index=0, on_pose=None,
         big_window=False, show_3d=True, calibrate_delay=CALIBRATE_DELAY_S, fullscreen=False, state_keys=None,
         on_key=None):
    """Run the tracker. Run on its own it only draws; the callbacks let other code react.

    on_punch(kind, side, speed): a punch was recognised. kind is JAB, CROSS, HOOK or UPPERCUT.
    on_states(states): called every frame with the body-state labels active right now (BLOCK, DUCK,
        SLIP LEFT, SLIP RIGHT, GUARD DOWN). It gets an empty list when nobody is in view.
    on_frame(): called every frame; return True to stop the tracker.
    want_calibrate(): called every frame; return True to request a calibration. It happens
        calibrate_delay seconds later (CALIBRATE_DELAY_S, 5 s), with a countdown on screen, so the boxer
        can get into guard. A second request during the wait restarts it. This lets a hotkey recalibrate
        while another window (the game) has focus. The c key in the camera window always works too.
    on_calibrated(): called when the calibration actually happens, after the countdown.
    on_features(now, speeds, travel, from_low): the raw numbers the punch decision uses, every frame.
        speeds = [left, right] wrist speed; travel = 2x3 array of (extension, right, up) per hand;
        from_low = [left, right] wrist started below the elbow. It may return a list of (side, kind)
        punches it decided; those are shown on screen and passed to on_punch.
    on_draw(frame): draw extra text on the camera image before it is shown.
    builtin_punches: set False to switch off this file's own punch check, so only the punches
        returned by on_features count (see punch_detector.py).
    camera_index: which camera to open (0 is the default webcam).
    on_pose(world, visibility): called every frame a body is found, with the 33 smoothed 3D world
        landmarks (metres, origin at the hips) and how visible each one is.
    big_window: size the camera window as large as the screen allows, for reading from across the room.
        Ignored when fullscreen is on.
    show_3d: set False to skip the 3D skeleton window.
    calibrate_delay: seconds to wait between a calibrate request and the calibration itself (0 = at once).
    fullscreen: show the camera window full screen. set_fullscreen() switches it while running.
    on_key(key): called with each key pressed in the camera window (an 8-bit code) before this function's own
        shortcuts (c, a, d, q); return True to use it up, so typing text doesn't calibrate or quit.
    state_keys(state): the key a defence state (such as SLIP LEFT) is pressing right now, or None. Slips are
        shown beside the torso, with this key under them.
    Layout: this function draws its calibration messages in the bottom BOTTOM_RESERVED pixels of the
    camera image; on_draw should keep its own text above that, so nothing covers them.
    """
    ensure_model()
    landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    ))
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise SystemExit("Could not open webcam")

    smooth_img, smooth_world = Smoother(0.6), Smoother(0.7)
    yaw = 0.0  # 3D view rotation
    cv2.namedWindow("Pose", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)  # resizable, so it can go full screen
    if fullscreen:
        set_fullscreen(True)
    if show_3d:
        cv2.namedWindow("3D skeleton")
        cv2.moveWindow("3D skeleton", 700, 50)  # beside the camera window, not on top of it
    sized = fullscreen or not big_window  # the big window is sized once the first frame shows the camera's resolution
    history = deque(maxlen=5)  # (time, wrist-relative-to-shoulder, wrist-below-elbow), ~0.2 s
    baseline = None
    calibrated_at = -10.0  # when the last calibration happened, for the on-screen flash
    calibrate_at = None    # when a requested calibration will happen, or None if none is waiting
    last_punch = {"L": 0.0, "R": 0.0}
    flash = {"L": ("", 0.0), "R": ("", 0.0)}
    t0 = time.time()

    failed_reads = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            # Some webcams return empty frames for the first moment after opening. Wait before giving up.
            failed_reads += 1
            if failed_reads > 150:  # about 3 seconds of nothing
                break
            time.sleep(0.02)
            continue
        failed_reads = 0
        now = time.time()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int((now - t0) * 1000))
        frame = cv2.flip(frame, 1)  # mirror for display only; image x is flipped to match
        overlay.begin_layer(frame)  # text on this picture is drawn at the window's real size, so it stays sharp
        lines = ["no body detected"]

        if result.pose_landmarks:
            lm = result.pose_landmarks[0]
            img = smooth_img(np.array([[1 - p.x, p.y] for p in lm]))
            vis = np.array([p.visibility for p in lm])
            world = smooth_world(np.array([[p.x, p.y, p.z] for p in result.pose_world_landmarks[0]]))
            sw = np.linalg.norm(world[L_SH] - world[R_SH]) + 1e-6
            if on_pose:
                on_pose(world, vis)

            wrist_below_elbow = world[[L_WR, R_WR], 1] > world[[L_EL, R_EL], 1]  # world y points down
            history.append((now, world[[L_WR, R_WR]] - world[[L_SH, R_SH]], wrist_below_elbow))
            dt = now - history[0][0]
            speeds = [0.0, 0.0]
            if dt > 0:
                delta = (history[-1][1] - history[0][1]) / sw
                travel = punch_travel(delta, history[0][1], history[-1][1], world, sw)
                for i, side in enumerate("LR"):
                    speeds[i] = np.linalg.norm(delta[i]) / dt
                    if builtin_punches and speeds[i] > PUNCH_SPEED and now - last_punch[side] > PUNCH_COOLDOWN:
                        kind = classify_punch(side, travel[i], history[0][2][i])
                        ext, right, up = travel[i]
                        print(f"{side} {kind} speed={speeds[i]:.1f} extension={ext:.2f} right={right:.2f} up={up:.2f} low={history[0][2][i]}")
                        if kind:
                            last_punch[side] = now
                            flash[side] = (f"{kind} {speeds[i]:.1f}", now)
                            if on_punch:
                                on_punch(kind, side, speeds[i])
                if on_features:
                    for side, kind in on_features(now, list(speeds), travel, list(history[0][2])) or ():
                        flash[side] = (kind, now)
                        if on_punch:
                            on_punch(kind, side, speeds["LR".index(side)])

            states = body_state(img, world, sw, baseline)
            if on_states:
                on_states(states)
            lines = [" | ".join(states) or "ok",
                     f"speed L {speeds[0]:.1f}  R {speeds[1]:.1f}"]
            draw(frame, img, vis, flash, now)
            draw_slips(frame, img, vis, states, state_keys)
            if show_3d:
                cv2.imshow("3D skeleton", draw_3d(world, vis, yaw))
        else:
            if show_3d:
                cv2.imshow("3D skeleton", np.zeros((480, 480, 3), np.uint8))
            if on_states:
                on_states([])  # nobody in view: let go of any held keys

        for n, text in enumerate(lines):
            overlay.outlined_text(frame, text, 10, 30 + 30 * n, 0.8, (255, 255, 255), 2)
        if baseline is None:
            overlay.outlined_text(frame, f"press C, then get in guard: it calibrates {calibrate_delay:g} s later",
                                  10, frame.shape[0] - 15, 0.6, (0, 255, 255), 2)
        else:
            if now - calibrated_at < 1.5:
                overlay.outlined_text(frame, "CALIBRATED", 10, frame.shape[0] - 45, 1.0, (0, 255, 0), 3)
            overlay.outlined_text(frame, "C = recalibrate", 10, frame.shape[0] - 15, 0.5, (255, 255, 255), 1)
        if calibrate_at is not None:  # big enough to read from a few metres, over the top of everything else
            h, w = frame.shape[:2]
            k = h / 480
            overlay.outlined_text(frame, "GET IN GUARD", w / 2, h / 2 - 50 * k, 2.0 * k, (0, 255, 255), 4 * k,
                                  max_width=w - 40 * k, align="centre")
            overlay.outlined_text(frame, str(max(1, math.ceil(calibrate_at - now))), w / 2, h / 2 + 110 * k,
                                  6.0 * k, (0, 255, 255), 10 * k, align="centre")
        if on_draw:
            on_draw(frame)
        cv2.imshow("Pose", overlay.finish_layer(frame, *window_size("Pose", frame)))
        if not sized:
            fit_window("Pose", frame.shape[1], frame.shape[0])
            sized = True
        if on_frame and on_frame():
            break
        key = cv2.waitKey(1) & 0xFF
        if key != 255 and on_key and on_key(key):
            key = 255  # the tool used it (for example typing a name), so it is not a shortcut here
        if key == ord("c") or bool(want_calibrate and want_calibrate()):
            # Design rule (PROJECT.md): calibration waits calibrate_delay after the request, so the boxer can
            # get into guard after pressing the key, or after clicking back to the game for F7.
            # A repeat press while waiting restarts the wait.
            calibrate_at = now + calibrate_delay
            if calibrate_delay > 0:
                print(f"Calibrating in {calibrate_delay:g} s: get into your guard")
        if calibrate_at is not None and now >= calibrate_at:
            calibrate_at = None
            if result.pose_landmarks:
                baseline = {"nose": img[NOSE].copy(), "sw": np.linalg.norm(img[L_SH] - img[R_SH])}
                calibrated_at = now
                print("Calibrated")
                if on_calibrated:
                    on_calibrated()
            else:
                print("Calibration skipped: no body in view. Press C to try again")
        elif key in (ord("a"), ord("d")):
            yaw += 0.15 if key == ord("d") else -0.15
        elif key == ord("q"):
            break

    if on_states:
        on_states([])
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
