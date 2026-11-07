"""Score punch-detection settings against a recorded tune_punches.py session, with no camera needed.

  python replay_punches.py                      newest session, new detector with default settings
  python replay_punches.py --old                what the original pose_tracker check did in that session
  python replay_punches.py --set start_speed=6 min_up=1.0

A punch counts as found if the right hand and kind fired between its beep and the next beep (people
react about a second after the beep). Anything else that fires is a mistake.
"""
import argparse
import csv
import glob
import os
from collections import Counter, OrderedDict

import punch_detector

NUMERIC = {"t", "rep", "spd_L", "spd_R", "ext_L", "right_L", "up_L", "low_L",
           "ext_R", "right_R", "up_R", "low_R"}


def load(path):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        for k in NUMERIC:
            r[k] = float(r[k])
    return rows


def beeps(rows):
    """[(time, kind, hand, rep)] for each GO window start."""
    out, seen = [], None
    for r in rows:
        key = (r["kind"], r["hand"], int(r["rep"])) if r["phase"] == "go" else None
        if key and key != seen:
            out.append((r["t"], *key))
        seen = key if key else seen
    return out


def new_events(rows, settings):
    det = punch_detector.PunchDetector(settings)
    events = []
    for r in rows:
        speeds = [r["spd_L"], r["spd_R"]]
        travel = [[r["ext_L"], r["right_L"], r["up_L"]], [r["ext_R"], r["right_R"], r["up_R"]]]
        low = [bool(r["low_L"]), bool(r["low_R"])]
        for side, kind in det.update(r["t"], speeds, travel, low):
            events.append((r["t"], side, kind))
    return events


def old_events(rows):
    return [(r["t"], r["fired"].split(":")[0], r["fired"].split(":")[1]) for r in rows if r["fired"]]


def score(rows, events, quiet=True):
    marks = beeps(rows)
    window = 2.45  # seconds after a beep that still belong to it
    hits = OrderedDict()
    for t, kind, hand, rep in marks:
        hits.setdefault((kind, hand), []).append(
            [e for e in events if t <= e[0] < t + window])
    total = Counter()
    print(f"{'punch':10}{'hand':5}{'found':>7}{'doubled':>9}{'wrong kind/hand':>18}")
    for (kind, hand), windows in hits.items():
        if kind == "NONE":
            n = sum(len(w) for w in windows)
            print(f"{'(no punch)':10}{'':5}{'':>7}{'':>9}{n:>14} fires")
            total["wrong"] += n
            continue
        found = sum(any(e[1] == hand and e[2] == kind for e in w) for w in windows)
        doubled = sum(sum(e[1] == hand and e[2] == kind for e in w) > 1 for w in windows)
        wrong = sum(sum(not (e[1] == hand and e[2] == kind) for e in w) for w in windows)
        print(f"{kind:10}{hand:5}{found:>5}/{len(windows)}{doubled:>9}{wrong:>18}")
        total["found"] += found
        total["wrong"] += wrong
        total["doubled"] += doubled
        total["reps"] += len(windows)
    first, last = marks[0][0], marks[-1][0] + window
    quiet_fires = [e for e in events if e[0] < first or e[0] >= last or
                   rows[min(range(len(rows)), key=lambda i: abs(rows[i]["t"] - e[0]))]["phase"] in ("ready", "announce")]
    print(f"fires while standing still (before/between blocks): {len(quiet_fires)}")
    print(f"TOTAL found {total['found']}/{total['reps']}, doubled {total['doubled']}, "
          f"wrong or extra {total['wrong']}, while still {len(quiet_fires)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", nargs="?", help="session csv (default: the newest in tuning_data/)")
    parser.add_argument("--old", action="store_true", help="score what pose_tracker's built-in check fired")
    parser.add_argument("--set", nargs="*", default=[], metavar="NAME=VALUE", help="override detector settings")
    args = parser.parse_args()

    path = args.file or sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "tuning_data", "session_*.csv")))[-1]
    rows = load(path)
    print(f"{os.path.basename(path)}: {len(rows)} frames, {rows[-1]['t']:.0f} s")
    if args.old:
        score(rows, old_events(rows))
        return
    settings = punch_detector.Settings()
    for item in args.set:
        name, value = item.split("=")
        setattr(settings, name, None if value == "None" else float(value))
    print("settings:", settings)
    score(rows, new_events(rows, settings))


if __name__ == "__main__":
    main()
