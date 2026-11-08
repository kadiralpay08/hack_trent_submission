"""Edit the boxing controls like a game's keybind menu.

  python controls_editor.py                          edits mappings/controls.json
  python controls_editor.py --mapping other.json     edits another controls file (made if missing)

Click a bind, then press a key or click a mouse button to bind it. Esc cancels; the x clears a bind.
Three sets:
  Normal    punches while the glove button is up
  Tracking  punches while the glove button is held (the right hand steers the mouse)
  Defence   camera body states (block, duck, slip, guard down), held while they last
Hold is how long a punch keeps its key or mouse button down; some games miss very short presses.
Steering: speed (like a thumbstick) or position (like a trackpad), and how fast.

Every change is saved straight away, and a running punch_live.py picks it up within a second, so you
can tune it with the game open. Warnings show binds that clash (two actions on one key while both can
be active) and keys the tools already use as hotkeys.
"""
import argparse
import json
import os
import sys
import time

import game_input as gi

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(ROOT, "mappings", "controls.json")
HOTKEYS = {"f7": "calibrate", "f8": "key output on/off", "f9": "quit", "f11": "full screen",
           "bracketleft": "confidence down", "bracketright": "confidence up", "period": "start/pause"}
TK_KEYS = {
    "space": "space", "return": "enter", "tab": "tab", "backspace": "backspace", "caps_lock": "capslock",
    "shift_l": "shift", "shift_r": "shift", "control_l": "ctrl", "control_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
    "up": "up", "down": "down", "left": "left", "right": "right", "comma": "comma", "period": "period",
    "semicolon": "semicolon", "minus": "minus", "equal": "equals", "bracketleft": "bracketleft",
    "bracketright": "bracketright", "apostrophe": "quote", "grave": "grave", "backslash": "backslash",
    "slash": "slash", "insert": "insert", "delete": "delete", "home": "home", "end": "end",
    "prior": "pageup", "next": "pagedown",
}
TK_MOUSE = {1: "mouse_left", 2: "mouse_middle", 3: "mouse_right"}  # Tk's button numbers on Windows
NICE = {"mouse_left": "Left click", "mouse_right": "Right click", "mouse_middle": "Middle click",
        "bracketleft": "[", "bracketright": "]", "comma": ",", "period": ".", "semicolon": ";", "minus": "-",
        "equals": "=", "quote": "'", "grave": "`", "backslash": "\\", "slash": "/"}
LABELS = {"JAB": "Jab", "HOOK": "Hook", "UPPERCUT": "Uppercut", "CROSS": "Cross (right glove)",
          "BLOCK": "Block", "DUCK": "Duck", "SLIP LEFT": "Slip left", "SLIP RIGHT": "Slip right",
          "GUARD DOWN": "Guard down"}


def key_from_tk(keysym):
    """A Tk key name (event.keysym) -> our key name, or None if it cannot be sent to a game."""
    k = keysym.lower()
    if len(k) == 1 and k in gi.SCAN_CODES:
        return k
    if k.startswith("f") and k[1:].isdigit() and k in gi.SCAN_CODES:
        return k
    return TK_KEYS.get(k)


def nice(key):
    return "-" if not key else NICE.get(key, key.upper() if len(key) == 1 else key.capitalize())


def problems(mapping):
    """Warnings for the editor: clashing binds, and binds on the tools' own hotkeys."""
    out = []
    groups = [("normal", mapping.taps), ("tracking", mapping.tracking_taps)]
    for name, taps in groups:
        seen = {}
        for action, key in [*taps.items(), *mapping.holds.items()]:  # defence is active in both modes
            if key in seen:
                out.append(f"{nice(key)} is bound to both {LABELS.get(seen[key], seen[key])} and "
                           f"{LABELS.get(action, action)} ({name} + defence can be active together)")
            seen.setdefault(key, action)
    for key in sorted({*mapping.taps.values(), *mapping.tracking_taps.values(), *mapping.holds.values()}):
        if key in HOTKEYS:
            out.append(f"{nice(key)} is also the tools' {HOTKEYS[key]} hotkey")
    return sorted(set(out))


def load_or_default(path):
    if os.path.exists(path):
        return gi.Mapping.load(path)
    return gi.Mapping.load(DEFAULT_PATH) if os.path.exists(DEFAULT_PATH) else gi.Mapping({})


class Editor:
    def __init__(self, root, path):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root, self.path = tk, ttk, root, path
        self.mapping = load_or_default(path)
        self.capturing = None   # (set name, action) waiting for a key
        self.mouse_bound_at = 0.0  # a click that sets a bind also releases on a button; ignore that release
        self.buttons = {}       # (set name, action) -> bind button
        self.holds = {}         # (set name, action) -> IntVar of hold ms
        root.title("Boxing controls")
        root.minsize(720, 520)
        self._build()
        root.bind_all("<KeyPress>", self._on_key, add="+")
        root.bind_all("<ButtonPress>", self._on_mouse, add="+")
        self.refresh()

    # --- layout ---------------------------------------------------------------------------------
    def _build(self):
        tk, ttk = self.tk, self.ttk
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.path_label = ttk.Label(top, text="")
        self.path_label.pack(side="left")
        ttk.Button(top, text="Save as...", command=self.save_as).pack(side="right")
        ttk.Button(top, text="Open...", command=self.open).pack(side="right", padx=4)
        ttk.Button(top, text="Reset to defaults", command=self.reset).pack(side="right")

        body = ttk.Frame(self.root, padding=(8, 0))
        body.pack(fill="both", expand=True)
        self._bind_group(body, "Normal  (glove button up)", "normal", gi.PUNCHES, True).grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self._bind_group(body, "Tracking  (glove button held)", "tracking", gi.PUNCHES, True).grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self._bind_group(body, "Defence  (camera)", "defence", gi.DEFENCE, False).grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._aim_group(body).grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        body.columnconfigure((0, 1), weight=1)

        self.status = ttk.Label(self.root, text="", padding=8, foreground="#555")
        self.status.pack(fill="x")
        self.warnings = tk.Label(self.root, text="", padx=8, pady=4, fg="#b00", justify="left", anchor="w")
        self.warnings.pack(fill="x")

    def _bind_group(self, parent, title, name, actions, with_hold):
        ttk = self.ttk
        box = ttk.LabelFrame(parent, text=title, padding=8)
        ttk.Label(box, text="Action").grid(row=0, column=0, sticky="w")
        ttk.Label(box, text="Bind").grid(row=0, column=1)
        if with_hold:
            ttk.Label(box, text="Hold (ms)").grid(row=0, column=3)
        for i, action in enumerate(actions, start=1):
            ttk.Label(box, text=LABELS.get(action, action)).grid(row=i, column=0, sticky="w", pady=2)
            b = ttk.Button(box, width=16, command=lambda n=name, a=action: self.start_capture(n, a))
            b.grid(row=i, column=1, padx=4)
            self.buttons[(name, action)] = b
            ttk.Button(box, text="x", width=2, command=lambda n=name, a=action: self.set_bind(n, a, None)).grid(row=i, column=2)
            if with_hold:
                var = self.tk.IntVar()
                spin = ttk.Spinbox(box, from_=10, to=2000, increment=10, width=6, textvariable=var,
                                   command=lambda n=name, a=action: self.set_hold(n, a))
                spin.bind("<FocusOut>", lambda e, n=name, a=action: self.set_hold(n, a))
                spin.bind("<Return>", lambda e, n=name, a=action: self.set_hold(n, a))
                spin.grid(row=i, column=3, padx=4)
                self.holds[(name, action)] = var
        return box

    def _aim_group(self, parent):
        tk, ttk = self.tk, self.ttk
        box = ttk.LabelFrame(parent, text="Steering  (right hand, while the button is held)", padding=8)
        self.aim_vars = {}
        self.aim_vars["mode"] = tk.StringVar()
        row = ttk.Frame(box)
        row.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(row, text="Speed (thumbstick)", value="speed", variable=self.aim_vars["mode"],
                        command=self.aim_changed).pack(side="left")
        ttk.Radiobutton(row, text="Position (trackpad)", value="position", variable=self.aim_vars["mode"],
                        command=self.aim_changed).pack(side="left", padx=8)
        sliders = [("speed", "Speed (px/s)", 100, 5000, "speed"), ("curve", "Curve", 1.0, 3.0, "speed"),
                   ("dead_zone", "Dead zone", 0.0, 0.25, "speed"),
                   ("position_gain", "Sensitivity (px per shoulder width)", 200, 6000, "position"),
                   ("vertical_scale", "Vertical vs horizontal", 0.25, 2.0, None)]
        self.sliders = {}
        for i, (key, text, lo, hi, mode) in enumerate(sliders, start=1):
            var = tk.DoubleVar()
            self.aim_vars[key] = var
            label = ttk.Label(box, text=text)
            label.grid(row=i, column=0, sticky="w")
            scale = ttk.Scale(box, from_=lo, to=hi, variable=var, length=200,
                              command=lambda _v: self.aim_changed(save=False))
            scale.bind("<ButtonRelease-1>", lambda e: self.aim_changed())
            scale.grid(row=i, column=1, padx=4)
            value = ttk.Label(box, width=6)
            value.grid(row=i, column=2)
            self.sliders[key] = (label, scale, value, mode)
        for j, (key, text) in enumerate([("invert_y", "Invert up/down"),
                                         ("block_while_tracking", "Camera BLOCK works while tracking")]):
            var = tk.BooleanVar()
            self.aim_vars[key] = var
            ttk.Checkbutton(box, text=text, variable=var, command=self.aim_changed).grid(
                row=len(sliders) + 1 + j, column=0, columnspan=3, sticky="w")
        return box

    # --- state -> screen ------------------------------------------------------------------------
    def _set(self, name):
        m = self.mapping
        return {"normal": (m.taps, m.tap_s), "tracking": (m.tracking_taps, m.tracking_s), "defence": (m.holds, None)}[name]

    def refresh(self):
        for (name, action), button in self.buttons.items():
            keys, _ = self._set(name)
            waiting = self.capturing == (name, action)
            button.configure(text="press a key or click..." if waiting else nice(keys.get(action)))
        for (name, action), var in self.holds.items():
            _, hold = self._set(name)
            var.set(round(1000 * hold.get(action, 0.05 if name == "normal" else 0.15)))
        aim = self.mapping.aim
        for key, var in self.aim_vars.items():
            var.set(aim[key])
        self._show_aim_values()
        self.path_label.configure(text=f"Editing {os.path.relpath(self.path, ROOT)}")
        found = problems(self.mapping)
        self.warnings.configure(text="\n".join("Warning: " + p for p in found))

    def _show_aim_values(self):
        mode = self.aim_vars["mode"].get()
        for key, (label, scale, value, only) in self.sliders.items():
            v = self.aim_vars[key].get()
            value.configure(text=f"{v:.0f}" if v >= 100 else f"{v:.2f}")
            state = "!disabled" if only in (None, mode) else "disabled"
            scale.state([state])
            label.state([state])

    # --- editing --------------------------------------------------------------------------------
    def start_capture(self, name, action):
        if time.time() - self.mouse_bound_at < 0.8:
            return
        self.capturing = (name, action)
        self.say(f"Press a key or click a mouse button for {LABELS.get(action, action)} ({name}). Esc cancels.")
        self.refresh()

    def _on_key(self, event):
        if self.capturing is None:
            return
        if event.keysym == "Escape":
            self.capturing = None
            self.say("Cancelled.")
            self.refresh()
            return "break"
        key = key_from_tk(event.keysym)
        if key is None:
            self.say(f"{event.keysym} cannot be sent to a game. Try another key.")
            return "break"
        name, action = self.capturing
        self.capturing = None
        self.set_bind(name, action, key)
        return "break"

    def _on_mouse(self, event):
        if self.capturing is None or event.num not in TK_MOUSE:
            return
        name, action = self.capturing
        self.capturing = None
        self.mouse_bound_at = time.time()
        self.set_bind(name, action, TK_MOUSE[event.num])
        return "break"

    def set_bind(self, name, action, key):
        keys, hold = self._set(name)
        if key:
            keys[action] = key
            if hold is not None:
                hold.setdefault(action, 0.05 if name == "normal" else 0.15)
        else:
            keys.pop(action, None)
        self.capturing = None
        self.save(f"{LABELS.get(action, action)} ({name}) = {nice(key)}")

    def set_hold(self, name, action):
        try:
            ms = int(self.holds[(name, action)].get())
        except (ValueError, self.tk.TclError):
            return
        _, hold = self._set(name)
        ms = max(10, min(2000, ms))
        if round(1000 * hold.get(action, -1)) != ms:
            hold[action] = ms / 1000
            self.save(f"{LABELS.get(action, action)} ({name}) holds {ms} ms")

    def aim_changed(self, save=True):
        aim = self.mapping.aim
        for key, var in self.aim_vars.items():
            v = var.get()
            aim[key] = round(v) if key in ("speed", "position_gain") else (round(v, 2) if isinstance(v, float) else v)
        self._show_aim_values()
        if save:
            self.save("steering settings")

    # --- files ----------------------------------------------------------------------------------
    def save(self, what=""):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        self.mapping.save(tmp)
        os.replace(tmp, self.path)  # so punch_live never reads a half-written file
        self.say(f"Saved {what}. A running punch_live.py picks it up within a second.")
        self.refresh()

    def say(self, text):
        self.status.configure(text=text)

    def open(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(initialdir=os.path.dirname(self.path), filetypes=[("Controls", "*.json")])
        if path:
            self.path, self.mapping = path, load_or_default(path)
            self.say(f"Opened {os.path.basename(path)}")
            self.refresh()

    def save_as(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(initialdir=os.path.dirname(self.path), defaultextension=".json",
                                            filetypes=[("Controls", "*.json")])
        if path:
            self.path = path
            self.save(f"as {os.path.basename(path)}")

    def reset(self):
        self.mapping = gi.Mapping(json.loads(json.dumps(DEFAULTS)))
        self.save("the default controls")


DEFAULTS = {
    "game": "Default controls: edit with python controls_editor.py",
    "normal": {"JAB": {"bind": "j", "hold_ms": 50}, "HOOK": {"bind": "h", "hold_ms": 50},
               "UPPERCUT": {"bind": "u", "hold_ms": 50}, "CROSS": {"bind": "k", "hold_ms": 50}},
    "tracking": {"JAB": {"bind": "mouse_left", "hold_ms": 150}, "HOOK": {"bind": "mouse_right", "hold_ms": 150},
                 "UPPERCUT": {"bind": "mouse_middle", "hold_ms": 150}},
    "defence": {"BLOCK": "w", "DUCK": "s", "SLIP LEFT": "a", "SLIP RIGHT": "d", "GUARD DOWN": "g"},
    "aim": dict(gi.AIM_DEFAULTS),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mapping", default=DEFAULT_PATH, help="controls file to edit")
    args = parser.parse_args(argv)
    import tkinter as tk
    root = tk.Tk()
    Editor(root, os.path.abspath(args.mapping))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
