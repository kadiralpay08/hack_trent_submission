"""Open a game from the Keyboard Warrior page: find it on this computer, remember it, and start it on request.

A web page cannot start programs, so app_server.py does it with this module. Each game gets ONE saved shortcut
(mappings/launchers.json, kept off git because paths differ on every computer). A shortcut is one of:

  steam   a Steam game by its app id           opens steam://rungameid/<id>, so Steam starts it
  uri     a launcher link such as Epic's       com.epicgames.launcher://apps/...
  file    a program or shortcut on this PC     .exe, .lnk or .url (nothing else: no scripts)
  url     a web address                        http or https, opened in the default browser

find() looks for installed games by name (Steam libraries, Epic, Start Menu and Desktop shortcuts), so the page can
offer them to pick from. Nothing is launched except a shortcut the person saved, and what can be saved is checked
here, not in the page.
"""
import glob
import json
import os
import re
import subprocess
import sys
import webbrowser
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "mappings", "launchers.json")
FILE_TYPES = (".exe", ".lnk", ".url")
URI_SCHEMES = ("steam://", "com.epicgames.launcher://")
KINDS = ("steam", "uri", "file", "url")
APPID = re.compile(r"\d{1,10}")


# ---------------------------------------------------------------- saved shortcuts
def load(path=None):
    """{game id: {"kind", "value", "label"}}. A missing or damaged file is just 'nothing saved yet'."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {g: e for g, e in data.items() if isinstance(e, dict) and e.get("kind") in KINDS and e.get("value")} \
        if isinstance(data, dict) else {}


def save(config, path=None):
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def clean_entry(kind, value, label=""):
    """Check a shortcut the page wants to save. Returns the entry, or raises ValueError with a message to show."""
    value = str(value or "").strip()
    label = str(label or "").strip()[:80]
    if kind == "steam":
        if not APPID.fullmatch(value):
            raise ValueError("A Steam app id is a number, such as 1364780. Find it in the game's Steam store page address.")
    elif kind == "uri":
        if not value.lower().startswith(URI_SCHEMES) or any(c in value for c in ' "<>^|\r\n'):
            raise ValueError("That launcher link is not one this page can open (Steam and Epic links only).")
    elif kind == "file":
        value = value.strip('"')
        if not os.path.isfile(value):
            raise ValueError("That file does not exist. Paste the full path to the game's .exe or its shortcut.")
        if not value.lower().endswith(FILE_TYPES):
            raise ValueError("Only .exe, .lnk and .url files can be used as a game shortcut.")
    elif kind == "url":
        parts = urlparse(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("A web address must start with http:// or https://.")
    else:
        raise ValueError("Choose Steam, a program file, a launcher link or a web address.")
    return {"kind": kind, "value": value, "label": label or default_label(kind, value)}


def default_label(kind, value):
    if kind == "steam":
        return f"Steam game {value}"
    if kind == "file":
        return os.path.splitext(os.path.basename(value))[0]
    if kind == "url":
        return urlparse(value).netloc
    return value


# ---------------------------------------------------------------- opening
def _open(target):
    if sys.platform != "win32":
        raise OSError("Opening games from this page needs Windows.")
    os.startfile(target)  # noqa: S606  (a target this module has already checked)


def launch(entry):
    """Start a saved shortcut. Raises OSError with a message to show if it could not be started."""
    kind, value = entry["kind"], entry["value"]
    if kind == "steam":
        if not APPID.fullmatch(str(value)):
            raise OSError("The saved Steam app id is not valid. Set the shortcut up again.")
        _open(f"steam://rungameid/{value}")
    elif kind == "uri":
        if not str(value).lower().startswith(URI_SCHEMES):
            raise OSError("The saved launcher link is not valid. Set the shortcut up again.")
        _open(value)
    elif kind == "file":
        if not os.path.isfile(value) or not value.lower().endswith(FILE_TYPES):
            raise OSError(f"{value} is missing. It may have been moved or uninstalled: set the shortcut up again.")
        if value.lower().endswith(".exe"):
            subprocess.Popen([value], cwd=os.path.dirname(value) or None)  # a game wants to start in its own folder
        else:
            _open(value)
    elif kind == "url":
        if not webbrowser.open(value):
            raise OSError("No browser could be opened.")
    else:
        raise OSError("Unknown shortcut type.")


# ---------------------------------------------------------------- finding installed games
def parse_acf(text):
    """(app id, name) from a Steam appmanifest_*.acf, or None."""
    appid = re.search(r'"appid"\s+"(\d+)"', text)
    name = re.search(r'"name"\s+"([^"]*)"', text)
    return (appid.group(1), name.group(1)) if appid and name else None


def parse_library_paths(text):
    """The library folders listed in Steam's libraryfolders.vdf."""
    return [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', text)]


def steam_root():
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                root = winreg.QueryValueEx(key, "SteamPath")[0]
                if os.path.isdir(root):
                    return os.path.normpath(root)
        except OSError:
            pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(guess):
            return guess
    return None


def steam_games(root=None):
    root = root or steam_root()
    if not root:
        return []
    libraries = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            libraries += [p for p in parse_library_paths(f.read()) if os.path.normpath(p) != os.path.normpath(root)]
    except OSError:
        pass
    found = []
    for lib in libraries:
        for manifest in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                with open(manifest, encoding="utf-8", errors="replace") as f:
                    parsed = parse_acf(f.read())
            except OSError:
                continue
            if parsed:
                found.append({"kind": "steam", "value": parsed[0], "label": parsed[1], "source": "Steam"})
    return found


def epic_games():
    manifests = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Epic", "EpicGamesLauncher", "Data", "Manifests")
    found = []
    for item in glob.glob(os.path.join(manifests, "*.item")):
        try:
            with open(item, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            ns, cat, app = data["CatalogNamespace"], data["CatalogItemId"], data["AppName"]
            found.append({"kind": "uri", "value": f"com.epicgames.launcher://apps/{ns}%3A{cat}%3A{app}?action=launch&silent=true",
                          "label": data["DisplayName"], "source": "Epic Games"})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def shortcut_games(max_depth=3):
    """Start Menu and Desktop shortcuts (.lnk): what a person would double-click to start a game."""
    roots = [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs"),
             os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
             os.path.join(os.path.expanduser("~"), "Desktop"), os.path.join(os.environ.get("PUBLIC", ""), "Desktop")]
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for folder, dirs, files in os.walk(root):
            if folder.count(os.sep) - base_depth >= max_depth:
                dirs[:] = []
            for name in files:
                if name.lower().endswith(".lnk"):
                    found.append({"kind": "file", "value": os.path.join(folder, name), "label": os.path.splitext(name)[0],
                                  "source": "Start Menu or Desktop"})
    return found


def find(words=(), limit=40):
    """Installed games and shortcuts whose names contain every word (case does not matter). No words: the games
    from Steam and Epic only, since every Start Menu shortcut would be too many."""
    words = [w.lower() for w in words if w]
    pools = [steam_games(), epic_games()] + ([shortcut_games()] if words else [])
    seen, out = set(), []
    for item in (i for pool in pools for i in pool):
        if all(w in item["label"].lower() for w in words) and (item["kind"], item["value"]) not in seen:
            seen.add((item["kind"], item["value"]))
            out.append(item)
    return out[:limit]
