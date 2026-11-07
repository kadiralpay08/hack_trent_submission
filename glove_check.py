"""Check that the glove is sending good data, one step at a time, and say what to fix if it is not.

  python glove_check.py              wait for the real glove on UDP port 5005
  python glove_check.py --simulate   try this tool with a fake glove (no hardware)

Steps:
  1  Hear the glove      any packet arrives at all
  2  Data quality        frames arrive intact, at the right rate, with few gaps
  3  At rest             hold it still: gravity should read 1 g and rotation near zero
  4  Punches             throw a few; each is counted with its impact strength

Nothing is pressed or sent to any game.
"""
import argparse
import socket
import statistics
import sys
import threading
import time

import numpy as np

import glove_punch
import glove_receiver

PORT = glove_receiver.PORT
FRAME_LEN = glove_receiver.FRAME_LEN


def say(text=""):
    print(text, flush=True)


def laptop_addresses():
    found = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("203.0.113.1", 9))  # documentation address; connecting a UDP socket sends nothing
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def collect(sock, seconds, on_packet=None):
    """Receive for `seconds`. Returns a list of (arrival_time, sender_ip, data)."""
    packets, end = [], time.time() + seconds
    while time.time() < end:
        sock.settimeout(max(0.01, min(0.2, end - time.time())))
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        item = (time.time(), addr[0], data)
        packets.append(item)
        if on_packet:
            on_packet(*item)
    return packets


def step1_hear(sock, wait_seconds):
    say("STEP 1 of 4: listening for the glove on UDP port %d (up to %d s)..." % (PORT, wait_seconds))
    say("  Power the glove now. The ESP32 prints 'Connected' on its serial monitor when it joins Wi-Fi.")
    end, last_note = time.time() + wait_seconds, time.time()
    first = []
    while time.time() < end and not first:
        first = collect(sock, 1.0)
        if not first and time.time() - last_note >= 5:
            say("  ... nothing yet (%d s left)" % max(0, end - time.time()))
            last_note = time.time()
    if first:
        senders = sorted({p[1] for p in first})
        say("  PASS: heard the glove, from %s" % ", ".join(senders))
        return first
    say("  FAIL: no packets arrived.")
    try:
        loop = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        loop.sendto(b"self-test", ("127.0.0.1", PORT))
        sock.settimeout(1.0)
        sock.recvfrom(64)
        say("  This computer's own socket works (a packet sent to itself arrived), so the problem is the")
        say("  glove, the Wi-Fi network, or the Windows firewall.")
    except OSError as e:
        say("  Even a packet sent to itself did not arrive (%s), so something is wrong on this computer." % e)
    say("  Check these, most likely first:")
    say("   1. Network. The glove and this laptop must be on the SAME Wi-Fi network. This laptop's address(es): %s"
        % (", ".join(laptop_addresses()) or "unknown"))
    say("      University Wi-Fi (eduroam) will not work: the ESP32 cannot log into it, and it also blocks")
    say("      devices from talking to each other. Use a phone hotspot (2.4 GHz band) or a travel router,")
    say("      and connect both the laptop and the glove to it.")
    say("   2. Firmware settings (config.h): WIFI_SSID and WIFI_PASSWORD must be that network. Set LAPTOP_IP")
    say("      to this laptop's address ON THAT NETWORK (leaving it empty uses broadcast, which many networks block).")
    say("   3. Windows firewall. The first time Python listens, Windows asks to allow it. Choose 'Allow', and")
    say("      tick the box for the network type you are on. Public networks are blocked by default.")
    say("   4. The glove itself: open its serial monitor at 115200 baud. It reports 'MPU6050 not found' for a")
    say("      wiring problem, and the line 'Sending UDP to <address> port 5005' once it is on Wi-Fi.")
    return None


def parse(packets, clock_per_sender=None):
    """Turn packets into samples. Returns (samples, total_bytes, junk_bytes)."""
    samples, total, good = [], 0, 0
    for arrival, sender, data in packets:
        total += len(data)
        clock = None
        if clock_per_sender is not None:
            clock = clock_per_sender.setdefault(sender, glove_receiver.Clock())
        for s in glove_receiver.parse_frames(data):
            good += FRAME_LEN
            if clock:
                clock.stamp(s, arrival)
            samples.append(s)
    return samples, total, total - good


def step2_quality(sock, seconds, expected_hz, check_rate=True):
    say("")
    say("STEP 2 of 4: checking data quality for %d s..." % seconds)
    packets = collect(sock, seconds)
    samples, total, junk = parse(packets)
    if not samples:
        say("  FAIL: %d packets arrived but none held a valid frame. The data is corrupted or in the wrong" % len(packets))
        say("  format (are two devices sending to the same port?).")
        return False
    ok = True
    t_us = np.array([s.t_us for s in samples], dtype=np.int64)
    steps = np.diff(t_us) & 0xFFFFFFFF
    steps = steps[steps < 5_000_000] / 1e6  # seconds between frames; a bigger jump is a reboot
    sensor_hz = 1.0 / np.median(steps) if len(steps) else 0.0
    nominal = 1.0 / expected_hz
    lost = float(np.sum(np.maximum(steps / nominal - 1, 0)[steps > 1.9 * nominal])) if len(steps) else 0.0
    loss_pct = 100.0 * lost / max(lost + len(samples), 1)
    gaps = int(np.sum(steps > 0.05)) if len(steps) else 0
    say("  packets %d | frames %d (%.0f per second) | sensor rate from its own clock: %.0f Hz (expect about %d)"
        % (len(packets), len(samples), len(samples) / seconds, sensor_hz, expected_hz))
    say("  junk bytes: %d of %d | estimated lost frames: %.1f%% | gaps longer than 50 ms: %d"
        % (junk, total, loss_pct, gaps))
    if check_rate and abs(sensor_hz - expected_hz) > 0.15 * expected_hz:
        say("  PROBLEM: the sensor is not sampling at the expected rate. Compare with the 'Sensor ... Hz' line"
            " on the ESP32 serial monitor.")
        ok = False
    if junk > 0.02 * total:
        say("  PROBLEM: more than 2%% of the bytes are not valid frames. Check for another device sending to port %d." % PORT)
        ok = False
    if check_rate and (loss_pct > 10 or gaps > 3):
        say("  PROBLEM: the Wi-Fi link is dropping data. Move closer, use a 2.4 GHz hotspot, and avoid a busy network.")
        ok = False
    say("  " + ("PASS" if ok else "FAIL") + ": data quality")
    return ok


def step3_rest(sock, seconds):
    say("")
    say("STEP 3 of 4: HOLD THE GLOVE STILL for %d s (rest it on the table)..." % seconds)
    samples, _, _ = parse(collect(sock, seconds))
    if len(samples) < 20:
        say("  FAIL: not enough data.")
        return False
    accel = np.array([float(np.linalg.norm(s.accel)) for s in samples])
    gyro = np.array([float(np.linalg.norm(s.gyro)) for s in samples])
    a, g = statistics.median(accel), statistics.median(gyro)
    a_sd = 1.4826 * float(np.median(np.abs(accel - a)))  # typical noise, not thrown by a few bumps
    moving = 100.0 * float(np.mean(np.abs(accel - 1.0) > 0.5))
    say("  gravity reads %.2f g (should be 1.00) with noise %.3f g | rotation at rest %.1f deg/s (should be under 5)"
        % (a, a_sd, g))
    if moving > 10:
        say("  NOTE: the glove moved noticeably during %.0f%% of this step. Rest it on the table for a cleaner reading."
            % moving)
    ok = True
    if 0.9 <= a <= 1.1:
        pass
    elif 6.5 <= a <= 9.5:
        say("  PROBLEM: gravity reads about 8 g. The sensor has reset to its +-2 g range while the code assumes")
        say("  +-16 g. Power-cycle the glove (the firmware also re-configures it every quarter of a second).")
        ok = False
    elif a < 0.3:
        say("  PROBLEM: the accelerometer reads almost zero. The sensor may be disconnected or not awake.")
        ok = False
    else:
        say("  PROBLEM: gravity should read 1 g. Check the sensor range settings in the firmware and in glove_receiver.py.")
        ok = False
    if a_sd > 0.1:
        say("  PROBLEM: the reading is noisy at rest (%.3f g). Was the glove really still?" % a_sd)
        ok = False
    if g > 5:
        say("  NOTE: the gyroscope reads %.1f deg/s at rest. A small offset is normal; a large one needs"
            " calibration or a re-check that the glove was still." % g)
    say("  " + ("PASS" if ok else "FAIL") + ": at rest")
    return ok


def step4_punches(sock, wanted, seconds):
    say("")
    say("STEP 4 of 4: THROW %d PUNCHES with the glove on (pause between them). Listening for %d s..." % (wanted, seconds))
    clocks, detectors, found = {}, {}, []
    peak_any = [0.0]

    def handle(arrival, sender, data):
        clock = clocks.setdefault(sender, glove_receiver.Clock())
        det = detectors.setdefault(sender, glove_punch.PunchDetector())
        for s in glove_receiver.parse_frames(data):
            clock.stamp(s, arrival)
            peak_any[0] = max(peak_any[0], abs(float(np.linalg.norm(s.accel)) - 1.0))
            punch = det.update(s)
            if punch:
                found.append(punch)
                say("  punch %d: impact %.1f g, lasted %d ms" % (len(found), punch.peak_g, round(punch.duration * 1000)))

    end = time.time() + seconds
    while time.time() < end and len(found) < wanted:
        collect(sock, min(0.5, max(0.01, end - time.time())), handle)
    say("  strongest movement seen: %.1f g beyond gravity (a punch is counted above %.1f g)"
        % (peak_any[0], glove_punch.START_G))
    if len(found) >= wanted:
        say("  PASS: %d punches detected" % len(found))
        return True
    say("  FAIL: %d of %d punches detected." % (len(found), wanted))
    if peak_any[0] < glove_punch.START_G:
        say("  Your hardest movement stayed below the trigger. If you were punching hard, lower START_G in"
            " glove_punch.py (try %.1f)." % max(1.0, peak_any[0] * 0.7))
    else:
        say("  The glove felt hard movements but they did not count as punches. Raise END_G or lower COOLDOWN in glove_punch.py.")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--simulate", action="store_true", help="use a fake glove to try this tool")
    parser.add_argument("--wait", type=int, default=45, help="seconds to wait for the glove (step 1)")
    parser.add_argument("--hz", type=int, default=500, help="sensor rate the firmware should produce")
    parser.add_argument("--punches", type=int, default=5, help="punches to throw in step 4")
    parser.add_argument("--punch-seconds", type=int, default=30)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", PORT))
    except OSError as e:
        say("Cannot listen on UDP port %d: %s" % (PORT, e))
        say("Another program (such as controller.py --glove) is probably using it. Close it and try again.")
        return 1
    if args.simulate:
        say("SIMULATION: a fake 200 Hz glove that punches every 1.5 s. Real hardware is not involved.")
        threading.Thread(target=glove_receiver.simulate, daemon=True).start()
        args.hz = 200
    results = {}
    try:
        if step1_hear(sock, 10 if args.simulate else args.wait) is None:
            return 1
        results["data quality"] = step2_quality(sock, 5, args.hz, check_rate=not args.simulate)
        results["at rest"] = step3_rest(sock, 4)
        results["punches"] = step4_punches(sock, args.punches, args.punch_seconds)
    except KeyboardInterrupt:
        say("\nStopped.")
        return 1
    say("")
    say("SUMMARY: " + " | ".join("%s: %s" % (k, "ok" if v else "PROBLEM") for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
