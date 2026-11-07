import argparse
import json
import os
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import glove_receiver
import phyphox_imu
import punch_session as ps

ACC = (["accX", "accY", "accZ"], "acc_time")
GYRO = (["gyrX", "gyrY", "gyrZ"], "gyr_time")
GYRO_RAD = 0.1  # rad/s about x


class FakePhyphox:
    """A phone that answers the way phyphox's remote-interface spec says: a bare name is the last value,
    "full" is everything, "N" is what comes after the first value above N, and "N|ref" applies N to buffer ref.
    The accelerometer runs at 100 Hz and the gyroscope at 50 Hz, offset, so the two timelines differ."""

    def __init__(self, measuring=True):
        self.start, self.measuring, self.session, self.requests = time.time(), measuring, "s1", []

    def buffers(self):
        elapsed = time.time() - self.start
        acc_t = np.arange(0, elapsed, 0.01)
        gyr_t = np.arange(0.005, elapsed, 0.02)
        return {"acc_time": acc_t, "accX": np.zeros_like(acc_t), "accY": np.zeros_like(acc_t),
                "accZ": np.full_like(acc_t, 9.80665), "gyr_time": gyr_t, "gyrX": np.full_like(gyr_t, GYRO_RAD),
                "gyrY": np.zeros_like(gyr_t), "gyrZ": np.zeros_like(gyr_t)}

    def get(self, params):
        data, out = self.buffers(), {}
        for name, value in params:
            if name not in data:
                continue  # nonexistent buffers are silently left out
            values = data[name]
            if value == "":
                chosen = values[-1:]
            elif value == "full":
                chosen = values
            else:
                number, _, ref = value.partition("|")
                reference = data[ref or name]
                above = np.nonzero(reference > float(number))[0]
                chosen = values[above[0]:] if len(above) else values[:0]
            out[name] = {"size": 0, "updateMode": "partial", "buffer": [float(v) for v in chosen]}
        return {"buffer": out, "status": {"session": self.session, "measuring": self.measuring,
                                          "timedRun": False, "countDown": 0}}


def serve(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qsl(url.query, keep_blank_values=True)
            fake.requests.append(self.path)
            if url.path == "/get":
                body = fake.get(params)
            elif url.path == "/config":
                body = {"title": "Fake", "buffers": [{"name": n, "size": 0} for n in fake.buffers()], "export": []}
            elif url.path == "/control":
                body = {"result": True}
            else:
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class PickBuffersTests(unittest.TestCase):
    def test_finds_accelerometer_and_gyroscope(self):
        names = ["accX", "accY", "accZ", "acc", "acc_time", "gyrX", "gyrY", "gyrZ", "gyr_time", "gyr"]
        self.assertEqual(phyphox_imu.pick_buffers(names), {"acc": ACC, "gyro": GYRO})

    def test_other_spellings(self):
        names = ["gyroX", "gyroY", "gyroZ", "gyro_time", "acc_x", "acc_y", "acc_z", "acc_time"]
        found = phyphox_imu.pick_buffers(names)
        self.assertEqual(found["gyro"], (["gyroX", "gyroY", "gyroZ"], "gyro_time"))
        self.assertEqual(found["acc"], (["acc_x", "acc_y", "acc_z"], "acc_time"))

    def test_missing_kinds_are_left_out(self):
        self.assertEqual(list(phyphox_imu.pick_buffers(["accX", "accY", "accZ", "acc_time", "light"])), ["acc"])
        self.assertEqual(phyphox_imu.pick_buffers(["light", "lux_time"]), {})


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakePhyphox()
        self.server = serve(self.fake)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        self.receiver = None

    def tearDown(self):
        if self.receiver:
            self.receiver.close()
        self.server.shutdown()
        self.server.server_close()

    def start(self, **kwargs):
        self.receiver = phyphox_imu.PhyphoxReceiver(self.address, ACC, kwargs.pop("gyro", GYRO), interval=0.05, **kwargs)
        return self.receiver

    def wait_for(self, count, seconds=6):
        end = time.time() + seconds
        while time.time() < end and self.receiver.total < count:
            time.sleep(0.05)

    def test_units_and_values(self):
        receiver = self.start()
        self.wait_for(60)
        self.assertGreaterEqual(receiver.total, 60)
        sample = receiver.latest()
        np.testing.assert_allclose(sample.accel, [0, 0, 1.0], atol=1e-6)               # 9.80665 m/s^2 is 1 g
        np.testing.assert_allclose(sample.gyro, [np.degrees(GYRO_RAD), 0, 0], atol=1e-6)  # rad/s become deg/s

    def test_every_reading_once_in_order(self):
        receiver = self.start()
        self.wait_for(100)
        times = [s.t_us for s in list(receiver.samples["R"])]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), len(set(times)), "a reading was sent twice")
        steps = np.diff(times) / 1e6
        self.assertLess(abs(np.median(steps) - 0.01), 0.002)  # the accelerometer's 100 Hz is the timeline

    def test_starts_from_now_not_from_the_whole_buffer(self):
        time.sleep(1.0)  # the phone has been measuring for a while before the laptop connects
        receiver = self.start()
        self.wait_for(20)
        self.assertTrue(all("=full" not in r for r in self.fake.requests))
        self.assertLess(receiver.total, 150)  # not the thousand-odd readings already in the buffer

    def test_accel_only(self):
        receiver = self.start(gyro=None)
        self.wait_for(30)
        self.assertGreaterEqual(receiver.total, 30)
        np.testing.assert_allclose(receiver.latest().gyro, [0, 0, 0])

    def test_recovers_when_the_phone_restarts_its_clock(self):
        # The experiment is cleared or restarted in the app: its time goes back to zero, so nothing is "newer"
        # than the last reading the laptop saw, until the clock catches up. The receiver must notice and re-sync.
        self.fake.start = time.time() - 100  # the phone has been measuring for 100 s, so waiting for the clock to
        receiver = self.start()              # catch up again would take 100 s, far longer than this test waits
        self.wait_for(30)
        self.assertGreaterEqual(receiver.total, 30)
        self.fake.start = time.time()  # phyphox's time restarts at 0
        before = receiver.total
        end = time.time() + 6
        while time.time() < end and receiver.total < before + 30:
            time.sleep(0.05)
        self.assertGreaterEqual(receiver.total, before + 30, "readings never resumed after the phone's clock restarted")

    def test_not_measuring_is_reported(self):
        self.fake.measuring = False
        receiver = self.start()
        time.sleep(0.5)
        self.assertIs(receiver.measuring, False)

    def test_unreachable_phone_is_reported_not_raised(self):
        self.server.shutdown()
        self.server.server_close()
        receiver = self.start(timeout=0.3)  # a refused connection to a closed port is slow to report on Windows
        time.sleep(1.5)
        self.assertTrue(receiver.error)
        self.assertEqual(receiver.total, 0)
        self.server = serve(FakePhyphox())  # so tearDown has something to close


class RightArmPhoneTests(unittest.TestCase):
    def test_the_phone_feeds_the_arm_s_sensor_buffers(self):
        server = serve(FakePhyphox())
        sensors = None
        try:
            sensors = ps.LiveSensors(ps.load_config()["segment"], arm=ps.ARMS["right"],
                                     phone=f"127.0.0.1:{server.server_address[1]}")
            time.sleep(2.0)
            self.assertTrue(sensors.glove_ok())
            self.assertGreater(sensors.samples_seen, 50)
            self.assertTrue(sensors.segmenter.ready)  # a still phone is a still arm
            imu, _ = sensors.window(0, time.time() + 10)
            self.assertGreater(len(imu["t"]), 50)
            self.assertNotIn("mag", imu)              # a phone supplies no magnetometer, which is allowed
            np.testing.assert_allclose(np.linalg.norm(imu["accel"], axis=1), 1.0, atol=1e-6)
        finally:
            if sensors:
                sensors.close()
            server.shutdown()
            server.server_close()

    def test_a_phone_is_only_for_the_right_arm(self):
        with self.assertRaises(SystemExit):
            ps.phone_for(argparse.Namespace(phone="172.20.10.1", arm="left"))
        self.assertEqual(ps.phone_for(argparse.Namespace(phone="172.20.10.1", arm="right")), "172.20.10.1")
        self.assertIsNone(ps.phone_for(argparse.Namespace(phone=None, arm="left")))

    def test_phone_and_simulate_do_not_mix(self):
        with self.assertRaises(SystemExit):
            ps.LiveSensors(ps.load_config()["segment"], simulate=True, phone="127.0.0.1:1")

    def test_an_unreachable_phone_exits_with_a_message(self):
        with self.assertRaises(SystemExit) as caught:
            ps.LiveSensors(ps.load_config()["segment"], phone="127.0.0.1:1")
        self.assertIn("Cannot reach phyphox", str(caught.exception))


def feed(sensors, start, seconds, hz, burst_at=None, hole=None):
    """Send a still sensor at `hz`, with a punch-like burst at burst_at and no samples inside hole=(from, to)."""
    for i in range(int(seconds * hz)):
        t = start + i / hz
        if hole and hole[0] <= t < hole[1]:
            continue
        hit = burst_at is not None and burst_at <= t < burst_at + 0.06
        accel = np.array([7.0, 0.0, 1.0]) if hit else np.array([0.0, 0.0, 1.0])
        sample = glove_receiver.Sample(int(t * 1e6), accel, np.zeros(3), t=t)
        sensors._on_sample("R", sample)


class SampleRateTests(unittest.TestCase):
    def setUp(self):
        self.sensors = ps.LiveSensors(ps.load_config()["segment"], port=0)  # no glove needed: samples are fed in

    def tearDown(self):
        self.sensors.close()

    def test_rate_follows_the_sensor(self):
        self.assertEqual(self.sensors.sample_rate_hz(), 500.0)   # nothing yet: assume the glove
        feed(self.sensors, 1000.0, 2.0, 100)
        self.assertAlmostEqual(self.sensors.sample_rate_hz(), 100.0, delta=1.0)

    def test_the_glove_still_reads_500(self):
        feed(self.sensors, 1000.0, 2.0, 500)
        self.assertAlmostEqual(self.sensors.sample_rate_hz(), 500.0, delta=5.0)

    def test_a_few_dropped_packets_do_not_change_it(self):
        feed(self.sensors, 1000.0, 3.0, 100, hole=(1001.0, 1001.3))
        self.assertAlmostEqual(self.sensors.sample_rate_hz(), 100.0, delta=2.0)


class CaptureAcceptsAPhonesRateTests(unittest.TestCase):
    """Regression: capture rejected every block that had under 60% of a 500 Hz glove's samples, so a phone at
    about 100 Hz lost all of its punches, reported as MISSED."""

    def setUp(self):
        import tempfile
        import punch_capture
        self.tmp = tempfile.TemporaryDirectory()
        self.sensors = ps.LiveSensors(ps.load_config()["segment"], port=0)
        self.cap = punch_capture.PromptedCapture(self.sensors, ps.load_config(), 5, self.tmp.name, "test", ps.ARMS["right"])
        self.cap.say = lambda *a, **k: None

    def tearDown(self):
        self.sensors.close()
        self.tmp.cleanup()

    def test_a_punch_at_phone_rate_is_saved(self):
        feed(self.sensors, 1000.0, 5.0, 100, burst_at=1002.0)
        segment = self.cap.save_block("CROSS", 1001.9, 1003.5, quiet=True)
        self.assertIsNotNone(segment, "a 100 Hz punch was rejected")
        self.assertEqual(self.cap.counts["CROSS"], 1)

    def test_a_real_gap_in_the_data_is_still_rejected(self):
        feed(self.sensors, 1000.0, 5.0, 100, burst_at=1002.0, hole=(1001.2, 1002.9))  # 1.7 s of nothing
        self.assertIsNone(self.cap.save_block("CROSS", 1001.9, 1003.5, quiet=True))
        self.assertEqual(self.cap.counts["CROSS"], 0)


if __name__ == "__main__":
    unittest.main()
