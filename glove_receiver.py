"""Receive glove IMU frames over UDP.

Frame (19 bytes): A5 5A | uint32 us (little-endian) | ax ay az gx gy gz int16 (big-endian) | XOR of bytes 2-17
Run directly to print incoming data, or with --simulate to fake a glove for testing.
"""
import argparse
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

PORT = 5005
SYNC = b"\xA5\x5A"
FRAME_LEN = 19

# Must match the ranges the firmware sets on the MPU.
ACCEL_LSB_PER_G = 2048.0   # +-16 g; 16384 = +-2 g, 8192 = +-4 g, 4096 = +-8 g
GYRO_LSB_PER_DPS = 16.4    # +-2000 deg/s; 131 = +-250, 65.5 = +-500, 32.8 = +-1000

# How the sensor sits on the glove: glove axis i = AXIS_SIGN[i] * sensor axis AXIS_MAP[i]
AXIS_MAP = [0, 1, 2]
AXIS_SIGN = np.array([1, 1, 1])

# Frames carry no glove id, so identify the glove by its sender IP.
GLOVE_BY_IP = {}        # e.g. {"192.168.137.20": "L", "192.168.137.21": "R"}
DEFAULT_GLOVE = "L"


@dataclass
class Sample:
    t_us: int            # ESP32 clock (wraps every ~71 min)
    accel: np.ndarray    # g, in glove axes
    gyro: np.ndarray     # deg/s, in glove axes
    t: float = 0.0       # when it was measured, on the laptop clock (time.time()); set by Clock
    dt: float = 0.0      # seconds since the previous sample, from t_us; a dropped packet shows as a big dt


def checksum(body):
    out = 0
    for b in body:
        out ^= b
    return out


def parse_frames(data):
    """Yield a Sample for every valid frame in data; junk or corrupt bytes are skipped."""
    i = 0
    while i + FRAME_LEN <= len(data):
        if data[i:i + 2] == SYNC and checksum(data[i + 2:i + 18]) == data[i + 18]:
            (t_us,) = struct.unpack_from("<I", data, i + 2)
            raw = np.array(struct.unpack_from(">6h", data, i + 6), dtype=float)
            accel = raw[:3][AXIS_MAP] / ACCEL_LSB_PER_G * AXIS_SIGN
            gyro = raw[3:][AXIS_MAP] / GYRO_LSB_PER_DPS * AXIS_SIGN
            yield Sample(t_us, accel, gyro)
            i += FRAME_LEN
        else:
            i += 1  # resync


def pack_frame(t_us, accel, gyro):
    """Build a frame from accel (g) and gyro (deg/s); used by the simulator and tests."""
    counts = np.concatenate([np.asarray(accel) * ACCEL_LSB_PER_G, np.asarray(gyro) * GYRO_LSB_PER_DPS])
    body = struct.pack("<I", t_us & 0xFFFFFFFF) + struct.pack(">6h", *np.clip(np.round(counts), -32768, 32767).astype(int))
    return SYNC + body + bytes([checksum(body)])


class Clock:
    """Puts one glove's ESP32 microsecond counter on the laptop clock.

    Packets arrive late and in bursts, so arrival time is too jittery to use directly. Instead the
    ESP32 time is unwrapped and shifted by the smallest (arrival - ESP32 time) seen recently, which
    is the packet that suffered the least delay. The result is smooth and off by only that minimum
    WiFi latency (a few ms).
    """
    MASK = 0xFFFFFFFF
    RESET_US = 5_000_000   # a step bigger than this means a reboot or long outage

    def __init__(self, window=500):
        self.last_us, self.elapsed_us = None, 0
        self.offsets = deque(maxlen=window)

    def stamp(self, sample, arrival):
        step = (sample.t_us - self.last_us) & self.MASK if self.last_us is not None else 0
        if self.last_us is None or step > self.RESET_US:  # first sample, reboot, or long outage
            self.elapsed_us, step = sample.t_us, 0
            self.offsets.clear()
        else:
            self.elapsed_us += step  # modular difference, so the 32-bit rollover is harmless
        self.last_us = sample.t_us
        t_esp = self.elapsed_us / 1e6
        self.offsets.append(arrival - t_esp)
        sample.dt, sample.t = step / 1e6, t_esp + min(self.offsets)


class GloveReceiver:
    """Background UDP listener. samples[glove_id] holds the most recent readings."""

    def __init__(self, port=PORT, history=1000):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self.samples, self.clocks, self.history = {}, {}, history
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            data, addr = self.sock.recvfrom(2048)
            arrival = time.time()
            glove = GLOVE_BY_IP.get(addr[0], DEFAULT_GLOVE)
            clock = self.clocks.setdefault(glove, Clock())
            for sample in parse_frames(data):
                clock.stamp(sample, arrival)
                self.samples.setdefault(glove, deque(maxlen=self.history)).append(sample)

    def latest(self, glove):
        buf = self.samples.get(glove)
        return buf[-1] if buf else None

    def recent(self, glove, seconds):
        cutoff = time.time() - seconds
        return [s for s in list(self.samples.get(glove, [])) if s.t >= cutoff]


def simulate(port=PORT, hz=200):
    """Send fake frames: a resting glove with an 80 ms punch every 1.5 s."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.time()
    while True:
        t = time.time() - t0
        phase = t % 1.5
        bump = np.sin(np.pi * phase / 0.08) if phase < 0.08 else 0.0
        accel = np.array([8 * bump, 0, 1]) + np.random.normal(0, 0.02, 3)
        gyro = np.array([0, 0, 300 * bump]) + np.random.normal(0, 1, 3)
        sock.sendto(pack_frame(int(t * 1e6), accel, gyro), ("127.0.0.1", port))
        time.sleep(1 / hz)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="send fake glove data to ourselves")
    args = parser.parse_args()

    receiver = GloveReceiver()
    if args.simulate:
        threading.Thread(target=simulate, daemon=True).start()
    while True:
        time.sleep(0.5)
        for glove in list(receiver.samples):
            recent = receiver.recent(glove, 0.5)
            peak = max((np.linalg.norm(s.accel) for s in recent), default=0)
            print(f"glove {glove}: {len(recent) * 2} Hz, peak accel {peak:.1f} g, gyro {receiver.latest(glove).gyro.round(0)}")
