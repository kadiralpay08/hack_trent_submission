"""Flash the glove boards from the terminal, without opening the Arduino IDE.

  python flash.py cam                  build and flash the ESP32-CAM glove (firmware/glove_imu)
  python flash.py cam --ip auto        ...sending to THIS laptop's current WiFi address
  python flash.py cam --ip 172.20.10.5 ...sending to another computer
  python flash.py s3                   the ESP32-S3 glove (firmware/glove_imu_s3)
  python flash.py cam-check            ESP32-CAM sensor check, USB only (firmware/mpu6050_usb_check)
  python flash.py s3-check             ESP32-S3 sensor check, USB only (firmware/mpu9250_usb_check)
  python flash.py cam --watch          no flashing: just show what the board prints
  python flash.py --list               list the USB serial ports

After flashing it shows the board's messages for a few seconds (--seconds, or --seconds 0 to skip), so
you can see it join WiFi and where it sends ("Sending UDP to ..."). The WiFi password is hidden.

Options:
  --ip X        set LAPTOP_IP in that board's config.h first ("auto" = this laptop, "" = broadcast)
  --ssid NAME   set WIFI_SSID in config.h first (the password stays as it is in config.h; edit the
                file to change it, so it never ends up in your terminal history)
  --port COM6   which serial port (default: found automatically)
  --seconds N   how long to show the board's messages after flashing (default 12)

The ESP32-CAM is flashed on its programmer board, where the sensor is not connected, so it prints
"Sensor 0 Hz, ... read errors" there. That is expected; move it to the breadboard afterwards.
The ESP32-S3 plugs straight in by USB-C. If an upload cannot connect, hold BOOT, tap RST, let go of BOOT.
Uses the arduino-cli that comes with the Arduino IDE, and the ESP32 board package already installed.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
CLI_PATHS = [r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe")]
# name: (sketch folder, board, has config.h, USB chip vendor id, plugged in through the programmer board)
TARGETS = {
    "cam":       ("glove_imu", "esp32:esp32:esp32cam", True, "1A86", True),
    "s3":        ("glove_imu_s3", "esp32:esp32:esp32s3:CDCOnBoot=cdc", True, "303A", False),
    "cam-check": ("mpu6050_usb_check", "esp32:esp32:esp32cam", False, "1A86", True),
    "s3-check":  ("mpu9250_usb_check", "esp32:esp32:esp32s3:CDCOnBoot=cdc", False, "303A", False),
}


def find_cli():
    for path in [shutil.which("arduino-cli"), *CLI_PATHS]:
        if path and os.path.exists(path):
            return path
    raise SystemExit("arduino-cli not found. Install the Arduino IDE (it includes arduino-cli), or put arduino-cli on PATH.")


def ports(cli):
    out = subprocess.run([cli, "board", "list", "--format", "json"], capture_output=True, text=True).stdout
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    found = []
    for item in data.get("detected_ports", data if isinstance(data, list) else []):
        port = item.get("port", item)
        if port.get("protocol") == "serial":
            props = port.get("properties", {})
            found.append((port.get("address"), (props.get("vid") or "").upper().replace("0X", ""), port.get("label", "")))
    return found


def pick_port(cli, vid):
    found = ports(cli)
    if not found:
        raise SystemExit("No board found on USB. Plug it in (the ESP32-CAM sits on its programmer board) and check the "
                         "cable carries data, not just power.")
    matching = [p for p in found if p[1] == vid]
    if len(matching) == 1 or (matching and len(found) > 1):
        return matching[0][0]
    if len(found) == 1:
        return found[0][0]
    raise SystemExit("Several serial ports: " + ", ".join(p[0] for p in found) + ". Choose one with --port.")


def this_laptop_ip():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Wi-Fi).IPAddress"],
                         capture_output=True, text=True).stdout.split()
    ips = [ip for ip in out if not ip.startswith(("127.", "169.254."))]
    if not ips:
        raise SystemExit("This laptop has no WiFi address. Join the phone hotspot first.")
    return ips[0]


def set_config(folder, ip=None, ssid=None):
    path = os.path.join(folder, "config.h")
    if not os.path.exists(path):
        shutil.copy(os.path.join(folder, "config.example.h"), path)
        print(f"Made {os.path.relpath(path, ROOT)} from config.example.h. Put the WiFi password in it, then run this again.")
        if ssid is None:
            raise SystemExit(1)
    text = open(path, encoding="utf-8").read()
    for name, value in (("LAPTOP_IP", ip), ("WIFI_SSID", ssid)):
        if value is not None:
            text, n = re.subn(rf'(const char\*\s*{name}\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{value}"', text)
            if not n:
                raise SystemExit(f"Could not find {name} in {path}")
    open(path, "w", encoding="utf-8").write(text)
    show = re.search(r'LAPTOP_IP\s*=\s*"([^"]*)"', text)
    net = re.search(r'WIFI_SSID\s*=\s*"([^"]*)"', text)
    print(f"config.h: network {net.group(1) if net else '?'!r}, sending to "
          f"{(show.group(1) or 'broadcast') if show else '?'}")


def watch(port, seconds, via_programmer):
    """Print what the board says for a while. The ESP32-CAM programmer resets the chip through RTS; the
    S3's own USB port disappears for a moment while it restarts, so keep trying to open it."""
    import serial
    end, opened, started = time.time() + seconds, False, False
    while time.time() < end:
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = port, 115200, 0.2
            if via_programmer:
                s.dtr = s.rts = False  # or the programmer board holds the chip in reset
            s.open()
        except (serial.SerialException, OSError):
            time.sleep(0.2)
            continue
        if not opened:
            print(f"--- {port}: board messages for {seconds:g} s (Ctrl+C to stop) ---")
            opened = True
        if via_programmer and not started:
            s.rts = True  # reset, so the messages start from the top
            time.sleep(0.1)
            s.rts = False
            started = True
        try:
            buf = b""
            while time.time() < end:
                buf += s.read(256)
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    text = line.decode("ascii", "replace").rstrip()
                    print(re.sub(r"(?i)(password\S*\s*[:=]?\s*).*", r"\1<hidden>", text))
        except (serial.SerialException, OSError):
            pass
        finally:
            s.close()
    if not opened:
        print(f"Could not open {port}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("target", nargs="?", choices=sorted(TARGETS))
    parser.add_argument("--ip", help='set LAPTOP_IP first: an address, "auto" for this laptop, or "" to broadcast')
    parser.add_argument("--ssid", help="set WIFI_SSID first")
    parser.add_argument("--port", help="serial port, such as COM6 (default: found automatically)")
    parser.add_argument("--seconds", type=float, default=12, help="show board messages for this long after flashing")
    parser.add_argument("--watch", action="store_true", help="do not flash: only show the board's messages")
    parser.add_argument("--list", action="store_true", help="list serial ports and stop")
    args = parser.parse_args(argv)
    cli = find_cli()
    if args.list:
        for address, vid, label in ports(cli) or [("(none)", "", "")]:
            kind = {"1A86": "CH340: ESP32-CAM programmer board", "303A": "ESP32-S3 USB"}.get(vid, label)
            print(f"{address}  {kind}")
        return 0
    if not args.target:
        parser.error("say which board: cam, s3, cam-check or s3-check")
    sketch, fqbn, has_config, vid, via_programmer = TARGETS[args.target]
    folder = os.path.join(ROOT, "firmware", sketch)
    port = args.port or pick_port(cli, vid)
    if not args.watch:
        if has_config:
            ip = this_laptop_ip() if args.ip == "auto" else args.ip
            set_config(folder, ip, args.ssid)
        elif args.ip is not None or args.ssid is not None:
            print(f"Note: {args.target} is a USB-only check: it has no WiFi, so --ip and --ssid do nothing.")
        build = os.path.join(tempfile.gettempdir(), "glove_build", args.target)  # outside the repo: it holds the password
        print(f"Building {sketch} for {fqbn} and flashing it to {port}...")
        result = subprocess.run([cli, "compile", "--fqbn", fqbn, "--build-path", build, "--upload", "--port", port, folder],
                                capture_output=True, text=True)
        out = result.stdout + result.stderr
        for line in out.splitlines():
            if re.search(r"Sketch uses|Global variables|error|Error|Hash of data verified|Hard reset", line) and "Writing at" not in line:
                print("  " + line.strip())
        if result.returncode != 0:
            if "Failed to connect" in out or "No serial data" in out:
                print("Could not talk to the board. ESP32-CAM: is it seated on the programmer? ESP32-S3: hold BOOT, "
                      "tap RST, let go of BOOT, and run this again.")
            return result.returncode
        print("Flashed.")
    if args.seconds > 0:
        watch(port, args.seconds, via_programmer)
        if via_programmer and not args.watch and has_config:
            print("Now move it to the breadboard. Check the data with: python glove_check.py --until 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
