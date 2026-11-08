"""See, delete and restore whole recording sessions, for both arms.

  python punch_sessions.py                          list every session: who, when, and how many of each punch
  python punch_sessions.py --delete 20261107_182310 delete one session (both arms), then retrain
  python punch_sessions.py --delete last            delete the newest session
  python punch_sessions.py --person sam             delete everything recorded by one person, then retrain
  python punch_sessions.py --deleted                list deleted sessions
  python punch_sessions.py --restore 20261107_182310  bring a deleted session back, then retrain

Nothing is erased. A deleted session's recordings move to <arm folder>/_deleted/<session>/, which training
ignores, and --restore moves them back. Add --yes to skip the "are you sure", and --no-train to skip retraining.
A session is one run of punch_capture.py; its name is the time it started (YYYYMMDD_HHMMSS).
"""
import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict

import numpy as np

import punch_session as ps

DELETED = "_deleted"


def read_meta(path):
    with np.load(path, allow_pickle=False) as z:
        return json.loads(str(z["meta"]))


def scan(deleted=False):
    """{session: {"person": str, "arms": {arm: Counter(label)}, "paths": [(arm, path)]}}, oldest first."""
    sessions = defaultdict(lambda: {"person": "", "arms": defaultdict(Counter), "paths": []})
    for name, arm in ps.ARMS.items():
        if deleted:
            base = os.path.join(arm.data_dir, DELETED)
            paths = [os.path.join(d, label, f) for d in _dirs(base) for label in _dirs(d, names=True)
                     for f in sorted(os.listdir(os.path.join(d, label))) if f.endswith(".npz")]
        else:
            paths = ps.block_paths(arm.data_dir)
        for path in paths:
            meta = read_meta(path)
            s = sessions[meta.get("session", "?")]
            s["person"] = s["person"] or meta.get("person", "")
            s["arms"][name][meta.get("label", os.path.basename(os.path.dirname(path)))] += 1
            s["paths"].append((name, path))
    return dict(sorted(sessions.items()))


def _dirs(folder, names=False):
    if not os.path.isdir(folder):
        return []
    found = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)))
    return found if names else [os.path.join(folder, d) for d in found]


def show(sessions, title):
    print(title)
    if not sessions:
        print("  (none)")
        return
    print(f"  {'session':17} {'person':10} arm    recordings")
    for sid, s in sessions.items():
        for i, (arm, counts) in enumerate(sorted(s["arms"].items())):
            labels = "  ".join(f"{k} {n}" for k, n in sorted(counts.items()))
            print(f"  {sid if i == 0 else '':17} {s['person'] if i == 0 else '':10} {arm:6} {labels}")


def move(sessions, chosen, to_deleted):
    """Move every recording of the chosen sessions in or out of _deleted. Returns the arms touched."""
    arms = set()
    for sid in chosen:
        for arm_name, path in sessions[sid]["paths"]:
            arm = ps.ARMS[arm_name]
            label = os.path.basename(os.path.dirname(path))
            if to_deleted:
                target = os.path.join(arm.data_dir, DELETED, sid, label)
            else:
                target = os.path.join(arm.data_dir, label)
            os.makedirs(target, exist_ok=True)
            shutil.move(path, os.path.join(target, os.path.basename(path)))
            arms.add(arm_name)
        if not to_deleted:  # tidy the emptied _deleted/<session> folders
            for arm in ps.ARMS.values():
                shutil.rmtree(os.path.join(arm.data_dir, DELETED, sid), ignore_errors=True)
    return arms


def retrain(arms):
    import punch_capture
    for name in sorted(arms):
        punch_capture.train_arm(ps.ARMS[name], True)


def pick(sessions, wanted, person):
    if person:
        chosen = [sid for sid, s in sessions.items() if s["person"].lower() == person.lower()]
        if not chosen:
            raise SystemExit(f"No sessions by '{person}'. People: {sorted({s['person'] for s in sessions.values()})}")
        return chosen
    chosen = []
    for w in wanted:
        if w == "last":
            if sessions:
                chosen.append(list(sessions)[-1])
        elif w in sessions:
            chosen.append(w)
        else:
            raise SystemExit(f"No session called {w}. Run python punch_sessions.py to list them.")
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--delete", nargs="+", metavar="SESSION", help="sessions to delete ('last' = the newest)")
    parser.add_argument("--person", help="delete every session recorded by this person")
    parser.add_argument("--restore", nargs="+", metavar="SESSION", help="deleted sessions to bring back")
    parser.add_argument("--deleted", action="store_true", help="list deleted sessions")
    parser.add_argument("--yes", action="store_true", help="don't ask before deleting or restoring")
    parser.add_argument("--no-train", action="store_true", help="don't retrain the arms afterwards")
    args = parser.parse_args(argv)

    restoring = bool(args.restore)
    deleting = bool(args.delete or args.person)
    if restoring and deleting:
        raise SystemExit("Delete or restore, not both at once.")
    if not (restoring or deleting):
        show(scan(deleted=args.deleted), "Deleted sessions:" if args.deleted else "Sessions used for training:")
        return 0

    sessions = scan(deleted=restoring)
    chosen = pick(sessions, args.restore if restoring else (args.delete or []), None if restoring else args.person)
    show({sid: sessions[sid] for sid in chosen}, "Will " + ("restore:" if restoring else "delete (recoverable):"))
    if not args.yes:
        try:
            answer = input("Go ahead? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Nothing changed.")
            return 1
    arms = move(sessions, chosen, to_deleted=not restoring)
    print(("Restored " if restoring else "Deleted ") + ", ".join(chosen)
          + ("" if restoring else f". Undo with: python punch_sessions.py --restore {' '.join(chosen)}"))
    if not args.no_train:
        retrain(arms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
