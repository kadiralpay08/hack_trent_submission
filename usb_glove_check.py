"""Check a sensor board streaming glove frames over USB (firmware/glove_imu_c3_usb): is data really arriving?

    py usb_glove_check.py --port COM9            # 5 s check, keep the sensor still
    py usb_glove_check.py --port COM9 --live     # then print readings until Ctrl+C (move it around)

It decodes the frames with glove_receiver's own parser, so it checks exactly the format the pipeline reads, and
reports the rate, lost frames, gravity at rest and gyro drift, then a verdict.
"""
import argparse
import re
import sys
import time

import numpy as np
import serial

import glove_receiver


# The board's text lines. Matched by their opening words, since "# " alone can also occur inside binary frames.
TEXT = re.compile(rb"# (?:ESP32|MPU|ERROR|status)[ -~]*\r?\n")


def read_for(port, seconds):
    """Return (samples, text lines, total bytes) received in `seconds`."""
    data = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        data += port.read(port.in_waiting or 1)
    lines = [m.group().decode("ascii").strip() for m in TEXT.finditer(data)]
    frames = TEXT.sub(b"", bytes(data))
    return list(glove_receiver.parse_frames(frames)), lines, len(data)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--live", action="store_true", help="after the check, print readings until Ctrl+C")
    args = parser.parse_args(argv)

    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.05
    port.dtr = port.rts = False  # don't hold the board in reset
    port.open()
    time.sleep(0.5)
    port.reset_input_buffer()

    print(f"Listening on {args.port} for {args.seconds:.0f} s. Keep the sensor STILL...")
    samples, lines, total = read_for(port, args.seconds)
    for line in lines:
        print("  board says:", line)
    if not samples:
        print(f"FAIL: {total} bytes arrived but no valid frames." if total else "FAIL: nothing arrived at all.")
        return 1

    t = np.array([s.t_us for s in samples], dtype=np.int64)
    steps = np.diff(t) & 0xFFFFFFFF
    span_s = steps.sum() / 1e6
    rate = (len(samples) - 1) / span_s if span_s else 0
    expected = span_s * 500 + 1
    lost = max(0.0, 1 - len(samples) / expected)
    gaps = int((steps > 50_000).sum())
    accel = np.array([s.accel for s in samples])
    gyro = np.array([s.gyro for s in samples])
    g = float(np.linalg.norm(accel.mean(0)))
    drift = float(np.linalg.norm(gyro.mean(0)))
    noise = float(accel.std(0).max())
    junk = total - len(samples) * glove_receiver.FRAME_LEN - sum(len(x) + 1 for x in lines)

    print(f"  frames {len(samples)} | {rate:.0f} Hz from the board's clock (expect 500) | lost {100 * lost:.1f}% "
          f"| gaps over 50 ms: {gaps} | junk bytes {max(junk, 0)}")
    print(f"  accel mean {np.round(accel.mean(0), 2)} g, |a| = {g:.2f} g (expect about 1.00) | "
          f"noise {noise:.3f} g")
    print(f"  gyro mean {np.round(gyro.mean(0), 1)} deg/s, drift {drift:.1f} deg/s (expect a few at most)")

    problems = []
    if not 450 <= rate <= 550:
        problems.append(f"rate is {rate:.0f} Hz, not 500")
    if lost > 0.02 or gaps:
        problems.append("frames are being lost")
    if np.ptp(accel, 0).max() == 0 and np.ptp(gyro, 0).max() == 0:
        problems.append("readings are frozen (every frame identical)")
    elif noise < 0.05 and not 0.85 <= g <= 1.15:
        problems.append(f"gravity reads {g:.2f} g at rest: wrong range setting or a damaged sensor")
    if noise < 0.05 and drift > 15:
        problems.append(f"gyro reads {drift:.0f} deg/s while still")
    if noise >= 0.05:
        print("  (it was moving, so the gravity and gyro checks are skipped: keep it still for those)")
    print("VERDICT: DATA IS COMING THROUGH, and it looks right." if not problems else "VERDICT: PROBLEM: " + "; ".join(problems))

    if args.live:
        print("\nLive readings (Ctrl+C to stop): move the sensor and watch them change")
        try:
            while True:
                chunk, _, _ = read_for(port, 0.25)
                if chunk:
                    s = chunk[-1]
                    print(f"  accel {s.accel[0]:+6.2f} {s.accel[1]:+6.2f} {s.accel[2]:+6.2f} g | "
                          f"gyro {s.gyro[0]:+7.1f} {s.gyro[1]:+7.1f} {s.gyro[2]:+7.1f} deg/s | {len(chunk) * 4} Hz")
                else:
                    print("  nothing arriving")
        except KeyboardInterrupt:
            pass
    port.close()
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
