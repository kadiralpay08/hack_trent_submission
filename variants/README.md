# Product variants

A variant is one way of turning a boxer's movement into key presses. This folder holds the framework for trying several side by side. Each variant is a folder here with its own settings and its own recorded tuning sessions, found automatically.

**No variant is active now.** The team chose the front camera plus glove (`PROJECT.md`), and that runs as its own scripts: `play.bat` or `punch_live.py`, not through this framework.

| Version | Hardware | Status |
|---|---|---|
| Front camera + glove | Webcam in front, motion sensor on the glove | **The main path.** Scripts `punch_*.py`; see the top of `README.md`. Not a variant. |
| One camera at 45 degrees | One webcam, boxer turned about 45 degrees to it | **Shelved** in `archive/angled_camera/`. Found 24 of 36 punches on two sessions, but hooks were unreliable. Its README has the results and how to bring it back. |
| Front camera + side camera | Two webcams, no gloves | Not built. Needs a second camera. |

With nothing in this folder, `python play.py` prints `no variants found`. That is expected.

## What each variant owns

Everything specific to a variant lives in its folder, so changing one cannot change another:

- `settings.json`: punch-detector settings (see `punch_detector.Settings`). Misspelt names are an error.
- Its own recorded sessions, in `tuning_data/<name>/`.
- Its own code: extra on-screen guidance, extra hardware, or a different `run()`.

Shared by all variants and not variant-specific: `game_input.py` (key output), `mappings/` (which key each action presses, per game), `pose_tracker.py` (the camera loop), `punch_detector.py` (the detector).

## Adding a variant

1. Make a folder `variants/<name>/` with an `__init__.py` that ends with `VARIANT = YourVariant()`. The shelved `archive/angled_camera/` is a complete example to copy.
2. Subclass `variants.base.Variant`. Set `name`, `title`, `summary`, `needs`.
3. Put its punch settings in `variants/<name>/settings.json`.
4. If it works like a one-camera setup (the standard pipeline), you are done. `extra_callbacks()` can add on-screen guidance. If it needs something different, such as two cameras or a glove, override `run(args)`. The base class is the standard single-camera pipeline to copy from.

You do not edit `play.py`, `variants/__init__.py` or any other variant. The folder is found automatically. If your variant fails to load, `python play.py` reports it by name and every other variant still works.

`python play.py` should list your variant. Record with `python tune_punches.py --variant <name>` and score with `python replay_punches.py --variant <name>`.
