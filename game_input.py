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
}
EXTENDED_KEYS = {"up", "down", "left", "right"}

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
INPUT_KEYBOARD = 1

if sys.platform == "win32":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _KeyBdInput(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _MouseInput(ctypes.Structure):  # unused, but it sets the size of the INPUT union
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
    """Return the key name in lower case, or raise if there is no scan code for it."""
    key = key.lower()
    if key not in SCAN_CODES:
        raise ValueError(f"unknown key {key!r}; known keys: {', '.join(sorted(SCAN_CODES))}")
    return key


class Keyboard:
    """Remembers which keys are down, so none can be left stuck down."""

    def __init__(self):
        self.down = set()
        self.lock = threading.RLock()  # taps release from a timer thread

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

    def _send(self, key, is_down):
        print(f"    key {'DOWN' if is_down else 'UP  '} {key}")


class SendInputKeyboard(Keyboard):
    """Presses real keys on Windows. Input goes to whichever window has focus."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("live key presses are only supported on Windows")
        super().__init__()

    def _send(self, key, is_down):
        flags = KEYEVENTF_SCANCODE
        if key in EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        event = _Input(type=INPUT_KEYBOARD, u=_InputUnion(ki=_KeyBdInput(0, SCAN_CODES[key], flags, 0, 0)))
        if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())


class Mapping:
    """Which key each event presses. Loaded from a JSON file in mappings/."""

    def __init__(self, data):
        self.game = data.get("game", "")
        self.tap_hold = data.get("tap_hold_ms", 50) / 1000
        self.taps = {name.upper(): check_key(key) for name, key in data.get("taps", {}).items()}
        self.holds = {name.upper(): check_key(key) for name, key in data.get("holds", {}).items()}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))


class GameInput:
    """Call punch() when a punch lands and set_states() every frame with the current body states."""

    def __init__(self, mapping, keyboard):
        self.mapping, self.keyboard = mapping, keyboard
        self.enabled = True
        self._state_keys = set()  # keys currently held because of a body state
        atexit.register(self.close)  # normal exit or an uncaught error; a hard kill can still leave keys down

    def punch(self, kind):
        """Tap the key mapped to this punch. Returns True if a key was sent."""
        key = self.mapping.taps.get(kind.upper())
        if key is None or not self.enabled or key in self.keyboard.down:
            return False
        self.keyboard.press(key)
        timer = threading.Timer(self.mapping.tap_hold, self.keyboard.release, (key,))
        timer.daemon = True
        timer.start()
        return True

    def set_states(self, states):
        """states = the labels active right now, e.g. {"BLOCK", "SLIP LEFT"}. Keys follow the changes."""
        wanted = set()
        if self.enabled:
            wanted = {self.mapping.holds[s.upper()] for s in states if s.upper() in self.mapping.holds}
        for key in self._state_keys - wanted:
            self.keyboard.release(key)
        for key in wanted - self._state_keys:
            self.keyboard.press(key)
        self._state_keys = wanted

    def set_enabled(self, on):
        """Switch key output on or off. Turning it off lets go of every key."""
        self.enabled = on
        if not on:
            self.set_states(())
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
