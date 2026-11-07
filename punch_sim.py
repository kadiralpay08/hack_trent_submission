"""Synthetic glove data, for testing the pipeline without hardware. NOT real punches.

The shapes follow the team's punch definitions, in the glove's axes, with x forward, y to the boxer's
left and z up (gravity reads +1 g on z at rest):
  JAB       straight forward: speed up along +x, then brake
  HOOK      left, then forward, then right: +y, then +x, then -y, then brake
  UPPERCUT  down, then up: a small dip along -z, then a hard drive along +z, then brake
  CROSS     the right arm's straight punch: the same motion as JAB
mirror=True makes it a right-arm glove (left and right swapped, so a hook goes right, forward, left).
magnetometer=True adds a magnetic field that turns with the glove, as the S3 glove's MPU-9250 sends.

A model trained on this data is only a test that the code runs. Its accuracy says nothing about real
punches. Real training data comes from punch_capture.py.
"""
import socket
import threading
import time

import numpy as np

import glove_receiver

RATE_HZ = 500
LABELS = ("JAB", "HOOK", "UPPERCUT")
X, Y, Z = np.eye(3)


def _pulse(n, peak):
    """A smooth half-sine burst of n samples."""
    return peak * np.sin(np.pi * (np.arange(n) + 0.5) / n)


def _phases(phases, rng, rate=RATE_HZ):
    """phases = [(axis, g, seconds, gyro axis, deg/s)]. Returns (linear accel, gyro) arrays."""
    accel, gyro = [], []
    for axis, g, seconds, gyro_axis, dps in phases:
        n = max(3, int(round(seconds * rng.uniform(0.85, 1.15) * rate)))
        shape = _pulse(n, 1.0)
        accel.append(np.outer(shape * g, axis))
        gyro.append(np.outer(shape * dps, gyro_axis))
    return np.vstack(accel), np.vstack(gyro)


def punch_motion(kind, rng, strength=1.0):
    """Linear acceleration (g) and rotation (deg/s) for one synthetic punch, glove axes."""
    k = strength * rng.uniform(0.8, 1.2)
    if kind in ("JAB", "CROSS"):
        # equal areas, so the glove ends at rest: 6 g x 80 ms forward, then 7.2 g x 67 ms back
        phases = [(X, 6 * k, 0.080, X, 80), (X, -7.2 * k, 0.067, X, -80)]
    elif kind == "HOOK":
        phases = [(Y, 4 * k, 0.070, Z, -150), (X, 5 * k, 0.080, Z, -400), (Y, -6 * k, 0.090, Z, -450),
                  (X, -5 * k, 0.080, Z, 200), (Y, 2.9 * k, 0.090, Z, 150)]
    elif kind == "UPPERCUT":
        # dip down (2 g x 80 ms), then up (6 g x 120 ms stops the dip and drives up), then brake
        phases = [(Z, -2 * k, 0.080, Y, 60), (Z, 6 * k, 0.120, Y, -350), (Z, -7 * k, 0.080, Y, 200)]
    else:
        raise ValueError(kind)
    return _phases(phases, rng)


EARTH_FIELD_UT = np.array([20.0, 0.0, -40.0])  # a typical field, in glove axes at rest


def field_in_glove(gyro, rate=RATE_HZ, start=EARTH_FIELD_UT):
    """The Earth's field as a glove turning at gyro (deg/s) would measure it, sample by sample."""
    m, out = start.astype(float).copy(), np.empty((len(gyro), 3))
    for i, w in enumerate(np.radians(gyro)):
        m = m - np.cross(w, m) / rate  # a fixed vector, seen from a turning glove
        out[i] = m
    return out


def synthetic_recording(kind, rng, rest_before=1.0, rest_after=0.6, rate=RATE_HZ, t0=0.0, mirror=False,
                        magnetometer=False):
    """A training block's worth of data: rest, one punch, rest. Returns dict of arrays plus press/release."""
    lin, rot = punch_motion(kind, rng)
    if mirror:  # swap left and right: flip y; rotation is a turning axis, so its x and z flip instead
        lin, rot = lin * [1, -1, 1], rot * [-1, 1, -1]
    n_before, n_after = int(rest_before * rate), int(rest_after * rate)
    gravity = np.array([0.0, 0.0, 1.0])
    accel = np.vstack([np.tile(gravity, (n_before, 1)), gravity + lin, np.tile(gravity, (n_after, 1))])
    gyro = np.vstack([np.zeros((n_before, 3)), rot, np.zeros((n_after, 3))])
    accel += rng.normal(0, 0.02, accel.shape)
    gyro += rng.normal(0, 1.0, gyro.shape)
    n = len(accel)
    t = t0 + np.arange(n) / rate
    start = t0 + rest_before
    out = {"t": t, "t_us": (np.arange(n) * 1_000_000 // rate).astype(np.int64), "accel": accel, "gyro": gyro,
           "press_t": start - 0.4, "release_t": start + len(lin) / rate + 0.3, "punch_t": start}
    if magnetometer:
        out["mag"] = field_in_glove(gyro, rate) + rng.normal(0, 0.3, (n, 3))
        out["mag_ok"] = np.ones(n, dtype=bool)
    return out


def simulate_glove(port=glove_receiver.PORT, kinds=LABELS, hold_button=True, seed=0, stop=None, mirror=False,
                   magnetometer=False):
    """Stream synthetic punches over UDP like the real glove, button included. Runs until stop is set."""
    rng = np.random.default_rng(seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    started, sent, n = time.time(), 0, 0
    frame_len = glove_receiver.MAG_FRAME_LEN if magnetometer else glove_receiver.FRAME_LEN
    while stop is None or not stop.is_set():
        rec = synthetic_recording(kinds[n % len(kinds)], rng, t0=sent / RATE_HZ, mirror=mirror,
                                  magnetometer=magnetometer)
        n += 1
        packet = b""
        for i in range(len(rec["t"])):
            ti = rec["t"][i]
            pressed = hold_button and rec["press_t"] <= ti <= rec["release_t"]
            mag = rec["mag"][i] if magnetometer else None
            packet += glove_receiver.pack_frame(int(sent * 1e6 / RATE_HZ), rec["accel"][i], rec["gyro"][i], pressed, mag)
            sent += 1
            if len(packet) >= 10 * frame_len:
                sock.sendto(packet, ("127.0.0.1", port))
                packet = b""
                wait = started + sent / RATE_HZ - time.time()
                if wait > 0:
                    time.sleep(wait)


def start_simulator(**kwargs):
    stop = threading.Event()
    threading.Thread(target=simulate_glove, kwargs={**kwargs, "stop": stop}, daemon=True).start()
    return stop
