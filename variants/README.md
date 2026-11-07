# Product variants

We are testing three versions of the final product and will choose between them.

| Variant | Hardware | Status |
|---|---|---|
| `angled_camera` | One webcam, boxer turned about 45 degrees to it | Built. Settings are starting values until a 45 degree session is recorded. |
| front camera + gloves | Webcam in front, glove sensors | The team's main pipeline (`PROJECT.md`). Not yet a variant here. |
| front camera + side camera | Two webcams, no gloves | Not built. Needs a second camera. |

```
python play.py                         list the variants
python play.py angled_camera           dry run: prints the keys it would press
python play.py angled_camera --live    presses keys in the focused window
```

F7 calibrates, F8 turns key output on or off (live starts off), F9 quits.

## Trying the 45 degree camera

1. Put the webcam at about chest height, 1.5 to 2 m away. Run `python tune_punches.py --variant angled_camera`.
2. Stand in your boxing stance, lead shoulder toward the camera. The camera window shows your body angle and says what to change. Aim for about 45 and say "good".
3. Follow the prompts for about 2.5 minutes. Keep the same stance throughout.
4. `python replay_punches.py --variant angled_camera` scores the settings on what you recorded: how many of each punch were found, and how many mistakes.
5. Change `variants/angled_camera/settings.json` (or try `--set straight_ext=0.3` first) and replay again until jabs and crosses are found without more mistakes. The numbers are in `punch_detector.py`.
6. Play with `python play.py angled_camera --live`, **standing at the same angle** you recorded at.

Recording a session writes two files in `tuning_data/angled_camera/`: the numbers (`.csv`) and a note (`.txt`) with the body angle you actually held. These stay on your machine and are not committed.

## What each variant owns

Everything specific to a variant lives in its folder, so changing one cannot change another:

- `settings.json`: punch-detector settings (see `punch_detector.Settings`). Misspelt names are an error.
- Its own recorded sessions, in `tuning_data/<name>/`.
- Its own code: extra on-screen guidance, extra hardware, or a different `run()`.

Shared by all variants and not variant-specific: `game_input.py` (key output), `mappings/` (which key each action presses, per game), `pose_tracker.py` (the camera loop), `punch_detector.py` (the detector).

## Adding a variant

1. Make a folder `variants/<name>/` with an `__init__.py` that ends with `VARIANT = YourVariant()`. Copy `variants/angled_camera/` and change it.
2. Subclass `variants.base.Variant`. Set `name`, `title`, `summary`, `needs`.
3. Put its punch settings in `variants/<name>/settings.json`.
4. If it works like the angled camera (one camera, standard pipeline), you are done. `extra_callbacks()` can add on-screen guidance. If it needs something different, such as two cameras or a glove, override `run(args)`. The base class is the standard single-camera pipeline to copy from.

You do not edit `play.py`, `variants/__init__.py` or any other variant. The folder is found automatically. If your variant fails to load, `python play.py` reports it by name and every other variant still works.

`python play.py` should list your variant. Record with `python tune_punches.py --variant <name>` and score with `python replay_punches.py --variant <name>`.
