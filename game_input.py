"""Turn boxing events into keyboard input, so existing games can be played with your body.

Two kinds of key output:
  tap  - a punch. The key is pressed, then released a few milliseconds later. Games read the key
         state once per frame, so a press that is released instantly can be missed.
  hold - a body state (block, duck, slip). The key stays down while the state lasts.

Keys are sent as hardware scan codes with the Windows SendInput call. Browsers and DirectInput games
accept these; many games ignore the plain virtual-key events that most keyboard libraries send.

The event names are the labels pose_tracker.py already produces: JAB, CROSS, HOOK, UPPERCUT for
punches and BLOCK, DUCK, SLIP LEFT, SLIP RIGHT, GUARD DOWN for body states. A mapping file (see
mappings/) says which key each one presses.

Run `python game_input.py` for a dry run that only prints. `--live` really presses keys.
"""
import argparse
import atexit
import ctypes
import json
import os
import sys
import threading
import time

# Hardware scan codes (set 1). Arrow keys are "extended" keys and need the extended flag.
SCAN_CODES = {
    **dict(zip("qwertyuiop", range(0x10, 0x1A))),
    **dict(zip("asdfghjkl", range(0x1E, 0x27))),
    **dict(zip("zxcvbnm", range(0x2C, 0x33))),
    **dict(zip("1234567890", range(0x02, 0x0C))),
    "space": 0x39, "enter": 0x1C, "esc": 0x01, "tab": 0x0F,
    "shift": 0x2A, "ctrl": 0x1D, "alt": 0x38,
    "comma": 0x33, "period": 0x34, "semicolon": 0x27,
    "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
    "minus": 0x0C, "equals": 0x0D, "backspace": 0x0E, "bracketleft": 0x1A, "bracketright": 0x1B,
    "quote": 0x28, "grave": 0x29, "backslash": 0x2B, "slash": 0x35, "capslock": 0x3A,
    **{f"f{n}": 0x3B + n - 1 for n in range(1, 11)}, "f11": 0x57, "f12": 0x58,
    "insert": 0x52, "delete": 0x53, "home": 0x47, "end": 0x4F, "pageup": 0x49, "pagedown": 0x51,
}
EXTENDED_KEYS = {"up", "down", "left", "right", "insert", "delete", "home", "end", "pageup", "pagedown"}

# Mouse buttons can be bound like keys: (down flag, up flag) for SendInput.
MOUSE_BUTTONS = {"mouse_left": (0x0002, 0x0004), "mouse_right": (0x0008, 0x0010), "mouse_middle": (0x0020, 0x0040)}
MOUSEEVENTF_MOVE = 0x0001  # relative movement, which is what games read for looking around

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1

if sys.platform == "win32":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _KeyBdInput(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _MouseInput(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _HardwareInput(ctypes.Structure):  # unused, same reason
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]

    class _Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT


def check_key(key):
    """Return the key (or mouse button) name in lower case, or raise if it cannot be sent."""
    key = key.lower()
    if key not in SCAN_CODES and key not in MOUSE_BUTTONS:
        raise ValueError(f"unknown key {key!r}; known keys: {', '.join(sorted(SCAN_CODES))}, "
                         f"and the mouse buttons {', '.join(MOUSE_BUTTONS)}")
    return key


class Keyboard:
    """Remembers which keys (and mouse buttons) are down, so none can be left stuck down. Also moves
    the mouse, for looking around."""

    def __init__(self):
        self.down = set()
        self.lock = threading.RLock()  # taps release from a timer thread

    def move(self, dx, dy):
        """Move the mouse by dx, dy pixels (counts), relative to where it is. +dx right, +dy down."""
        if dx or dy:
            self._move(int(dx), int(dy))

    def _move(self, dx, dy):
        raise NotImplementedError

    def press(self, key):
        with self.lock:
            if key not in self.down:
                self._send(key, True)
                self.down.add(key)

    def release(self, key):
        with self.lock:
            if key in self.down:
                self._send(key, False)
                self.down.discard(key)

    def release_all(self):
        with self.lock:
            for key in list(self.down):
                self.release(key)

    def _send(self, key, is_down):
        raise NotImplementedError


class DryRunKeyboard(Keyboard):
    """Prints instead of pressing anything."""

    def __init__(self):
        super().__init__()
        self._moved, self._moved_since = [0, 0], time.time()

    def _send(self, key, is_down):
        print(f"    key {'DOWN' if is_down else 'UP  '} {key}")

    def _move(self, dx, dy):  # moves come hundreds of times a second, so print a total once a second
        self._moved[0] += dx
        self._moved[1] += dy
        if time.time() - self._moved_since >= 1.0:
            print(f"    mouse moved {self._moved[0]:+d}, {self._moved[1]:+d} in the last second")
            self._moved, self._moved_since = [0, 0], time.time()


def release_keys_when_console_ends(keyboard):
    """Release every held key if the console is closed, Ctrl+C or Ctrl+Break arrives, or Windows shuts down.

    atexit does not run when the console window is closed, and a key left pressed would stay down in
    the operating system until you tapped it again. Windows only.
    """
    if sys.platform != "win32":
        return
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(event):
        keyboard.release_all()
        return False  # carry on with Windows' normal handling, which ends the program

    keyboard._console_handler = handler_type(handler)  # keep a reference so it is not garbage collected
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetConsoleCtrlHandler.argtypes = (handler_type, wintypes.BOOL)
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    if not kernel32.SetConsoleCtrlHandler(keyboard._console_handler, True):
        raise ctypes.WinError(ctypes.get_last_error())


class SendInputKeyboard(Keyboard):
    """Presses real keys on Windows. Input goes to whichever window has focus."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("live key presses are only supported on Windows")
        super().__init__()
        release_keys_when_console_ends(self)

    def _send(self, key, is_down):
        if key in MOUSE_BUTTONS:
            flag = MOUSE_BUTTONS[key][0 if is_down else 1]
            event = _Input(type=INPUT_MOUSE, u=_InputUnion(mi=_MouseInput(0, 0, 0, flag, 0, 0)))
        else:
            flags = KEYEVENTF_SCANCODE
            if key in EXTENDED_KEYS:
                flags |= KEYEVENTF_EXTENDEDKEY
            if not is_down:
                flags |= KEYEVENTF_KEYUP
            event = _Input(type=INPUT_KEYBOARD, u=_InputUnion(ki=_KeyBdInput(0, SCAN_CODES[key], flags, 0, 0)))
        if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def _move(self, dx, dy):
        event = _Input(type=INPUT_MOUSE, u=_InputUnion(mi=_MouseInput(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)))
        if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())


# The left arm's JAB, HOOK and UPPERCUT, and the right arm's CROSS. The right arm's own HOOK and UPPERCUT are
# "RIGHT HOOK" and "RIGHT UPPERCUT" here (punch_session.Arm.action), so all six punches can have their own keys.
PUNCHES = ("JAB", "HOOK", "UPPERCUT", "CROSS", "RIGHT HOOK", "RIGHT UPPERCUT")
DEFENCE = ("BLOCK", "DUCK", "SLIP LEFT", "SLIP RIGHT", "GUARD DOWN")
# How the right hand steers the mouse while the glove button is held (see hand_aim.py).
AIM_DEFAULTS = {
    "mode": "speed",          # "speed": like a thumbstick; "position": like a trackpad
    "speed": 1200,            # speed mode: pixels per second with the hand half a shoulder width off centre
    "curve": 1.5,             # speed mode: 1 = straight line; higher = finer near the centre, faster at the edge
    "dead_zone": 0.06,        # speed mode: how far (shoulder widths) the hand can wander without moving the mouse
    "position_gain": 1500,    # position mode: pixels per shoulder width of hand movement
    "vertical_scale": 1.0,    # up/down movement relative to left/right
    "invert_y": False,
    "block_while_tracking": False,  # the camera's BLOCK, off while steering by default (the hand is up near the face)
}


def _binds(section, default_ms):
    """{"JAB": "j"} or {"JAB": {"bind": "mouse_left", "hold_ms": 150}} -> ({name: key}, {name: seconds}).
    An empty bind leaves the action unbound."""
    keys, hold = {}, {}
    for name, value in section.items():
        if not isinstance(value, dict):
            value = {"bind": value}
        if value.get("bind"):
            keys[name.upper()] = check_key(value["bind"])
            hold[name.upper()] = value.get("hold_ms", default_ms) / 1000
    return keys, hold


class Mapping:
    """Which key or mouse button each event presses. Loaded from a JSON file in mappings/.

    Three sets, like a game's controls menu (edit them with controls_editor.py):
      normal    punches while the glove button is up             {"JAB": {"bind": "j", "hold_ms": 50}}
      tracking  punches while the glove button is held, when the right hand steers the mouse
      defence   camera body states, held while they last          {"BLOCK": "w"}
    plus "aim", the steering settings (AIM_DEFAULTS). The older format ("taps" and "holds", as in
    browser_boxing.json) still loads: taps become the normal set and holds the defence set.
    """

    def __init__(self, data):
        self.game = data.get("game", "")
        self.notes = data.get("notes", [])
        default_ms = data.get("tap_hold_ms", 50)
        self.tap_hold = default_ms / 1000
        self.taps, self.tap_s = _binds(data.get("normal", data.get("taps", {})), default_ms)
        self.tracking_taps, self.tracking_s = _binds(data.get("tracking", {}), 150)
        self.holds, _ = _binds(data.get("defence", data.get("holds", {})), 0)
        self.aim = {**AIM_DEFAULTS, **data.get("aim", {})}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def to_dict(self):
        def section(keys, hold):
            return {name: {"bind": key, "hold_ms": round(1000 * hold[name])} for name, key in keys.items()}
        return {"game": self.game, "normal": section(self.taps, self.tap_s),
                "tracking": section(self.tracking_taps, self.tracking_s),
                "defence": dict(self.holds), "aim": dict(self.aim), "notes": self.notes}

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")


class GameInput:
    """Call punch() when a punch lands and set_states() every frame with the current body states.
    set_tracking() switches punches to the tracking set (glove button held); move() moves the mouse."""

    def __init__(self, mapping, keyboard):
        self.mapping, self.keyboard = mapping, keyboard
        self.enabled = True
        self.tracking = False
        self._states = ()
        self._state_keys = set()  # keys currently held because of a body state
        atexit.register(self.close)  # normal exit or an uncaught error; a hard kill can still leave keys down

    def key_for(self, kind):
        """The key or mouse button this punch presses right now, or None."""
        taps = self.mapping.tracking_taps if self.tracking else self.mapping.taps
        return taps.get(kind.upper())

    def punch(self, kind):
        """Press the key mapped to this punch in the current set, for its hold time. Returns True if sent."""
        key = self.key_for(kind)
        if key is None or not self.enabled or key in self.keyboard.down:
            return False
        hold = (self.mapping.tracking_s if self.tracking else self.mapping.tap_s).get(kind.upper(), self.mapping.tap_hold)
        self.keyboard.press(key)
        timer = threading.Timer(hold, self.keyboard.release, (key,))
        timer.daemon = True
        timer.start()
        return True

    def set_states(self, states):
        """states = the labels active right now, e.g. {"BLOCK", "SLIP LEFT"}. Keys follow the changes."""
        self._states = tuple(states)
        wanted = set()
        if self.enabled:
            for s in states:
                s = s.upper()
                if s == "BLOCK" and self.tracking and not self.mapping.aim["block_while_tracking"]:
                    continue  # steering puts the right hand up near the face, which looks like a block
                if s in self.mapping.holds:
                    wanted.add(self.mapping.holds[s])
        for key in self._state_keys - wanted:
            self.keyboard.release(key)
        for key in wanted - self._state_keys:
            self.keyboard.press(key)
        self._state_keys = wanted

    def key_for_state(self, state):
        """The key a defence state is holding down right now (for the screen), or None."""
        key = self.mapping.holds.get(state.upper())
        return key if key in self._state_keys else None

    def set_tracking(self, on):
        """The glove button: held = tracking set and steering, up = normal set."""
        if on != self.tracking:
            self.tracking = on
            self.set_states(self._states)  # a held BLOCK lets go as steering starts

    def move(self, dx, dy):
        if self.enabled:
            self.keyboard.move(dx, dy)

    def set_mapping(self, mapping):
        """Swap in edited controls while running. Held keys are let go first, so none stick."""
        states = self._states
        self.set_states(())
        self.keyboard.release_all()
        self.mapping = mapping
        self.set_states(states)

    def set_enabled(self, on):
        """Switch key output on or off. Turning it off lets go of every key."""
        self.enabled = on
        if not on:
            states = self._states
            self.set_states(())
            self._states = states
            self.keyboard.release_all()

    def close(self):
        self.set_enabled(False)


def demo(game_input):
    """Walk through every mapped punch and body state once."""
    for kind, key in game_input.mapping.taps.items():
        print(f"  punch {kind} -> tap {key}")
        game_input.punch(kind)
        time.sleep(0.4)
    for state, key in game_input.mapping.holds.items():
        print(f"  state {state} -> hold {key} for 0.6 s")
        game_input.set_states({state})
        time.sleep(0.6)
        game_input.set_states(())
        time.sleep(0.2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mapping", default=os.path.join(os.path.dirname(__file__), "mappings", "browser_boxing.json"))
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    args = parser.parse_args()

    mapping = Mapping.load(args.mapping)
    print(f"Game: {mapping.game}")
    if args.live:
        print("LIVE: keys will be sent to the window that has focus. Click the game now.")
        for n in range(5, 0, -1):
            print(f"  starting in {n}...")
            time.sleep(1)
        keyboard = SendInputKeyboard()
    else:
        print("Dry run: printing keys only. Use --live to press them.")
        keyboard = DryRunKeyboard()
    demo(GameInput(mapping, keyboard))
    print("Done.")
