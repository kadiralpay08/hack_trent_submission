# Shelved: one webcam, boxer turned 45 degrees

This was one of the three product versions in `variants/README.md`. The team chose the front camera plus glove instead (`PROJECT.md`), so this one is parked here. Nothing in the project loads it. The code is complete and was working when it was shelved.

## What it was

One webcam, with the boxer turned about 45 degrees to it and the lead (left) shoulder toward the camera. At that angle a straight punch moves partly across the picture, so jabs and crosses can be seen. A front-on camera cannot see them.

## How well it worked

Measured on two full recordings (36 punches each) at a true 45 degrees, with the settings in `settings.json` and nothing changed between sessions:

| Punch | First session | Second session |
|---|---|---|
| Jab | 2/6 | 6/6 |
| Cross | 6/6 | 6/6 |
| Uppercut, left | 5/6 | 5/6 |
| Uppercut, right | 4/6 | 6/6 |
| Hook, left | 3/6 | 1/6 |
| Hook, right | 4/6 | 0/6 |
| **Found** | **24/36** | **24/36** |
| Wrong fires | 11 | 23 |

Straight punches and uppercuts held up. Hooks did not: a flat right hook from the guard looks the same as a cross, and a left hook that arcs upward looks like an uppercut. Retuning on one session and testing on the other gave no reliable gain, so the weakness is in what a single camera can see, not in the settings.

## What is here

| File | What it is |
|---|---|
| `__init__.py` | The variant: a live body-angle guide (large border, gauge and message, readable from 2 m), the wait-for-position start for recording, and the session note. |
| `settings.json` | Detector settings chosen from the first session. |
| `tune.bat` | Recorded a tuning session and then scored it. |

## What stayed behind in shared code

These are generic, switched off by default, and still in place. Nothing needs changing for the front camera plus glove path:

- `punch_detector.py`: the `straight_max_up`, `straight_min_travel` and `far_hand_hold` settings.
- `tune_punches.py` and `replay_punches.py`: the tuning recorder and scorer, including the `--variant` option, the large recording screen and the wait-for-position start.
- `variants/base.py`, `overlay.py`, `play.py`: the variant framework. With no variants in `variants/`, `python play.py` reports that none were found.
- `website/`: the 45 degree mode is still listed, marked unavailable, and `website/settings/angled_camera.json` is its settings copy.

## Bring it back

From the project folder:

```powershell
git mv archive/angled_camera variants/angled_camera
git mv variants/angled_camera/tune.bat tune.bat
```

Then `python play.py angled_camera` plays it and `.\tune.bat` records and scores a session. To list it on the website again, set `available: true` and a badge in `website/js/modes.js`. The recorded sessions are in `tuning_data/angled_camera/` on the machine that recorded them. They are not committed.
