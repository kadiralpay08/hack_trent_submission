# Product variants

We are testing three versions of the final product and will choose between them.

| Variant | Hardware | Status |
|---|---|---|
| `angled_camera` | One webcam, boxer turned about 45 degrees to it | Built. Settings were chosen from one session at 45 degrees (24 of 36 punches found, 11 wrong). Not yet confirmed on a second session. |
| front camera + gloves | Webcam in front, glove sensors | The team's main pipeline (`PROJECT.md`). Not yet a variant here. |
| front camera + side camera | Two webcams, no gloves | Not built. Needs a second camera. |

```
python play.py                         list the variants
python play.py angled_camera           dry run: prints the keys it would press
python play.py angled_camera --live    presses keys in the focused window
```

F7 calibrates (press it, then get in guard: it counts down 5 seconds first), F8 turns key output on or off (live starts off), F9 quits.

## Trying the 45 degree camera

1. Put the webcam 1.5 to 2 m away, aimed at your upper body. A laptop on a desk with the screen tilted back works, and 2 m is easier than 1.5 m because more of you fits in the picture. Your head and both hands must stay in view when you punch toward the camera and when you duck. Mark your spot on the floor, and do not move the laptop or the screen angle between recording and playing.
2. Run `tune.bat` (double-click it, or `.\tune.bat` in PowerShell). It uses the project's `.venv`, records a session, then scores it.
3. Stand in your boxing stance, **left (lead) shoulder toward the camera**. The camera window fills the screen and is made to be read from 2 m: the border is orange until your body angle is right, then green, and the message says which way to turn ("MORE SIDEWAYS" or "FACE CAMERA MORE"). The white dot on the bar is you, and the green part is where it should be. Aim for about 45.
4. Hold it. A beep sounds when you reach the green, and after 2 seconds of holding it the recording starts by itself. (If you never get there it starts anyway after 60 seconds, and the note says so.)
5. Follow the prompts for about 2.5 minutes. Keep the same stance throughout. Throw straight punches straight out in front of you, not at the camera.
6. The score appears when it finishes: how many of each punch were found, and how many mistakes. Read the "recorded under" line. If it says to record again, too much of the session was off the angle, so run `tune.bat` again before changing any settings.
7. Do not change `variants/angled_camera/settings.json` before the first score of a new session. The current values were chosen by looking at one earlier session, so a new session scored with them unchanged is the honest test. If it scores about as well, the settings hold. If it scores much worse, they were fitted to one session, and the recordings can be combined to choose again. To try a value without editing the file, use `python replay_punches.py --variant angled_camera --set far_hand_hold=0.2`. Every setting is described in `punch_detector.py`.
8. Play with `python play.py angled_camera --live`, **standing at the same angle** you recorded at.

Recording a session writes two files in `tuning_data/angled_camera/`: the numbers (`.csv`) and a note (`.txt`) with the body angle you actually held and how much of the session was within 10 degrees of the target. These stay on your machine and are not committed. Quitting before the recording has started saves nothing.

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
