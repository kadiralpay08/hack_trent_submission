import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import phone_imu


def rows(n, accel=(0.0, 0.0, 9.80665), gyro=(0.0, 0.0, 0.0), start_ms=1000.0, step_ms=1000 / 60):
    return [[start_ms + i * step_ms, *accel, *gyro] for i in range(n)]


class PhoneReceiverTests(unittest.TestCase):
    def setUp(self):
        self.got = []
        self.receiver = phone_imu.PhoneReceiver(port=0, host="127.0.0.1", on_sample=lambda g, s: self.got.append((g, s)))

    def tearDown(self):
        self.receiver.close()

    def post(self, payload, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.receiver.port}/data", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def status_of(self, payload, raw=None):
        try:
            return self.post(payload, raw)
        except urllib.error.HTTPError as error:
            return error.code

    def test_metres_per_second_squared_become_g(self):
        self.assertEqual(self.post({"samples": rows(20)}), 204)
        self.assertEqual(len(self.got), 20)
        glove, sample = self.got[0]
        self.assertEqual(glove, "R")
        np.testing.assert_allclose(sample.accel, [0, 0, 1.0], atol=1e-6)

    def test_readings_already_in_g_are_left_alone(self):
        self.post({"samples": rows(20, accel=(0.0, 0.0, 1.0))})
        np.testing.assert_allclose(self.got[0][1].accel, [0, 0, 1.0], atol=1e-6)

    def test_waits_for_enough_samples_to_tell_the_units(self):
        self.post({"samples": rows(4)})
        self.assertEqual(self.got, [])
        self.post({"samples": rows(8, start_ms=2000)})
        self.assertEqual(len(self.got), 12)

    def test_flip_accel_and_gyro_in_radians(self):
        self.receiver.flip_accel, self.receiver.gyro_rad = True, True
        self.post({"samples": rows(20, accel=(0, 0, 9.80665), gyro=(np.pi, 0, 0))})
        sample = self.got[0][1]
        np.testing.assert_allclose(sample.accel, [0, 0, -1.0], atol=1e-6)
        np.testing.assert_allclose(sample.gyro, [180, 0, 0], atol=1e-6)

    def test_timing_follows_the_phone_clock(self):
        self.post({"samples": rows(30)})
        self.assertAlmostEqual(self.got[-1][1].dt, 1 / 60, places=3)
        self.assertLess(abs(self.got[-1][1].t - time.time()), 2)

    def test_latest_and_recent(self):
        self.post({"samples": rows(20)})
        self.assertIsNotNone(self.receiver.latest())
        self.assertGreater(len(self.receiver.recent(5)), 0)

    def test_bad_requests_are_refused(self):
        self.assertEqual(self.status_of(None, raw=b"not json"), 400)
        self.assertEqual(self.status_of({"nope": []}), 400)
        self.assertEqual(self.status_of({"samples": [[1, 2, 3]]}), 400)
        self.assertEqual(self.status_of({"samples": [["a"] * 7]}), 400)
        self.assertEqual(self.receiver.total, 0)

    def test_serves_the_page(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.receiver.port}/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Right hand sensor", response.read())


class CertTests(unittest.TestCase):
    @unittest.skipUnless(phone_imu.find_openssl(), "OpenSSL not installed")
    def test_certificate_is_made_once_and_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            cert, key = phone_imu.ensure_cert("10.1.2.3", folder)
            self.assertTrue(os.path.getsize(cert) > 0 and os.path.getsize(key) > 0)
            stamp = os.path.getmtime(cert)
            self.assertEqual(phone_imu.ensure_cert("10.1.2.3", folder), (cert, key))
            self.assertEqual(os.path.getmtime(cert), stamp)


if __name__ == "__main__":
    unittest.main()
