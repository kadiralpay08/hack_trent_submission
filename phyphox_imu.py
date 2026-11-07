"""Right-hand sensor from the phyphox app, which the laptop polls over WiFi.

The usual way round, a phone sending to the laptop, needs a connection into the laptop, and a firewall or a
hotspot can block that. Here the laptop opens the connection to the phone instead (phyphox's "remote access"
serves its readings over plain http), so nothing has to get through to the laptop.

On the phone: install phyphox, open an experiment that shows the accelerometer WITH gravity and the gyroscope,
turn on remote access in its menu (the app shows an address), and press play. Then on the laptop:

  python phyphox_imu.py --phone 172.20.10.1 --discover   list the experiment's buffers, to check them
  python phyphox_imu.py --phone 172.20.10.1               print the readings as they arrive
  python phyphox_imu.py --phone 192.168.1.20:8080         an Android phone says its port; an iPhone's is 80

On an iPhone's own hotspot, the phone's address is usually the hotspot's, 172.20.10.1. Samples come out like
glove_receiver.py's (units of g and deg/s, on the laptop clock) under the glove id "R", in their own receiver:
nothing here touches the left glove's pipeline.

phyphox reports acceleration in m/s^2 and rotation in rad/s; both are converted. Its rate is whatever the
experiment is set to, usually far below the glove's 500 Hz.
"""
import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import numpy as np

import phone_imu

AXES = "xyz"
DEFAULT_INTERVAL = 0.1   # seconds between polls
STALL_S = 1.5            # measuring, but nothing new for this long: the phone's clock restarted, so start again
KEEP_GYRO = 2000         # gyro readings kept to line up with the accelerometer's times


def pick_buffers(names):
    """Find the accelerometer and gyroscope among an experiment's buffer names.

    Returns {"acc": ([x, y, z], time) , "gyro": ([x, y, z], time)}; a kind that isn't there is left out.
    """
    found = {}
    for kind, stem in (("acc", r"acc(?:eleration)?"), ("gyro", r"gyr(?:o|oscope)?")):
        axes = {}
        for name in names:
            match = re.fullmatch(stem + r"_?([xyz])", name, re.IGNORECASE)
            if match:
                axes[match.group(1).lower()] = name
        times = [n for n in names if re.fullmatch(stem + r"_?time", n, re.IGNORECASE)]
        if all(a in axes for a in AXES) and times:
            found[kind] = ([axes[a] for a in AXES], times[0])
    return found


class PhyphoxClient:
    """The few phyphox remote-access calls used here (https://phyphox.org/docs/remote-interface/)."""

    def __init__(self, address, timeout=2.0):
        self.base = "http://" + address.removeprefix("http://").rstrip("/")
        self.timeout = timeout

    def call(self, path, params=()):
        query = "&".join(urllib.parse.quote(k, safe="") + ("" if v is None else "=" + urllib.parse.quote(v, safe=""))
                         for k, v in params)
        with urllib.request.urlopen(f"{self.base}/{path}" + (f"?{query}" if query else ""), timeout=self.timeout) as r:
            return json.loads(r.read())

    def config(self):
        return self.call("config")

    def get(self, params):
        """params: (buffer, value) pairs. A value of None asks for the last value only; "full" for everything;
        "3.2|time" for what comes after 3.2 on the buffer called time."""
        return self.call("get", params)

    def control(self, cmd):
        return self.call("control", [("cmd", cmd)]).get("result", False)


class PhyphoxReceiver:
    """Polls phyphox and feeds a phone_imu.PhoneReceiver, so samples, latest() and recent() work as usual.

    acc and gyro are ([x, y, z buffer names], time buffer name). gyro=None fills the rotation with zeros, for an
    experiment that only has an accelerometer.
    """

    def __init__(self, address, acc, gyro, interval=DEFAULT_INTERVAL, gyro_rad=True, flip_accel=False,
                 on_sample=None, history=1000, timeout=2.0):
        self.client = PhyphoxClient(address, timeout)
        self.acc, self.gyro, self.interval = acc, gyro, interval
        self.sink = phone_imu.PhoneReceiver(port=None, history=history, on_sample=on_sample,
                                            flip_accel=flip_accel, gyro_rad=gyro_rad)
        self.samples = self.sink.samples
        self.error, self.measuring, self.session = None, None, None
        self.last_t = {}                 # time buffer -> the newest time already taken
        self.acc_rows = []               # (t, ax, ay, az) waiting for gyro readings that reach their time
        self.gyro_rows = deque(maxlen=KEEP_GYRO)
        self.last_data = time.time()     # when readings last arrived, to notice a stall
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)

    @property
    def total(self):
        return self.sink.total

    def latest(self):
        return self.sink.latest()

    def recent(self, seconds):
        return self.sink.recent(seconds)

    def _groups(self):
        return [g for g in (self.acc, self.gyro) if g]

    def _params(self):
        params = {}
        for axes, tname in self._groups():
            if tname not in self.last_t:
                params[tname] = None  # first time round: only find out where the phone's clock is now
            else:
                after = repr(float(self.last_t[tname]))  # exact: a rounded-down threshold would resend a reading
                params[tname] = after
                for name in axes:
                    params[name] = f"{after}|{tname}"
        return list(params.items())

    def _run(self):
        while not self.stop.is_set():
            try:
                self._poll()
                self.error = None
            except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as error:
                self.error = str(error) or type(error).__name__
            self.stop.wait(self.interval)

    def _poll(self):
        data = self.client.get(self._params())
        arrival = time.time()
        status = data.get("status", {})
        self.measuring = status.get("measuring")
        if self.session is not None and status.get("session") != self.session:
            self.last_t.clear()  # a different experiment: start again
            self.acc_rows, self.gyro_rows = [], deque(maxlen=KEEP_GYRO)
        self.session = status.get("session")
        if self.measuring and self.last_t and time.time() - self.last_data > STALL_S:
            # The experiment was restarted or cleared, so the phone's time went back to zero and nothing is "newer"
            # than the last reading seen: it would stay silent until the clock caught up. Start again from now.
            self.last_t.clear()
            self.acc_rows, self.gyro_rows = [], deque(maxlen=KEEP_GYRO)
            self.last_data = time.time()
        buffers = data.get("buffer", {})

        columns = {}
        for axes, tname in self._groups():
            first = tname not in self.last_t
            t = (buffers.get(tname) or {}).get("buffer") or []
            if not t:
                continue
            if first:
                self.last_t[tname] = t[-1]  # start from now; older readings are not wanted
                self.last_data = time.time()
                continue
            series = [(buffers.get(a) or {}).get("buffer") or [] for a in axes]
            n = min([len(t)] + [len(s) for s in series])
            newer = [i for i in range(n) if t[i] is not None and t[i] > self.last_t[tname]]
            if newer:
                columns[tname, tuple(axes)] = ([t[i] for i in newer], [[s[i] for i in newer] for s in series])
                self.last_t[tname] = t[newer[-1]]
                self.last_data = time.time()

        if self.acc and (self.acc[1], tuple(self.acc[0])) in columns:
            t, (x, y, z) = columns[self.acc[1], tuple(self.acc[0])]
            self.acc_rows += [r for r in zip(t, x, y, z) if None not in r]
        if self.gyro and (self.gyro[1], tuple(self.gyro[0])) in columns:
            t, (x, y, z) = columns[self.gyro[1], tuple(self.gyro[0])]
            self.gyro_rows.extend(r for r in zip(t, x, y, z) if None not in r)
        self._emit(arrival)

    def _emit(self, arrival):
        """Accelerometer readings are the timeline; the gyro is interpolated to each one's time."""
        if not self.acc_rows:
            return
        if self.gyro is None:
            ready, self.acc_rows = self.acc_rows, []
            rows = [[t * 1000, x, y, z, 0.0, 0.0, 0.0] for t, x, y, z in ready]
        else:
            if len(self.gyro_rows) < 2:
                return
            g = np.array(self.gyro_rows, dtype=float)
            reach = g[-1, 0]  # beyond this the gyro is not known yet, so those readings wait
            ready = [r for r in self.acc_rows if r[0] <= reach]
            self.acc_rows = [r for r in self.acc_rows if r[0] > reach]
            rows = [[t * 1000, x, y, z] + [float(np.interp(t, g[:, 0], g[:, k])) for k in (1, 2, 3)]
                    for t, x, y, z in ready]
        if rows:
            self.sink.ingest(rows, arrival)


def open_receiver(address, on_sample=None, start=False, **kwargs):
    """Connect to phyphox, find the accelerometer and gyroscope, and start polling.

    Raises RuntimeError, saying what to fix, if the phone can't be reached or the open experiment lacks either.
    """
    client = PhyphoxClient(address)
    try:
        names = [b["name"] for b in client.config().get("buffers", [])]
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise RuntimeError(f"Cannot reach phyphox at {client.base}: {error}. Check remote access is on in the app, the "
                           "address (and port) it shows, and that the laptop is on the same WiFi.")
    found = pick_buffers(names)
    if "acc" not in found or "gyro" not in found:
        raise RuntimeError("The open phyphox experiment needs the accelerometer (with g) and the gyroscope. "
                           f"Its buffers are: {', '.join(names)}. Run phyphox_imu.py --discover to check.")
    if start:
        client.control("start")
    return PhyphoxReceiver(address, found["acc"], found["gyro"], on_sample=on_sample, **kwargs)


def discover(client):
    config = client.config()
    names = [b["name"] for b in config.get("buffers", [])]
    print(f"Experiment: {config.get('localTitle') or config.get('title')}")
    print("Buffers:", ", ".join(names))
    for group in config.get("export", []):
        print(f"Export set '{group.get('set')}':", ", ".join(f"{s['label']} -> {s['buffer']}" for s in group["sources"]))
    found = pick_buffers(names)
    for kind in ("acc", "gyro"):
        print(f"{kind}:", f"{found[kind][0]} with time {found[kind][1]}" if kind in found else "NOT FOUND")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--phone", required=True, help="the address phyphox shows, with :port if it isn't 80")
    parser.add_argument("--discover", action="store_true", help="list the experiment's buffers and stop")
    parser.add_argument("--start", action="store_true", help="start the measurement from here (or press play)")
    parser.add_argument("--acc", nargs=4, metavar=("X", "Y", "Z", "TIME"), help="accelerometer buffer names")
    parser.add_argument("--gyro", nargs=4, metavar=("X", "Y", "Z", "TIME"), help="gyroscope buffer names")
    parser.add_argument("--accel-only", action="store_true", help="the experiment has no gyroscope: use zeros")
    parser.add_argument("--gyro-deg", action="store_true", help="rotation is already in deg/s (phyphox uses rad/s)")
    parser.add_argument("--flip-accel", action="store_true", help="reverse acceleration if flat, screen up, reads -1 g")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between polls")
    args = parser.parse_args()

    client = PhyphoxClient(args.phone)
    try:
        found = discover(client) if args.discover or not (args.acc and (args.gyro or args.accel_only)) \
            else {}
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SystemExit(f"Cannot reach phyphox at {client.base}: {error}\nCheck remote access is on in the app, the "
                         "address (and port) it shows, and that the laptop is on the same WiFi.")
    if args.discover:
        return
    acc = (args.acc[:3], args.acc[3]) if args.acc else found.get("acc")
    gyro = (args.gyro[:3], args.gyro[3]) if args.gyro else found.get("gyro")
    if not acc or (not gyro and not args.accel_only):
        raise SystemExit("\nCould not find the buffers. Open an experiment with the accelerometer (with g) and the "
                         "gyroscope, or name the buffers with --acc and --gyro (see above), or use --accel-only.")
    if args.accel_only:
        gyro = None
    if args.start:
        print("start:", "ok" if client.control("start") else "refused")

    receiver = PhyphoxReceiver(args.phone, acc, gyro, interval=args.interval, gyro_rad=not args.gyro_deg,
                               flip_accel=args.flip_accel)
    print("Polling phyphox. Press play in the app if it isn't measuring. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
            if receiver.error:
                print(f"cannot reach phyphox: {receiver.error}")
                continue
            recent = receiver.recent(0.5)
            if not recent:
                print("phyphox is not measuring: press play in the app" if receiver.measuring is False
                      else "waiting for readings...")
                continue
            peak = max(np.linalg.norm(s.accel) for s in recent)
            print(f"phone {phone_imu.GLOVE}: {len(recent) * 2} Hz, peak accel {peak:.1f} g, "
                  f"gyro {receiver.latest().gyro.round(0)}")
    except KeyboardInterrupt:
        pass
    finally:
        receiver.close()


if __name__ == "__main__":
    main()
