"""Tests for game_launcher.py and the game shortcut and key-layout parts of app_server.py.

Nothing here opens a real game, browser or program: the operating-system calls are replaced, and the saved-shortcut
file is a temporary one, so the real mappings/launchers.json is never touched.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import game_input  # noqa: E402
import game_launcher as gl  # noqa: E402
from tests.test_app_server import ServerCase  # noqa: E402


class SavedShortcuts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "launchers.json")
        self.exe = os.path.join(self.tmp, "Game.exe")
        open(self.exe, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_missing_or_damaged_file_means_nothing_saved(self):
        self.assertEqual(gl.load(self.path), {})
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(gl.load(self.path), {})
        with open(self.path, "w") as f:
            f.write('["a list"]')
        self.assertEqual(gl.load(self.path), {})

    def test_saving_and_loading_round_trips(self):
        entry = gl.clean_entry("steam", "1364780", "Street Fighter 6")
        gl.save({"street-fighter": entry}, self.path)
        self.assertEqual(gl.load(self.path), {"street-fighter": entry})

    def test_entries_with_an_unknown_kind_are_dropped_on_load(self):
        with open(self.path, "w") as f:
            json.dump({"a": {"kind": "powershell", "value": "calc"}, "b": {"kind": "url", "value": "https://x.example"}}, f)
        self.assertEqual(list(gl.load(self.path)), ["b"])

    def test_good_entries_are_accepted(self):
        self.assertEqual(gl.clean_entry("steam", " 1364780 ")["value"], "1364780")
        self.assertEqual(gl.clean_entry("file", f'"{self.exe}"')["value"], self.exe)
        self.assertEqual(gl.clean_entry("file", self.exe)["label"], "Game")
        self.assertEqual(gl.clean_entry("url", "https://retrovault.gg/x")["label"], "retrovault.gg")
        self.assertTrue(gl.clean_entry("uri", "com.epicgames.launcher://apps/a%3Ab%3Ac?action=launch&silent=true"))

    def test_bad_entries_are_refused_with_a_reason(self):
        scripts = os.path.join(self.tmp, "run.bat")
        open(scripts, "w").close()
        for kind, value in (("steam", "abc"), ("steam", "12 34"), ("steam", ""), ("steam", "1; calc"),
                            ("file", os.path.join(self.tmp, "missing.exe")), ("file", scripts), ("file", self.tmp),
                            ("url", "javascript:alert(1)"), ("url", "file:///c:/windows"), ("url", "ftp://x.example"), ("url", "http://"),
                            ("uri", "calc://x"), ("uri", "steam://run/1 && calc"), ("uri", "javascript:1"),
                            ("shell", "calc"), (None, "x")):
            with self.assertRaises(ValueError, msg=f"{kind} {value!r}"):
                gl.clean_entry(kind, value)


class Launching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.exe = os.path.join(self.tmp, "Game.exe")
        self.lnk = os.path.join(self.tmp, "Game.lnk")
        open(self.exe, "w").close()
        open(self.lnk, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_steam_opens_the_steam_link(self):
        with mock.patch.object(gl, "_open") as opened:
            gl.launch({"kind": "steam", "value": "1364780"})
        opened.assert_called_once_with("steam://rungameid/1364780")

    def test_an_exe_starts_in_its_own_folder(self):
        with mock.patch.object(gl.subprocess, "Popen") as popen:
            gl.launch({"kind": "file", "value": self.exe})
        popen.assert_called_once_with([self.exe], cwd=self.tmp)

    def test_a_shortcut_file_is_opened_by_windows(self):
        with mock.patch.object(gl, "_open") as opened:
            gl.launch({"kind": "file", "value": self.lnk})
        opened.assert_called_once_with(self.lnk)

    def test_a_web_address_opens_the_browser(self):
        with mock.patch.object(gl.webbrowser, "open", return_value=True) as opened:
            gl.launch({"kind": "url", "value": "https://retrovault.gg/games/x"})
        opened.assert_called_once_with("https://retrovault.gg/games/x")

    def test_a_game_that_was_moved_says_so(self):
        os.remove(self.exe)
        with self.assertRaises(OSError) as raised:
            gl.launch({"kind": "file", "value": self.exe})
        self.assertIn("missing", str(raised.exception))

    def test_a_tampered_saved_entry_is_not_launched(self):
        """The file on disk is checked again at launch, not only when it was saved."""
        with mock.patch.object(gl, "_open") as opened, mock.patch.object(gl.subprocess, "Popen") as popen:
            for bad in ({"kind": "steam", "value": "1 & calc"}, {"kind": "uri", "value": "calc://x"},
                        {"kind": "file", "value": os.path.join(self.tmp, "run.bat")}, {"kind": "powershell", "value": "calc"}):
                with self.assertRaises(OSError, msg=str(bad)):
                    gl.launch(bad)
        opened.assert_not_called()
        popen.assert_not_called()


class FindingGames(unittest.TestCase):
    ACF = '"AppState"\n{\n\t"appid"\t\t"1364780"\n\t"name"\t\t"Street Fighter\u2122 6"\n\t"installdir"\t\t"StreetFighter6"\n}\n'
    VDF = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t}\n}\n'

    def test_steam_manifest_and_library_parsing(self):
        self.assertEqual(gl.parse_acf(self.ACF), ("1364780", "Street Fighter\u2122 6"))
        self.assertIsNone(gl.parse_acf("nothing here"))
        self.assertEqual(gl.parse_library_paths(self.VDF), [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"])

    def test_steam_games_are_read_from_every_library(self):
        tmp = tempfile.mkdtemp()
        try:
            root, other = os.path.join(tmp, "Steam"), os.path.join(tmp, "Lib2")
            for lib, appid, name in ((root, "1", "Alpha"), (other, "2", "Street Fighter V")):
                os.makedirs(os.path.join(lib, "steamapps"))
                with open(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"), "w", encoding="utf-8") as f:
                    f.write(self.ACF.replace("1364780", appid).replace("Street Fighter\u2122 6", name))
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), "w", encoding="utf-8") as f:
                f.write('"libraryfolders" { "1" { "path" "%s" } }' % other.replace("\\", "\\\\"))
            games = gl.steam_games(root)
            self.assertEqual(sorted(g["label"] for g in games), ["Alpha", "Street Fighter V"])
            self.assertTrue(all(g["kind"] == "steam" for g in games))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_find_matches_every_word_ignoring_case_and_removes_repeats(self):
        pool = [{"kind": "steam", "value": "1", "label": "Street Fighter 6", "source": "Steam"},
                {"kind": "steam", "value": "1", "label": "Street Fighter 6", "source": "Steam"},
                {"kind": "steam", "value": "2", "label": "Fighter Street", "source": "Steam"},
                {"kind": "file", "value": "x.lnk", "label": "street FIGHTER launcher", "source": "Start Menu"},
                {"kind": "steam", "value": "3", "label": "Alpha", "source": "Steam"}]
        with mock.patch.object(gl, "steam_games", return_value=pool), mock.patch.object(gl, "epic_games", return_value=[]), \
                mock.patch.object(gl, "shortcut_games", return_value=[]):
            found = gl.find(["street", "fighter"])
            self.assertEqual([f["value"] for f in found], ["1", "2", "x.lnk"])
            self.assertEqual(len(gl.find([])), 4, "no words: every Steam and Epic game, with repeats removed")
        with mock.patch.object(gl, "steam_games", return_value=[]), mock.patch.object(gl, "epic_games", return_value=[]),                 mock.patch.object(gl, "shortcut_games", return_value=[pool[3]]) as shortcuts:
            self.assertEqual(gl.find([]), [], "no words: the Start Menu is not searched")
            shortcuts.assert_not_called()
            self.assertEqual(len(gl.find(["street fighter"])), 1, "a phrase must match as a phrase")


class OnTheServer(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import app_server
        cls.srv = app_server
        cls.real_launchers = app_server.LAUNCHERS_PATH
        app_server.LAUNCHERS_PATH = os.path.join(cls.tmp, "launchers.json")

    @classmethod
    def tearDownClass(cls):
        cls.srv.LAUNCHERS_PATH = cls.real_launchers
        super().tearDownClass()

    def setUp(self):
        if os.path.exists(self.srv.LAUNCHERS_PATH):
            os.remove(self.srv.LAUNCHERS_PATH)

    def test_nothing_is_saved_at_first(self):
        status, body, _ = self.call("GET", "/api/launchers")
        self.assertEqual(status, 200)
        self.assertIn("street-fighter", body["games"])
        self.assertIsNone(body["games"]["street-fighter"])
        self.assertNotIn("punch-out", body["games"], "a browser game is a plain link, not a shortcut")

    def test_opening_before_setup_explains_what_to_do(self):
        status, body, _ = self.call("POST", "/api/launch", {"game": "street-fighter"})
        self.assertEqual(status, 400)
        self.assertIn("Set up", body["error"])

    def test_save_then_open(self):
        status, body, _ = self.call("POST", "/api/launchers", {"game": "street-fighter", "kind": "steam", "value": "1364780", "label": "Street Fighter 6"})
        self.assertEqual(status, 200)
        self.assertEqual(body["games"]["street-fighter"]["value"], "1364780")
        with mock.patch.object(gl, "launch") as launched:
            status, body, _ = self.call("POST", "/api/launch", {"game": "street-fighter"})
        self.assertEqual(status, 200)
        launched.assert_called_once()
        self.assertEqual(launched.call_args[0][0]["kind"], "steam")

    def test_only_games_on_the_list_can_have_shortcuts(self):
        for game in ("calc", "../../x", "", None, "sparring", "punch-out"):
            status, _, _ = self.call("POST", "/api/launchers", {"game": game, "kind": "steam", "value": "1"})
            self.assertEqual(status, 400, game)
            status, _, _ = self.call("POST", "/api/launch", {"game": game})
            self.assertEqual(status, 400, game)

    def test_bad_shortcuts_are_refused_and_nothing_is_saved(self):
        for body in ({"kind": "file", "value": r"C:\Windows\System32\cmd.exe\..\notepad.bat"}, {"kind": "url", "value": "javascript:1"},
                     {"kind": "steam", "value": "x"}, {"kind": "powershell", "value": "calc"}):
            status, error, _ = self.call("POST", "/api/launchers", {"game": "street-fighter", **body})
            self.assertEqual(status, 400, body)
            self.assertIn("error", error)
        self.assertEqual(gl.load(self.srv.LAUNCHERS_PATH), {})

    def test_a_shortcut_can_be_removed(self):
        self.call("POST", "/api/launchers", {"game": "other", "kind": "url", "value": "https://example.com"})
        status, body, _ = self.call("POST", "/api/launchers", {"game": "other", "clear": True})
        self.assertEqual(status, 200)
        self.assertIsNone(body["games"]["other"])

    def test_a_failed_launch_is_a_readable_error(self):
        self.call("POST", "/api/launchers", {"game": "other", "kind": "url", "value": "https://example.com"})
        with mock.patch.object(gl, "launch", side_effect=OSError("No browser could be opened.")):
            status, body, _ = self.call("POST", "/api/launch", {"game": "other"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "No browser could be opened.")

    def test_finding_uses_the_games_search_words(self):
        with mock.patch.object(gl, "find", return_value=[{"kind": "steam", "value": "1", "label": "Street Fighter 6", "source": "Steam"}]) as finder:
            status, body, _ = self.call("GET", "/api/launchers/find?game=street-fighter")
        self.assertEqual(status, 200)
        finder.assert_called_once_with(["street fighter"])
        self.assertEqual(body["candidates"][0]["label"], "Street Fighter 6")
        self.assertEqual(self.call("GET", "/api/launchers/find?game=nothing")[0], 400)


class KeyLayouts(ServerCase):
    def test_punch_out_is_on_the_games_list_with_its_link_and_layout(self):
        games = {g["id"]: g for g in self.call("GET", "/ui/games.json")[1]["games"]}
        g = games["punch-out"]
        self.assertEqual(g["link"], "https://retrovault.gg/games/mike-tysons-punch-out")
        self.assertEqual(g["preset"], "punchout")

    def test_the_preset_matches_the_games_own_controls(self):
        """From the game's page: X left punch, Z right punch, hold up for head shots, left/right dodge, down ducks, Enter star uppercut."""
        status, presets, _ = self.call("GET", "/api/presets")
        self.assertEqual(status, 200)
        p = presets["punchout"]
        binds = {k: v["bind"] for k, v in p["normal"].items()}
        self.assertEqual(binds, {"JAB": "x", "HOOK": "x", "UPPERCUT": "enter", "CROSS": "z", "RIGHT HOOK": "z", "RIGHT UPPERCUT": "enter"})
        self.assertEqual(p["defence"]["BLOCK"], "up")
        self.assertEqual(p["defence"]["DUCK"], "down")
        self.assertEqual((p["defence"]["SLIP LEFT"], p["defence"]["SLIP RIGHT"]), ("left", "right"))

    def test_the_game_engine_can_load_and_press_the_preset(self):
        mapping = game_input.Mapping.load(os.path.join(ROOT, "mappings", "presets", "punchout.json"))
        self.assertEqual(mapping.taps["CROSS"], "z")
        self.assertEqual(mapping.holds["BLOCK"], "up")
        self.assertNotIn("GUARD DOWN", mapping.holds, "guard down is left without a key, for body blows")

    def test_play_always_uses_the_one_controls_file(self):
        """One set of keys: the page's Play, the Controls page, the key editor and punch_live all use controls.json."""
        import app_server
        for p in ({"arm": "both", "preset": "punchout"}, {"arm": "left"}):
            self.assertNotIn("--mapping", app_server.tool_live(p)[1])

    def test_a_games_layout_is_copied_into_the_controls(self):
        status, saved, _ = self.call("POST", "/api/controls/preset", {"preset": "punchout"})
        self.assertEqual(status, 200)
        self.assertEqual((saved["normal"]["JAB"]["bind"], saved["normal"]["CROSS"]["bind"]), ("x", "z"))
        self.assertEqual(saved["defence"]["BLOCK"], "up")
        self.assertEqual(saved["defence"]["GUARD DOWN"], "")
        mapping = game_input.Mapping.load(__import__("app_server").CONTROLS_PATH)  # what play now presses
        self.assertEqual((mapping.taps["JAB"], mapping.holds["BLOCK"]), ("x", "up"))
        self.assertIn("tracking", self.call("GET", "/api/controls")[1])         # the rest of the file is kept

    def test_unknown_or_dangerous_presets_are_refused(self):
        for bad in ("../controls", "nope", "punchout.json", "PUNCHOUT", "", "a b", 5):
            status, _, _ = self.call("POST", "/api/controls/preset", {"preset": bad})
            self.assertEqual(status, 400, msg=str(bad))


if __name__ == "__main__":
    unittest.main()
