"""Right-hand sensor: a phone's accelerometer and gyroscope, received over WiFi.

The laptop serves a small web page (phone/index.html). Open it in the phone's browser, tap Start, and the
phone sends its motion readings back in batches. There is nothing to install on the phone. Browsers only give
out motion data to secure (https) pages, so the page is served with a self-made certificate and the phone asks
you to accept it once.

  python phone_imu.py               listen, and print the rate and readings of the phone as they arrive
  python phone_imu.py --no-tls      plain http, for testing on this computer only (a phone will refuse to share
                                    motion data from it)

Samples come out in the same form as glove_receiver.py's (units of g and deg/s, on the laptop clock), under the
glove id "R", but in their own receiver: nothing here touches the left glove's pipeline. Phones report motion at
about 60 Hz, far below the glove's 500 Hz, so a punch finder for this hand needs its own settings.
"""
import argparse
import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import glove_receiver

PORT = 8443
GLOVE = "R"
G = 9.80665                     # m/s^2 in one g; browsers report acceleration in m/s^2
ROOT = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(ROOT, "phone", "index.html")
CERT_DIR = os.path.join(ROOT, "certs")  # Git ignores it
FIELDS = 7                      # time ms, ax ay az, gx gy gz
SETTLE = 10                     # samples used to tell m/s^2 from g

# How the phone sits on the hand: hand axis i = AXIS_SIGN[i] * phone axis AXIS_MAP[i]. Phone axes: x to the
# right of the screen, y to the top, z out of the screen.
AXIS_MAP = [0, 1, 2]
AXIS_SIGN = np.array([1, 1, 1])


def corrected(accel_g, gyro_dps):
    """Phone axes to hand axes."""
    return accel_g[AXIS_MAP] * AXIS_SIGN, gyro_dps[AXIS_MAP] * AXIS_SIGN


class PhoneReceiver:
    """Listens for the phone's batches. samples[glove_id] holds the most recent readings, like GloveReceiver.

    on_sample(glove, sample), if given, is called for every sample in arrival order, on a server thread.
    port=None starts no server, for code that supplies the readings itself.
    flip_accel reverses the sign of acceleration (some phones report gravity the other way up); gyro_rad is for
    a browser that reports rotation in rad/s instead of deg/s.
    """

    def __init__(self, port=PORT, host="0.0.0.0", cert=None, history=1000, on_sample=None,
                 flip_accel=False, gyro_rad=False):
        self.samples, self.history, self.on_sample = {}, history, on_sample
        self.flip_accel, self.gyro_rad = flip_accel, gyro_rad
        self.clock = glove_receiver.Clock()
        self.divisor = None        # 1 if the phone reports g, G if it reports m/s^2; decided from its first samples
        self.pending = []
        self.lock = threading.Lock()
        self.total = 0
        self.last_arrival = 0.0
        self.last_client = None
        self.server = None
        if port is None:
            return  # no server: only the sample handling below (phyphox_imu.py feeds it by calling ingest)

        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep the connection open between batches

            def log_message(self, *args):
                pass

            def _reply(self, code, body=b"", kind="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.split("?")[0] in ("/", "/index.html"):
                    with open(PAGE, "rb") as f:
                        self._reply(200, f.read(), "text/html; charset=utf-8")
                else:
                    self._reply(404)

            def do_POST(self):
                if self.path != "/data":
                    return self._reply(404)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 1_000_000:
                        raise ValueError("bad length")
                    rows = json.loads(self.rfile.read(length))["samples"]
                    receiver.ingest(rows, time.time(), self.client_address[0])
                except (ValueError, KeyError, TypeError):
                    return self._reply(400)
                self._reply(204)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                sock, addr = super().get_request()
                if context:
                    # Shake hands on the connection's own thread, so a phone that stalls can't block the others.
                    sock = context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
                return sock, addr

            def handle_error(self, request, client_address):
                pass  # a phone dropping a connection or refusing the certificate is routine

        context = None
        if cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*cert)
        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def ingest(self, rows, arrival, client=None):
        """Turn a batch of [t_ms, ax, ay, az, gx, gy, gz] rows into samples. Raises ValueError on bad input."""
        clean = []
        for row in rows:
            if len(row) != FIELDS:
                raise ValueError("a row needs 7 numbers")
            values = np.array(row, dtype=float)
            if np.all(np.isfinite(values)):
                clean.append(values)
        with self.lock:
            self.last_arrival, self.last_client = arrival, client
            if self.divisor is None:
                self.pending += clean
                if len(self.pending) < SETTLE:
                    return
                norms = np.linalg.norm([r[1:4] for r in self.pending], axis=1)
                self.divisor = G if np.median(norms) > 3 else 1.0  # resting, it reads about 9.8 or about 1
                clean, self.pending = self.pending, []
            made = [self._sample(v, arrival) for v in clean]
        for sample in made:
            if self.on_sample:
                self.on_sample(GLOVE, sample)

    def _sample(self, v, arrival):
        accel = v[1:4] / self.divisor * (-1 if self.flip_accel else 1)
        gyro = np.degrees(v[4:7]) if self.gyro_rad else v[4:7]
        accel, gyro = corrected(accel, gyro)
        sample = glove_receiver.Sample(int(v[0] * 1000) & glove_receiver.Clock.MASK, accel, gyro)
        self.clock.stamp(sample, arrival)
        self.samples.setdefault(GLOVE, deque(maxlen=self.history)).append(sample)
        self.total += 1
        return sample

    def latest(self, glove=GLOVE):
        buf = self.samples.get(glove)
        return buf[-1] if buf else None

    def recent(self, seconds, glove=GLOVE):
        cutoff = time.time() - seconds
        return [s for s in list(self.samples.get(glove, [])) if s.t >= cutoff]


def lan_ip():
    """This computer's address on the network it uses to reach the outside, which is the hotspot's."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet is sent
        return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


def find_openssl():
    found = shutil.which("openssl")
    if found:
        return found
    for path in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files (x86)\Git\usr\bin\openssl.exe"):
        if os.path.exists(path):
            return path
    return None


def ensure_cert(ip, directory=CERT_DIR):
    """A self-signed certificate for this address. Made once per address, then reused. Returns (cert, key)."""
    os.makedirs(directory, exist_ok=True)
    cert, key = (os.path.join(directory, f"phone_{ip}.{ext}") for ext in ("crt", "key"))
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    exe = find_openssl()
    if not exe:
        raise SystemExit("Needs OpenSSL to make the https certificate. It comes with Git for Windows "
                         "(C:\\Program Files\\Git\\usr\\bin\\openssl.exe); install Git, or put openssl on PATH.")
    subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "30", "-subj", "/CN=boxing-phone", "-addext", f"subjectAltName=IP:{ip}"],
                   check=True, capture_output=True)
    return cert, key


def show_qr(url, directory=CERT_DIR):
    """Print the link as a QR code and open it as a picture, so the phone's camera can read it. Needs the
    optional qrcode package; without it nothing happens. Returns True if a code was shown."""
    try:
        import qrcode
    except ImportError:
        return False
    code = qrcode.QRCode(border=2)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(invert=True)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "phone_link.png")
    code.make_image(fill_color="black", back_color="white").save(path)
    if hasattr(os, "startfile"):
        os.startfile(path)  # opens in the default picture viewer, which is easier to scan than the terminal
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--ip", help="this computer's address on the phone's network (default: found automatically)")
    parser.add_argument("--no-tls", action="store_true", help="plain http, for testing here only")
    parser.add_argument("--no-qr", action="store_true", help="don't show a QR code for the link")
    parser.add_argument("--flip-accel", action="store_true",
                        help="reverse acceleration if the phone lying flat, screen up, reads -1 g on z")
    parser.add_argument("--gyro-rad", action="store_true", help="the browser reports rotation in rad/s, not deg/s")
    args = parser.parse_args()

    ip = args.ip or lan_ip()
    cert = None if args.no_tls else ensure_cert(ip)
    try:
        receiver = PhoneReceiver(port=args.port, cert=cert, flip_accel=args.flip_accel, gyro_rad=args.gyro_rad)
    except OSError as error:
        raise SystemExit(f"Cannot listen on port {args.port}: {error}")
    scheme = "http" if args.no_tls else "https"
    url = f"{scheme}://{ip}:{receiver.port}"
    print(f"On the phone, open  {url}  (same WiFi as this laptop)")
    if not args.no_qr and not show_qr(url):
        print("(pip install qrcode to get a QR code for this link)")
    print("Accept the certificate warning once, tap Start, allow motion access, keep the screen on. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
            recent = receiver.recent(0.5)
            if not recent:
                print("waiting for the phone..." if not receiver.total else "phone: no data")
                continue
            peak = max(np.linalg.norm(s.accel) for s in recent)
            print(f"phone {GLOVE}: {len(recent) * 2} Hz, peak accel {peak:.1f} g, gyro {receiver.latest().gyro.round(0)}")
    except KeyboardInterrupt:
        pass
    finally:
        receiver.close()


if __name__ == "__main__":
    main()
