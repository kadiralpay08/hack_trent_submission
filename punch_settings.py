"""Show or change the shared punch settings in punch_config.json, straight from the terminal.

  python punch_settings.py                    show every setting
  python punch_settings.py timeout 300        set the punch timeout to 300 ms
  python punch_settings.py confidence 0.7     set the confidence cutoff for live play (0 to 1)
  python punch_settings.py start_g 3          set any other punch setting by its name

No camera or glove needed. Every tool reads these settings when it starts, so close and reopen any tool
that is running. After changing the timeout or a punch-finding setting, run python punch_train.py again,
because the model learns punches cut with these settings. The confidence cutoff needs no retraining.
"""
import sys

import punch_session as ps

ALIASES = {"timeout": "timeout_s", "confidence": "min_confidence", "lag": "camera_lag_s"}
TIMEOUT_MS = (100, 1500)


def show(config):
    seg = config["segment"].to_dict()
    print(f"timeout      {1000 * seg['timeout_s']:.0f} ms")
    print(f"confidence   {config['min_confidence']:g}")
    print("other punch settings: " + ", ".join(f"{k} {v:g}" for k, v in seg.items() if k != "timeout_s"))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    config = ps.load_config()
    if not argv:
        show(config)
        return 0
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1])
        return 2
    name, raw = ALIASES.get(argv[0].lower(), argv[0]), argv[1].lower().removesuffix("ms")
    try:
        value = float(raw)
    except ValueError:
        print(f"{argv[1]!r} is not a number")
        return 2
    seg = config["segment"].to_dict()
    if name == "timeout_s":
        ms = value if value > 10 else value * 1000  # accept 300 or 0.3
        if not TIMEOUT_MS[0] <= ms <= TIMEOUT_MS[1]:
            print(f"timeout must be between {TIMEOUT_MS[0]} and {TIMEOUT_MS[1]} ms")
            return 2
        seg["timeout_s"] = ms / 1000
        what = f"timeout {ms:.0f} ms"
    elif name == "min_confidence":
        if not 0 < value < 1:
            print("confidence must be between 0 and 1, for example 0.7")
            return 2
        config["min_confidence"] = value
        what = f"confidence {value:g}"
    elif name == "camera_lag_s":
        config["camera_lag_s"] = value
        what = f"camera lag {value:g} s"
    elif name in seg:
        seg[name] = value
        what = f"{name} {value:g}"
    else:
        print(f"unknown setting {argv[0]!r}. Try timeout, confidence, or one of: {', '.join(seg)}")
        return 2
    config["segment"] = ps.punch_segment.SegmentSettings.from_dict(seg)
    ps.save_config(config)
    print(f"saved: {what}")
    if name != "min_confidence" and ps.Classifier.exists():
        print("You have a trained model: run python punch_train.py so it learns punches cut this way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
