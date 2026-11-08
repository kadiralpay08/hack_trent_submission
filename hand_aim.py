"""Tracking mode: while the glove button is held, the RIGHT hand steers the mouse like a joystick.

Where the right hand was when the button went down is the centre. From there:
  hand right / left  -> mouse right / left
  hand up / down     -> mouse up / down
Two styles, chosen in controls_editor.py (the "aim" settings in the controls file):
  speed     like a thumbstick: the further the hand is from the centre, the faster the view turns, and
            it keeps turning while the hand stays out. A small dead zone keeps a still hand from drifting.
  position  like a trackpad: the view follows the hand. Move it 10 cm and the view turns a fixed amount.
While the button is held, punches use the "tracking" set of controls (by default jab = left click,
hook = right click, uppercut = middle click), and the camera's BLOCK is off unless the settings say
otherwise, because a steering hand sits up near the face.

The hand position comes from the camera: the right wrist relative to the right shoulder, in shoulder
widths, in the body's own frame. So ducking or slipping moves the whole body and does not move the aim.
The camera updates about 30 times a second; AimDriver sends the mouse movement 250 times a second, so
it moves smoothly instead of in steps.
"""
import threading
import time

import numpy as np

L_SH, R_SH, R_WR = 11, 12, 16
FULL = 0.5          # shoulder widths from the centre that count as "full stick"
SMOOTH_S = 0.05     # position mode: how quickly the mouse catches up with the hand
MOVE_HZ = 250


def hand_offset(world, vis, min_visibility=0.5):
    """The right wrist relative to the right shoulder as (right, up) in shoulder widths, or None when
    the camera cannot see it well. world is MediaPipe's 33 x 3 world landmarks (y points down)."""
    if world is None or vis is None or vis[R_WR] < min_visibility:
        return None
    across = world[R_SH] - world[L_SH]
    width = np.linalg.norm(across)
    if width < 1e-6:
        return None
    right = np.array([across[0], 0.0, across[2]])
    right /= np.linalg.norm(right) + 1e-9
    rel = (world[R_WR] - world[R_SH]) / width
    return np.array([rel @ right, -rel[1]])


class Aim:
    """Turns hand offsets into mouse movement. Thread-safe: update() from the camera loop, take() from
    the driver thread."""

    def __init__(self, settings):
        self.settings = dict(settings)
        self.lock = threading.Lock()
        self.active = False
        self.centre = None
        self.offset = None        # latest hand position relative to the centre, shoulder widths
        self.velocity = np.zeros(2)   # speed mode, pixels per second (screen: +x right, +y down)
        self.target = np.zeros(2)     # position mode, pixels from where the mouse was at the start
        self.sent = np.zeros(2)       # position mode, pixels already sent
        self.carry = np.zeros(2)      # fractions of a pixel not sent yet

    def start(self, hand):
        with self.lock:
            self.active = hand is not None
            self.centre = None if hand is None else np.array(hand, dtype=float)
            self.offset = np.zeros(2) if self.active else None
            self.velocity[:] = 0
            self.target[:] = 0
            self.sent[:] = 0
            self.carry[:] = 0

    def stop(self):
        with self.lock:
            self.active, self.centre, self.offset = False, None, None
            self.velocity[:] = 0

    def _screen(self, d):
        """Hand offset (right, up) -> screen direction (+x right, +y down), with the settings applied."""
        s = self.settings
        flip = -1.0 if s.get("invert_y") else 1.0
        return np.array([d[0], -d[1] * s.get("vertical_scale", 1.0) * flip])

    def update(self, hand):
        """Call every camera frame with hand_offset(), or None if the hand is not visible."""
        with self.lock:
            if not self.active:
                return
            if self.centre is None:          # the button went down while the hand was out of view
                if hand is not None:
                    self.centre = np.array(hand, dtype=float)
                return
            if hand is None:                 # lost the hand: stop turning, keep the position target
                self.velocity[:] = 0
                return
            d = np.array(hand, dtype=float) - self.centre
            self.offset = d
            s = self.settings
            if s.get("mode", "speed") == "position":
                self.target = self._screen(d) * s.get("position_gain", 1500)
            else:
                size = float(np.linalg.norm(d))
                dz = s.get("dead_zone", 0.06)
                if size <= dz:
                    self.velocity[:] = 0
                else:
                    push = min((size - dz) / FULL, 1.5) ** s.get("curve", 1.5)
                    self.velocity = self._screen(d / size) * push * s.get("speed", 1200)

    def take(self, dt):
        """Whole pixels to move now, after dt seconds. Fractions carry over to the next call."""
        with self.lock:
            if not self.active:
                return 0, 0
            if self.settings.get("mode", "speed") == "position":
                step = (self.target - self.sent) * (1 - np.exp(-dt / SMOOTH_S))
                self.sent += step
            else:
                step = self.velocity * dt
            step = step + self.carry
            whole = np.trunc(step)
            self.carry = step - whole
            return int(whole[0]), int(whole[1])


class AimDriver:
    """Sends the Aim's movement to the game 250 times a second, on its own thread."""

    def __init__(self, aim, game):
        self.aim, self.game = aim, game
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        last = time.perf_counter()
        while not self.stop_flag.wait(1 / MOVE_HZ):
            now = time.perf_counter()
            dx, dy = self.aim.take(now - last)
            last = now
            if dx or dy:
                self.game.move(dx, dy)

    def close(self):
        self.stop_flag.set()


class Tracking:
    """Glue for punch_live.py: watches the glove button, switches the game to the tracking set, and
    feeds the right hand to the Aim. Call poll() every camera frame."""

    def __init__(self, sensors, game, log=print):
        self.sensors, self.game, self.log = sensors, game, log
        self.aim = Aim(game.mapping.aim)
        self.driver = AimDriver(self.aim, game)
        self.held = False

    def set_settings(self, settings):
        self.aim.settings = dict(settings)

    def poll(self):
        held = bool(self.sensors.button) and self.sensors.glove_ok()
        hand = self._hand()
        if held != self.held:
            self.held = held
            self.game.set_tracking(held)
            if held:
                self.aim.start(hand)
                self.log(f"TRACKING ON ({self.aim.settings.get('mode', 'speed')} steering)"
                         + ("" if hand is not None else ": right hand not in view yet"))
            else:
                self.aim.stop()
                self.log("tracking off")
        if held:
            self.aim.update(hand)

    def _hand(self):
        latest = self.sensors.latest_pose()
        if latest is None or time.time() - latest[0] > 0.3:
            return None
        return hand_offset(latest[1], latest[2])

    def draw(self, frame, put, x=None, y=168, size=104):
        """A small joystick panel on the right of the camera image while tracking, under the main card. x, y and size
        are in design pixels (a picture 480 high); put is kept so callers need not change."""
        if not self.held:
            return
        import cv2
        import hud
        h, w = frame.shape[:2]
        k = h / 480
        W = hud.width_of(frame)
        x = W - size - hud.MARGIN if x is None else x
        hud.card(frame, x, y, size, size, accent=hud.GOOD, radius=8)
        c = (int((x + size / 2) * k), int((y + size / 2) * k))
        half = size / 2 * k - 10 * k       # keep the stick inside the panel
        dz = int(self.aim.settings.get("dead_zone", 0.06) / FULL * half)
        if self.aim.settings.get("mode", "speed") == "speed" and dz > 0:
            cv2.circle(frame, c, dz, hud.MUTED, 1, cv2.LINE_AA)
        offset = self.aim.offset
        if offset is not None:
            # the camera image is mirrored for display, so the boxer's right is on the right of the picture
            px = int(np.clip(offset[0] / FULL, -1, 1) * half)
            py = int(np.clip(-offset[1] / FULL, -1, 1) * half)
            cv2.line(frame, c, (c[0] + px, c[1] + py), hud.GOOD, max(2, round(2 * k)), cv2.LINE_AA)
            cv2.circle(frame, (c[0] + px, c[1] + py), max(4, round(6 * k)), hud.GOOD, -1, cv2.LINE_AA)
        else:
            hud.text(frame, "no hand", x + size / 2, y + size / 2 + 5, 0.5, hud.BAD, True, align="centre")
        hud.text(frame, f"TRACKING ({self.aim.settings.get('mode', 'speed')})", x + size / 2, y + size - 8, 0.4, hud.MUTED,
                 True, align="centre", max_width=size - 12)

    def close(self):
        self.driver.close()
        self.game.set_tracking(False)
