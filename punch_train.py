"""Train and test the random forest punch classifier on the blocks recorded with punch_capture.py.

  python punch_train.py                    train on punch_data/, test, save models/punch_rf.joblib
  python punch_train.py --features imu     use only the glove (or pose, or both: the default)

Every block is cut with the CURRENT punch settings (punch_config.json), so you can change the timeout in
punch_tune.py and retrain without recording again. The model remembers those settings, and
punch_live.py uses them, so play always matches training.

How it is tested: 5-fold cross-validation. Each block is predicted by a forest that never saw it. If
there are several recording sessions, it also trains on all sessions but one and tests on that one,
which is closer to a new day of play. Glove-only, camera-only and combined results are all shown, so
you can see what the camera adds.
"""
import argparse
import os
import sys
import time
from collections import Counter

import numpy as np

import punch_features
import punch_segment
import punch_session as ps


def build_dataset(root, config, quiet=False):
    """Returns (X, y, groups, names, skipped)."""
    rows, labels, groups, skipped = [], [], [], Counter()
    seg_settings, lag = config["segment"], config["camera_lag_s"]
    for path in ps.block_paths(root):
        block = ps.load_block(path)
        meta, imu = block["meta"], block["imu"]
        segments = punch_segment.find_segments(imu["t"], imu["accel"], imu["gyro"], seg_settings)
        seg, _ = punch_segment.pick_block_segment(segments, meta["press_t"], meta["release_t"])
        if seg is None or seg.reason == "gap":
            skipped["no punch found with the current settings"] += 1
            continue
        feats = punch_features.extract(imu, block["pose"], seg, seg_settings.pre_s, lag)
        if feats is None:
            skipped["too little glove data"] += 1
            continue
        rows.append(feats)
        labels.append(meta["label"])
        groups.append(meta.get("session", "?"))
    if not rows:
        return None, None, None, None, skipped
    names = list(rows[0])
    X = np.array([[r.get(n, np.nan) for n in names] for r in rows], dtype=float)
    return X, np.array(labels), np.array(groups), names, skipped


def make_forest(seed=0):
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=1, class_weight="balanced",
                                  random_state=seed, n_jobs=-1)


def usable_columns(X, names, feature_set):
    """Columns for this feature set, without ones that are always missing."""
    chosen = punch_features.select(names, feature_set)
    if feature_set == "pose" and not np.any(X[:, names.index("pose_frames")] >= 3):
        return []  # the camera never saw the arm
    return [names.index(n) for n in chosen if not np.all(np.isnan(X[:, names.index(n)]))]


def evaluate(X, y, groups, names, feature_set, min_confidence, show=True):
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.model_selection import LeaveOneGroupOut, StratifiedKFold, cross_val_predict
    cols = usable_columns(X, names, feature_set)
    if not cols:
        return None
    smallest = min(Counter(y).values())
    folds = min(5, smallest)
    if folds < 2:
        return None
    proba = cross_val_predict(make_forest(), X[:, cols], y, cv=StratifiedKFold(folds, shuffle=True, random_state=0),
                              method="predict_proba")
    classes = np.unique(y)
    pred = classes[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    result = {"accuracy": float(np.mean(pred == y)), "columns": len(cols)}
    sure = conf >= min_confidence
    result["sure_share"] = float(np.mean(sure))
    result["sure_accuracy"] = float(np.mean(pred[sure] == y[sure])) if sure.any() else float("nan")
    if len(set(groups)) >= 2 and all(len(set(y[groups == g])) >= 1 for g in set(groups)):
        p2 = cross_val_predict(make_forest(), X[:, cols], y, cv=LeaveOneGroupOut(), groups=groups)
        result["new_session_accuracy"] = float(np.mean(p2 == y))
    if show:
        print(classification_report(y, pred, digits=2, zero_division=0))
        print("confusion (rows = what was thrown, columns = what the model said):")
        cm = confusion_matrix(y, pred, labels=classes)
        print(" " * 10 + "".join(f"{c:>10}" for c in classes))
        for c, row in zip(classes, cm):
            print(f"{c:>10}" + "".join(f"{v:>10}" for v in row))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", default=ps.DATA_DIR)
    parser.add_argument("--out", default=ps.MODEL_PATH)
    parser.add_argument("--features", choices=["both", "imu", "pose"], default="both")
    parser.add_argument("--config", default=ps.CONFIG_PATH)
    args = parser.parse_args(argv)

    config = ps.load_config(args.config)
    X, y, groups, names, skipped = build_dataset(args.data, config)
    if X is None:
        print(f"No usable blocks in {os.path.abspath(args.data)}. Record some with python punch_capture.py.")
        for why, n in skipped.items():
            print(f"  skipped {n}: {why}")
        return 1
    counts = Counter(y)
    print(f"{len(y)} blocks from {len(set(groups))} session(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    few = sorted(k for k, n in counts.items() if n < 5)
    if few:
        print(f"  left out of training (fewer than 5 examples): {', '.join(few)}")
        keep = ~np.isin(y, few)
        X, y, groups = X[keep], y[keep], groups[keep]
        counts = Counter(y)
    if "NONE" not in counts:
        print("  no NONE examples yet, so every movement it catches will be called a punch")
    for why, n in skipped.items():
        print(f"  skipped {n} block(s): {why}")
    pose_seen = float(np.mean(X[:, names.index("pose_frames")] >= 3))
    print(f"camera saw the arm in {100 * pose_seen:.0f}% of punches")
    if len(counts) < 2 or min(counts.values()) < 5:
        print("Need at least 5 punches of at least 2 kinds to train.")
        return 1

    print(f"\nTimeout {1000 * config['segment'].timeout_s:.0f} ms. Cross-validated accuracy:")
    summary = {}
    for fs in ("imu", "pose", "both"):
        r = evaluate(X, y, groups, names, fs, config["min_confidence"], show=False)
        summary[fs] = r
        if r is None:
            print(f"  {fs:5}: " + ("no camera data in these blocks" if fs == "pose" else "not enough data"))
            continue
        extra = f", new session {100 * r['new_session_accuracy']:.0f}%" if "new_session_accuracy" in r else ""
        sure = (f"right on {100 * r['sure_accuracy']:.0f}% of those" if r["sure_share"] else "so no keys would be pressed")
        print(f"  {fs:5}: {100 * r['accuracy']:.0f}%{extra}  "
              f"(sure enough to press a key for {100 * r['sure_share']:.0f}% of punches, {sure})")

    if summary.get(args.features) is None:
        print(f"Cannot train with --features {args.features}: not enough usable data.")
        return 1
    print(f"\nDetails for --features {args.features}:")
    evaluate(X, y, groups, names, args.features, config["min_confidence"], show=True)

    import joblib
    cols = usable_columns(X, names, args.features)
    forest = make_forest().fit(X[:, cols], y)
    used = [names[i] for i in cols]
    top = sorted(zip(forest.feature_importances_, used), reverse=True)[:10]
    print("most useful features: " + ", ".join(f"{n} {v:.2f}" for v, n in top))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    joblib.dump({"model": forest, "features": used, "labels": list(forest.classes_), "feature_set": args.features,
                 "segment": config["segment"].to_dict(), "camera_lag_s": config["camera_lag_s"],
                 "min_confidence": config["min_confidence"], "trained_at": time.strftime("%Y-%m-%d %H:%M"),
                 "counts": {str(k): int(v) for k, v in counts.items()},
                 "cv_accuracy": summary[args.features]["accuracy"]}, args.out)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
