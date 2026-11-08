"""The arcade punch sounds from the website (website/js/audio.js), made in Python for the training tool.

punch_capture.py plays one of these when it calls a punch, so training sounds like the game:
  JAB     a quick "tik" and a tap                 HOOK      a wide "whoo-oop" and a slap
  CROSS   a falling "pyoo" and a thud             UPPERCUT  a rising zip and a boom
  FREE    a bell, for the "MOVE, DON'T PUNCH" round
Anything else gets the short noise burst the website uses for an unknown punch.

Like the website there are no sound files: every sound is built from square, triangle, sine and sawtooth
tones and filtered noise. This is a port of the website's punch() and roundBell(), so if you change a
sound there, change it here too. render() makes the WAV bytes (no speakers needed, so it can be tested);
play() plays them and waits until they finish, so call it from a thread.

  python punch_sounds.py            play every sound once
  python punch_sounds.py JAB HOOK   play just those
"""
import io
import sys
import wave
from functools import lru_cache

import numpy as np

RATE = 44100
LOUDNESS = 0.8      # every sound is scaled to this fraction of full scale, so a jab is as easy to hear as an uppercut
BLOCK = 32          # samples between filter updates when a noise's cutoff is sweeping
KINDS = ("JAB", "HOOK", "CROSS", "UPPERCUT", "FREE")


class Mix:
    """A mono buffer that tones and noise are added into, at a start time in seconds."""

    def __init__(self, seconds=2.0):
        self.buf = np.zeros(int(RATE * seconds))
        self.end = 0
        self.rng = np.random.default_rng(7)   # the same noise every time, so a sound renders identically

    def _add(self, t, wave_):
        start = int(round(t * RATE))
        self.buf[start:start + len(wave_)] += wave_
        self.end = max(self.end, start + len(wave_))

    @staticmethod
    def _envelope(n, attack, dur, vol):
        """Rise to vol over `attack`, then fall exponentially to nearly nothing at `dur` (Web Audio's ramps)."""
        t = np.arange(n) / RATE
        floor = 0.0001
        fall = vol * (floor / vol) ** (np.clip(t - attack, 0, None) / (dur - attack))
        return np.where(t < attack, floor + (vol - floor) * t / attack, fall)

    @staticmethod
    def _sweep(points, n):
        """Values at each of n samples, moving exponentially through (seconds, value) points, then holding the last."""
        times, values = zip(*points)
        return np.exp(np.interp(np.arange(n) / RATE, times, np.log(values)))

    def tone(self, shape, freq, t, dur, vol, to=None, path=None, attack=0.004):
        """One oscillator. `to` is the end pitch; `path` is [(seconds, pitch), ...] for a sweep with several legs."""
        n = int(dur * RATE)
        legs = list(path) if path else ([(dur, to)] if to else [])
        pitch = self._sweep([(0.0, freq)] + legs, n) if legs else np.full(n, float(freq))
        phase = np.cumsum(pitch) / RATE
        if shape == "sine":
            osc = np.sin(2 * np.pi * phase)
        elif shape == "square":
            osc = np.where(np.sin(2 * np.pi * phase) >= 0, 1.0, -1.0)
        elif shape == "sawtooth":
            osc = 2 * ((phase + 0.5) % 1.0) - 1
        else:  # triangle
            osc = 2 / np.pi * np.arcsin(np.sin(2 * np.pi * phase))
        self._add(t, osc * self._envelope(n, attack, dur, vol))

    def noise(self, kind, freq, t, dur, vol, to=None, q=1.0, attack=0.002):
        """White noise through a lowpass, highpass or bandpass filter whose cutoff slides from freq to `to`."""
        n = int(dur * RATE)
        source = self.rng.uniform(-1, 1, n).tolist()
        cutoff = self._sweep([(0.0, freq), (dur, to)], n) if to else np.full(n, float(freq))
        out, z1, z2 = [0.0] * n, 0.0, 0.0
        for i in range(0, n, BLOCK):
            f = min(cutoff[min(i + BLOCK // 2, n - 1)], RATE * 0.45)
            w0 = 2 * np.pi * f / RATE
            c = np.cos(w0)
            # Web Audio reads Q as decibels for lowpass and highpass, and as a plain number for bandpass
            alpha = np.sin(w0) / (2 * (q if kind == "bandpass" else 10 ** (q / 20)))
            if kind == "lowpass":
                b0, b1, b2 = (1 - c) / 2, 1 - c, (1 - c) / 2
            elif kind == "highpass":
                b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
            else:
                b0, b1, b2 = alpha, 0.0, -alpha
            a0 = 1 + alpha
            b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, -2 * c / a0, (1 - alpha) / a0
            for j in range(i, min(i + BLOCK, n)):  # a plain loop, not scipy: scipy takes 2.5 s to load the first time
                x = source[j]
                y = b0 * x + z1
                z1 = b1 * x - a1 * y + z2
                z2 = b2 * x - a2 * y
                out[j] = y
        self._add(t, np.array(out) * self._envelope(n, attack, dur, vol))


def _throw(m, kind, t):
    """The whoosh of the throw (audio.js _throw)."""
    if kind == "JAB":
        m.noise("highpass", 4000, t, 0.07, 0.22, to=7000)
        m.tone("square", 1200, t, 0.06, 0.1, to=1900)
    elif kind == "CROSS":
        m.noise("bandpass", 2000, t, 0.12, 0.26, to=800, q=1.2)
        m.tone("square", 560, t, 0.12, 0.13, to=240)
    elif kind == "HOOK":
        m.noise("bandpass", 600, t, 0.24, 0.24, to=1800, q=0.9)
        m.tone("sawtooth", 240, t, 0.26, 0.15, path=[(0.12, 720), (0.26, 200)])
    elif kind == "UPPERCUT":
        m.tone("square", 170, t, 0.2, 0.15, to=1500)
        m.tone("triangle", 85, t, 0.2, 0.2, to=600)
        m.noise("highpass", 1500, t, 0.2, 0.16, to=6000)
    else:
        m.noise("bandpass", 1500, t, 0.1, 0.2)


def _impact(m, kind, t):
    """The hit that follows the whoosh (audio.js _impact, the unguarded sounds)."""
    if kind == "JAB":
        m.noise("lowpass", 2200, t, 0.06, 0.3, to=600)
        m.tone("sine", 260, t, 0.08, 0.35, to=120)
    elif kind == "CROSS":
        m.noise("lowpass", 1500, t, 0.1, 0.4, to=400)
        m.tone("sine", 170, t, 0.16, 0.55, to=55)
    elif kind == "HOOK":
        m.noise("bandpass", 3200, t, 0.09, 0.4, to=1100, q=0.8)
        m.tone("sine", 230, t, 0.18, 0.5, to=80)
        m.tone("square", 95, t, 0.1, 0.12, to=60)
        m.noise("bandpass", 1400, t + 0.07, 0.1, 0.22, to=500)
        m.tone("sine", 110, t + 0.07, 0.2, 0.35, to=40)
    elif kind == "UPPERCUT":
        m.noise("lowpass", 1100, t, 0.24, 0.5, to=200)
        m.tone("sine", 120, t, 0.4, 0.8, to=32)
        m.tone("square", 70, t, 0.15, 0.18, to=40)
        m.tone("square", 1760, t + 0.03, 0.09, 0.09)
    else:
        m.noise("lowpass", 1500, t, 0.1, 0.3)


def render(kind):
    """The WAV file (as bytes) for a kind of sound. Made once, then reused."""
    return _render(str(kind).upper())


@lru_cache(maxsize=None)
def _render(kind):
    m = Mix()
    if kind == "FREE":  # audio.js roundBell(1)
        m.tone("triangle", 1175, 0.0, 0.9, 0.25)
        m.tone("sine", 2350, 0.0, 0.5, 0.1)
    else:               # audio.js punch(): the whoosh, then the impact 45 ms later
        _throw(m, kind, 0.0)
        _impact(m, kind, 0.045)
    samples = np.tanh(m.buf[:m.end])  # the website's soft ceiling: a loud moment saturates a little instead of clipping
    samples *= LOUDNESS / max(np.abs(samples).max(), 1e-9)
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((samples * 32767).astype("<i2").tobytes())
    return out.getvalue()


def play(kind):
    """Play a sound and wait for it to end. Windows only. A new sound cuts off one that is still playing."""
    if sys.platform != "win32":
        return
    import winsound
    # SND_MEMORY cannot be combined with SND_ASYNC (Python raises RuntimeError), so this blocks: use a thread.
    winsound.PlaySound(render(kind), winsound.SND_MEMORY)


if __name__ == "__main__":
    import time
    for name in [k.upper() for k in sys.argv[1:]] or list(KINDS):
        print("playing", name)
        play(name)
        time.sleep(0.4)
