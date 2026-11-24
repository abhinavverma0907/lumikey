#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""Lumikey: animated, music- and typing-reactive RGB for Dynamic Lighting (HID LampArray) keyboards.

  lumikey setup [--all-keyboards]     one-time: allow access to your keyboard (asks for your password)
  lumikey doctor                      check that everything is set up, and say how to fix what isn't
  lumikey devices                     list Dynamic Lighting devices that are plugged in
  lumikey device <vid:pid|auto>       pick which device to light (default: the first keyboard)
  lumikey probe                       device details to paste into a GitHub issue
  lumikey-gui                         the control panel

  lumikey                             show the current setting
  lumikey <effect> [color]            e.g. lumikey galaxy, lumikey breathe pink
  lumikey <effect>+<layer>...         stack layers on top, e.g. lumikey nebula+ripple+beat
  lumikey <layer> [color]             a layer on a dark board, e.g. lumikey sparks
  lumikey <color>                     all keys one colour: a name or hex (ff8800)
  lumikey brightness <0-100>
  lumikey speed <0.25-4>
  lumikey all-keyboards on|off        typing on other keyboards (e.g. a laptop's) lights this one too (default on)
  lumikey off                         all keys dark
  lumikey auto                        hand lighting back to the keyboard's built-in effects
  lumikey list                        list effects (by category), layers, colours and key names
  lumikey next | prev                 the next / previous effect, keeping your layers
  lumikey random [category|favorites] a random effect, e.g. lumikey random space
  lumikey fav [effect]                list favourites, or star / unstar an effect
  lumikey saturation <0-200>          colour intensity in % (100 = as designed)
  lumikey hue <0-359>                 rotate every colour around the colour wheel
  lumikey flip none|h|v|hv            mirror effects left-right (h), upside down (v) or both
  lumikey fade <seconds>              crossfade length when switching effects (0 = cut)
  lumikey idle <minutes>              fade the lights out after that long without typing (0 = never)
  lumikey cycle <minutes> [favorites|all|category]switch effect on a timer (0 = off)
  lumikey sensitivity <0.3-3>         how strongly music effects react
  lumikey looks                       list saved looks (and the starter ones)
  lumikey save <name>                 save the current setup as a look
  lumikey load <name>                 switch to a look
  lumikey forget <name>               delete a saved look
  lumikey key <keys> <color>          pin keys to a colour over any effect, e.g. lumikey key wasd pink
  lumikey key <keys> clear            let those keys follow the effect again
  lumikey keys [clear]                list the pinned keys, or unpin them all

<keys> is key names and groups joined by commas: w,a,s,d  esc,enter  arrows,space.

Effects are drawn by the lumikey user service (lumikey daemon). Music effects listen to
whatever is playing (the default output's monitor, via parec). Colours live only in
the keyboard's RAM; nothing is written to its firmware.
"""
import colorsys
import glob
import importlib.resources
import shutil
import json
import math
import os
import random
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from collections import namedtuple

from . import __author__, __version__, lamparray

APP_ID = "io.github.abhinavverma0907.Lumikey"
HOMEPAGE = "https://github.com/abhinavverma0907/lumikey"

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CACHE_HOME = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
CONFIG = os.path.join(CONFIG_HOME, "lumikey", "config.json")
UDEV_RULE = "/etc/udev/rules.d/70-lumikey.rules"
SERVICE = "lumikey"
LAYOUT_CACHE = os.path.join(CACHE_HOME, "lumikey", "lamps.json")  # for lumikey-gui, even when unplugged
FRAME_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "lumikey.frame")  # live frame for lumikey-gui
DEFAULTS = {"spec": "auto", "color": None, "brightness": 100, "speed": 1.0, "keys": {},  # keys: {usage: colour}
            "all_keyboards": True,  # other keyboards' key presses also drive typing effects
            "device": "",  # vid:pid to light; empty = the first keyboard found
            "saturation": 100, "hue": 0, "flip": "", "fade": 0.6, "idle": 0, "cycle": 0, "cycle_pool": "favorites",
            "sensitivity": 1.0, "favorites": [], "looks": {},
            "previews": True, "columns": 0, "sidebar": True}  # lumikey-gui only
FRAME = 1 / 30  # fastest frame rate we draw at (slower if the device asks)
GAMMA = 2.2
PITCH = 19.05  # key pitch in mm
EV_KEY, EV_MSC, MSC_SCAN = 1, 4, 4
KEY_A = 30
# Linux keycode -> HID keyboard usage, for keyboards whose MSC_SCAN isn't a HID usage (e.g. a laptop's i8042)
LINUX_TO_HID = {
    **{code: 4 + "abcdefghijklmnopqrstuvwxyz".index(c)
       for row, start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)) for code, c in enumerate(row, start)},
    **{2 + n: 30 + n for n in range(10)},  # 1..0
    **{59 + n: 58 + n for n in range(10)}, 87: 68, 88: 69,  # F1..F12
    1: 41, 12: 45, 13: 46, 14: 42, 15: 43, 26: 47, 27: 48, 28: 40, 39: 51, 40: 52, 41: 53, 43: 49,
    51: 54, 52: 55, 53: 56, 57: 44, 58: 57, 99: 70, 70: 71, 119: 72, 127: 101,
    110: 73, 102: 74, 104: 75, 111: 76, 107: 77, 109: 78, 106: 79, 105: 80, 108: 81, 103: 82,
    29: 224, 42: 225, 56: 226, 125: 227, 97: 228, 54: 229, 100: 230, 126: 231,
}
TAU = 2 * math.pi

COLORS = {
    "red": (1, 0, 0), "orange": (1, 0.45, 0), "amber": (1, 0.68, 0), "yellow": (1, 0.9, 0),
    "lime": (0.55, 1, 0), "green": (0, 1, 0.1), "teal": (0, 0.85, 0.6), "cyan": (0, 0.85, 1),
    "blue": (0, 0.2, 1), "violet": (0.55, 0.1, 1), "magenta": (1, 0, 0.85), "pink": (1, 0.15, 0.5),
    "white": (1, 1, 1), "warm": (1, 0.72, 0.45), "coral": (1, 0.3, 0.2), "gold": (1, 0.6, 0.05),
    "mint": (0.3, 1, 0.6), "sky": (0.2, 0.6, 1), "indigo": (0.25, 0.05, 1), "lavender": (0.7, 0.5, 1),
    "rose": (1, 0.05, 0.3),
}
HIDDEN_COLORS = {"off": (0, 0, 0), "dim": (0.025, 0.015, 0.07)}
STAR_COLORS = [(1, 1, 1), (0.6, 0.8, 1), (1, 0.85, 0.5), (1, 0.6, 0.85)]

# HID keyboard usage -> (name for the CLI, keycap legend for lumikey-gui)
KEYS = {
    **{4 + n: (c, c.upper()) for n, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{30 + n: (c, c) for n, c in enumerate("1234567890")},
    **{58 + n: (f"f{n + 1}", f"F{n + 1}") for n in range(12)},
    40: ("enter", "Enter"), 41: ("esc", "Esc"), 42: ("backspace", "⌫"), 43: ("tab", "Tab"), 44: ("space", ""),
    45: ("minus", "-"), 46: ("equal", "="), 47: ("lbracket", "["), 48: ("rbracket", "]"), 49: ("backslash", "\\"),
    51: ("semicolon", ";"), 52: ("quote", "'"), 53: ("grave", "`"), 54: ("comma", ","), 55: ("period", "."),
    56: ("slash", "/"), 57: ("capslock", "Caps"), 70: ("print", "PrtSc"), 71: ("scrolllock", "ScrLk"),
    72: ("pause", "Pause"), 73: ("insert", "Ins"), 74: ("home", "Home"), 75: ("pageup", "PgUp"),
    76: ("delete", "Del"), 77: ("end", "End"), 78: ("pagedown", "PgDn"),
    79: ("right", "→"), 80: ("left", "←"), 81: ("down", "↓"), 82: ("up", "↑"), 176: ("fn", "Fn"),
    224: ("lctrl", "Ctrl"), 225: ("lshift", "Shift"), 226: ("lalt", "Alt"), 227: ("super", "◆"),
    228: ("rctrl", "Ctrl"), 229: ("rshift", "Shift"), 230: ("ralt", "Alt"), 231: ("rsuper", "◆"),
}
KEY_GROUPS = {
    "wasd": [26, 4, 22, 7],
    "arrows": [82, 80, 81, 79],
    "numbers": list(range(30, 40)),
    "fkeys": list(range(58, 70)),
    "letters": list(range(4, 30)),
    "mods": [41, 43, 57, 225, 224, 227, 226, 230, 176, 228, 229, 40, 42],
    "nav": [70, 71, 76, 74, 75, 78, 77],
    "homerow": [4, 22, 7, 9, 13, 14, 15, 51],
    "esdf": [8, 22, 7, 9],
    "all": None,  # every lamp the keyboard has
}

Lamp = lamparray.Lamp


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- device

class Keyboard:
    """The lit device: any HID LampArray (Dynamic Lighting) keyboard, see lamparray.py."""

    def __init__(self, prefer=""):
        self.dev = lamparray.open_device(prefer)
        self.path, self.name, self.lamps = self.dev.path, self.dev.name, self.dev.lamps
        self.sysfs = self.dev.candidate.sysfs
        self.frame = max(FRAME, self.dev.min_interval)
        self.host_since = 0.0

    def send(self, leds):
        now = time.monotonic()
        if now - self.host_since > 5:  # re-assert host control in case the device reset
            self.dev.host = False
            self.host_since = now
        self.dev.send(leds)

    def autonomous(self):
        self.dev.autonomous(True)
        self.host_since = 0.0

    def close(self):
        self.dev.close()


class KeyInput:
    """Key presses reported as lamp indices: the lit keyboard's own, plus (others=True) any other
    keyboard's, e.g. a laptop's built-in one, lit on the matching key."""

    def __init__(self, lamps, others=True, sysfs=None):
        self.lamp_of = {l.usage: i for i, l in enumerate(lamps) if l.usage}
        home = lamparray.physical(sysfs) if sysfs else None
        self.fds, self.own_fds, self.denied, self.others_denied = [], set(), False, False
        for node in sorted(glob.glob("/sys/class/input/event*")):
            try:
                with open(f"{node}/device/capabilities/key") as f:
                    words = [int(w, 16) for w in f.read().split()]
            except OSError:
                continue
            own = home is not None and lamparray.physical(f"{node}/device") == home
            bits = 8 * struct.calcsize("l")
            is_keyboard = len(words) > KEY_A // bits and words[-1 - KEY_A // bits] >> KEY_A % bits & 1
            if not (own and any(words) or others and not own and is_keyboard):
                continue
            try:
                fd = os.open(f"/dev/input/{os.path.basename(node)}", os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                if own:
                    self.denied = True
                else:
                    self.others_denied = True
                continue
            self.fds.append(fd)
            if own:
                self.own_fds.add(fd)
        # other keyboards we can't read (e.g. the mouse's macro interface) don't matter once one other keyboard opens
        self.others_denied = self.others_denied and len(self.fds) == len(self.own_fds)

    def wait(self, timeout):
        if not self.fds:
            time.sleep(timeout)
            return []
        pressed = []
        for fd in select.select(self.fds, [], [], timeout)[0]:
            try:
                data = os.read(fd, 24 * 64)
            except BlockingIOError:
                continue
            own, scan = fd in self.own_fds, 0
            for _, _, etype, code, value in struct.iter_unpack("llHHi", data):
                if etype == EV_MSC and code == MSC_SCAN:
                    scan = value
                elif etype == EV_KEY and value == 1:
                    if own:
                        usage = scan & 0xFFFF if scan >> 16 == 0x07 else None  # keyboard usage page
                    else:
                        usage = LINUX_TO_HID.get(code)
                    lamp = self.lamp_of.get(usage)
                    if lamp is not None:
                        pressed.append(lamp)
        return pressed

    def close(self):
        for fd in self.fds:
            os.close(fd)


class Audio(threading.Thread):
    """Whatever is playing (default output's monitor via parec), as bands, levels and beats.

    Everything is auto-gained to 0..1, so it works at any volume, and reads 0 in silence.
    """
    RATE, HOP, WIN, NBANDS = 44100, 512, 2048, 16

    def __init__(self):
        super().__init__(daemon=True)
        self.bands = [0.0] * self.NBANDS  # log-spaced, ~35 Hz .. 16 kHz
        self.level = self.left = self.right = self.bass = self.mid = self.treble = 0.0
        self.beat, self.beats, self.strength, self.centroid = 0.0, 0, 0.0, 0.5
        self.running, self.proc, self.sensitivity = True, None, 1.0
        try:
            import numpy as np
        except ImportError:
            log("music effects need python-numpy")
            return
        self.np = np
        freqs = np.fft.rfftfreq(self.WIN, 1 / self.RATE)
        edges = np.geomspace(35, 16000, self.NBANDS + 1)
        self.bins = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
        self.window = np.hanning(self.WIN).astype(np.float32)
        self.peaks = np.full(self.NBANDS + 3, 1e-3)  # slowly decaying maxima -> auto gain
        self.vu_peak, self.bass_avg = 1e-3, 0.0
        self.start()

    def run(self):
        np = self.np
        while self.running:
            try:
                self.proc = subprocess.Popen(
                    ["parec", "--device=@DEFAULT_MONITOR@", "--format=float32le", "--channels=2",
                     f"--rate={self.RATE}", "--latency-msec=15", "--client-name=lumikey"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                buf = np.zeros((self.WIN, 2), np.float32)
                while self.running:
                    raw = self.proc.stdout.read(self.HOP * 8)
                    if len(raw) < self.HOP * 8:
                        break
                    buf = np.roll(buf, -self.HOP, axis=0)
                    buf[-self.HOP:] = np.frombuffer(raw, np.float32).reshape(-1, 2)
                    self.analyse(buf)
            except OSError:
                pass
            if self.proc:
                self.proc.kill()  # no-op if it already exited
                self.proc.stdout.close()
                self.proc.wait()  # reap it
            if self.running:
                time.sleep(1)  # parec died (e.g. sink switched): restart

    def analyse(self, buf):
        np = self.np
        mag = np.abs(np.fft.rfft(buf.mean(axis=1) * self.window))
        raw = np.array([mag[b].mean() for b in self.bins])
        raw = np.concatenate([raw, [raw[:3].mean(), raw[3:9].mean(), raw[9:].mean()]])
        self.peaks = np.maximum(self.peaks * 0.9995, raw)  # ~10 s gain release
        norm = np.clip(raw / np.maximum(self.peaks, 0.5) * self.sensitivity, 0, 1)  # the floor keeps silence at ~0
        if raw[-3:].sum() < 0.05:
            norm[:] = 0

        def ease(cur, tgt, up=0.6, down=0.12):  # fast attack, slow release
            return cur + (tgt - cur) * (up if tgt > cur else down)

        self.bands = [ease(c, t) for c, t in zip(self.bands, norm[:self.NBANDS])]
        self.bass, self.mid, self.treble = (ease(c, t) for c, t in
                                            zip((self.bass, self.mid, self.treble), norm[self.NBANDS:]))
        self.level = (self.bass + self.mid + self.treble) / 3
        total = sum(self.bands)
        if total > 0.05:
            self.centroid = ease(self.centroid, sum(i * b for i, b in enumerate(self.bands))
                                 / total / (self.NBANDS - 1), 0.05, 0.05)

        rms = np.sqrt((buf[-self.HOP:] ** 2).mean(axis=0))
        self.vu_peak = max(self.vu_peak * 0.9995, float(rms.max()))
        vu = [0.0 if norm[-3:].sum() == 0 else
              float(np.clip(1 + 20 * np.log10(r / self.vu_peak + 1e-9) / 36, 0, 1)) for r in rms]
        self.left, self.right = ease(self.left, vu[0], 0.5, 0.08), ease(self.right, vu[1], 0.5, 0.08)

        b = norm[self.NBANDS]
        if b > 0.55 and b > self.bass_avg * 1.35 and self.beat < 0.4:
            self.beat, self.beats = 1.0, self.beats + 1
            self.strength = float(clamp((b - self.bass_avg) * 2, 0.3, 1))
        self.bass_avg += (b - self.bass_avg) * 0.05
        self.beat *= 0.93

    def close(self):
        self.running = False
        if self.proc:
            self.proc.terminate()


# ---------------------------------------------------------------- colour helpers

def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def smoothstep(a, b, x):
    t = clamp((x - a) / (b - a))
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def hsv(h, s=1.0, v=1.0):
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


def ramp(stops, x):
    x = clamp(x)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if x <= p1:
            return mix(c0, c1, (x - p0) / (p1 - p0))
    return stops[-1][1]


def to_led(c, brightness):
    return tuple(int(255 * clamp(v * brightness) ** GAMMA + 0.5) for v in c)


_PERM = list(range(256))
random.Random(7).shuffle(_PERM)
_PERM += _PERM


def noise(x, y):
    """Smooth 2D value noise in 0..1."""
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = x - xi, y - yi
    xi, yi = xi & 255, yi & 255
    u, v = xf * xf * (3 - 2 * xf), yf * yf * (3 - 2 * yf)
    a, b = _PERM[_PERM[xi] + yi], _PERM[_PERM[xi + 1] + yi]
    c, d = _PERM[_PERM[xi] + yi + 1], _PERM[_PERM[xi + 1] + yi + 1]
    top, bottom = a + (b - a) * u, c + (d - c) * u
    return (top + (bottom - top) * v) / 255


def fbm(x, y):
    return (2 * noise(x, y) + noise(x * 2.03 + 17.1, y * 2.03 + 31.7)) / 3


def neighbours(lamps, radius):
    return [[j for j, m in enumerate(lamps) if j != i and math.hypot(l.x - m.x, l.y - m.y) < radius]
            for i, l in enumerate(lamps)]


def splat(lamps, frame, x, y, color, radius, k=1.0):
    """Add a soft round glow centred at (x, y) mm."""
    reach, r2 = radius * 2.5, radius * radius
    for i, l in enumerate(lamps):
        dx, dy = l.x - x, l.y - y
        if -reach < dx < reach and -reach < dy < reach:
            w = k * math.exp(-(dx * dx + dy * dy) / r2)
            if w > 0.004:
                frame[i] = add(frame[i], scale(color, w))


def make_stars(n, density):
    return [(random.uniform(2.5, 8), random.random(), random.choice(STAR_COLORS))
            if random.random() < density else None for _ in range(n)]


def star_light(star, t):
    if not star:
        return (0, 0, 0)
    period, phase, col = star
    return scale(col, max(0.0, math.sin(TAU * (t / period + phase))) ** 12)


def grid_cells(lamps):
    """The board as a grid of key-sized cells: (columns, rows, {cell: nearest lamp}, [cell per lamp])."""
    cols = max(round(l.x / PITCH) for l in lamps) + 1
    rows = max(round(l.y / PITCH) for l in lamps) + 1
    lamp_at = {}
    for c in range(cols):
        for r in range(rows):
            d, i = min((math.hypot(l.x - c * PITCH, l.y - r * PITCH), i) for i, l in enumerate(lamps))
            if d < PITCH * 0.75:  # wide keys leave some cells without a lamp
                lamp_at[(c, r)] = i
    return cols, rows, lamp_at, [(round(l.x / PITCH), round(l.y / PITCH)) for l in lamps]


def grade(colors, saturation=1.0, hue=0.0):
    """A frame with its hue turned (in turns) and its saturation scaled (1 = as drawn)."""
    if saturation == 1 and not hue:
        return colors
    out = []
    for c in colors:
        h, s, v = colorsys.rgb_to_hsv(max(c[0], 0.0), max(c[1], 0.0), max(c[2], 0.0))
        out.append(colorsys.hsv_to_rgb((h + hue) % 1, min(1.0, s * saturation), v))
    return out


def flipped(lamps, flip):
    """The layout mirrored left-right ('h'), upside down ('v') or both, lamp for lamp."""
    w, h = max(l.x for l in lamps), max(l.y for l in lamps)
    return [Lamp(w - l.x if "h" in flip else l.x, h - l.y if "v" in flip else l.y,
                 1 - l.u if "h" in flip else l.u, 1 - l.v if "v" in flip else l.v, l.usage) for l in lamps]


# ---------------------------------------------------------------- effects

class Effect:
    category = "classic"
    animated = True
    takes_color = False
    needs_keys = False
    needs_audio = False
    audio = None  # set by Scene for needs_audio effects

    def __init__(self, lamps, color=None):
        self.lamps = lamps
        self.color = color
        # at least a key wide and tall, so one-row strips and single lamps work too
        self.width = max(PITCH, max(l.x for l in lamps))
        self.height = max(PITCH, max(l.y for l in lamps))
        self.cx, self.cy = self.width / 2, self.height / 2
        self.last = None

    def step(self, t):
        """Seconds since the previous call (0 on the first), capped so pauses don't jump."""
        dt = 0.0 if self.last is None else clamp(t - self.last, 0.0, 0.1)
        self.last = t
        return dt

    def on_key(self, i, t):
        pass

    def render(self, t):
        raise NotImplementedError


class Solid(Effect):
    category = "classic"
    animated = False  # its colour comes from the spec itself (lumikey cyan), not a colour argument

    def render(self, t):
        return [self.color] * len(self.lamps)


class Rainbow(Effect):
    category = "classic"
    animated = False

    def render(self, t):
        return [hsv(l.u * 0.85) for l in self.lamps]


class Wave(Effect):
    category = "classic"
    def render(self, t):
        return [hsv(l.u * 0.9 - t * 0.2) for l in self.lamps]


class Spectrum(Effect):
    category = "classic"
    def render(self, t):
        return [hsv(t * 0.04 + l.u * 0.06) for l in self.lamps]


class Breathe(Effect):
    category = "classic"
    takes_color = True

    def render(self, t):
        level = 0.5 - 0.5 * math.cos(TAU * t / 4.5)
        return [scale(self.color or COLORS["violet"], 0.04 + 0.96 * level)] * len(self.lamps)


class Aurora(Effect):
    category = "nature"
    SKY = (0.0, 0.015, 0.06)
    GLOW = [(0, (0.0, 1.0, 0.45)), (0.45, (0.0, 0.85, 0.75)), (0.75, (0.45, 0.2, 1.0)), (1, (0.9, 0.15, 0.75))]

    def render(self, t):
        out = []
        for l in self.lamps:
            curtain = smoothstep(0.38, 0.62, fbm(l.u * 3.0 + t * 0.1, t * 0.13 + l.v * 0.5))
            shimmer = 0.65 + 0.35 * noise(l.u * 10 - t * 0.8, l.v * 1.5 + t * 0.3)
            tint = 1 - l.v + 0.6 * (noise(l.u * 2 + 40, t * 0.07) - 0.5)  # green low, violet/pink high
            out.append(add(self.SKY, scale(ramp(self.GLOW, tint), curtain * shimmer)))
        return out


class Plasma(Effect):
    category = "neon"
    NEON = [(0, (1, 0.05, 0.55)), (0.25, (0.55, 0.05, 1)), (0.5, (0.05, 0.15, 0.9)),
            (0.75, (0, 0.85, 1)), (1, (1, 0.05, 0.55))]

    @staticmethod
    def field(l, p):
        cx, cy = 1.4 + 0.9 * math.sin(p * 0.37), 0.5 + 0.6 * math.cos(p * 0.29)
        x, y = l.x / 100, l.y / 100
        return (math.sin(x * 2.2 + p * 0.9) + math.sin(y * 3.1 - p * 0.7 + x)
                + math.sin(math.hypot(x - cx, y - cy) * 4.5 - p * 1.2))

    def render(self, t):
        return [ramp(self.NEON, (self.field(l, t) / 6 + 0.5 + t * 0.03) % 1) for l in self.lamps]


class Lava(Effect):
    category = "elements"
    BG = (0.09, 0.0, 0.13)
    HOT = [(0, (0.6, 0.0, 0.05)), (0.35, (1, 0.12, 0)), (0.6, (1, 0.42, 0)), (1, (1, 0.85, 0.3))]
    BLOBS = [(0.31, 0.53, 0.0, 1.7, 38), (0.23, 0.41, 2.1, 0.4, 32), (0.17, 0.67, 4.0, 3.1, 44),
             (0.27, 0.37, 5.2, 2.2, 28), (0.13, 0.59, 1.1, 4.4, 34)]

    def render(self, t):
        w, h = self.width, self.height
        blobs = [(w / 2 + w * 0.45 * math.sin(t * a + pa), h / 2 + h * 0.6 * math.sin(t * b + pb), r * r)
                 for a, b, pa, pb, r in self.BLOBS]
        out = []
        for l in self.lamps:
            f = sum(r2 / ((l.x - x) ** 2 + (l.y - y) ** 2 + 1) for x, y, r2 in blobs)
            out.append(mix(self.BG, ramp(self.HOT, f / 3), smoothstep(0.6, 1.2, f)))
        return out


class Fire(Effect):
    category = "elements"
    FLAME = [(0, (0, 0, 0)), (0.25, (0.3, 0, 0)), (0.5, (0.9, 0.1, 0)), (0.72, (1, 0.4, 0)),
             (0.9, (1, 0.7, 0.1)), (1, (1, 0.92, 0.55))]

    def render(self, t):
        return [ramp(self.FLAME, l.v * 1.15 - 0.25 + (fbm(l.u * 7, l.v * 2.5 + t * 1.8) - 0.5) * 1.2)
                for l in self.lamps]


class Ocean(Effect):
    category = "nature"
    WATER = [(0, (0, 0.03, 0.2)), (0.55, (0, 0.25, 0.7)), (0.82, (0, 0.65, 0.95)), (1, (0.7, 0.97, 1))]

    def render(self, t):
        out = []
        for l in self.lamps:
            swell = 0.5 + 0.5 * math.sin(l.u * 5.5 - t * 0.9 + 1.3 * math.sin(l.v * 2.5 + t * 0.35))
            out.append(ramp(self.WATER, 0.6 * swell + 0.4 * noise(l.u * 4 - t * 0.25, l.v * 2 + t * 0.15)))
        return out


class Sunset(Effect):
    category = "mood"
    DUSK = [(0, (0.1, 0.02, 0.35)), (0.35, (0.75, 0.05, 0.55)), (0.65, (1, 0.35, 0.08)), (1, (1, 0.72, 0.2))]

    def render(self, t):
        return [ramp(self.DUSK, l.v + 0.12 * math.sin(l.u * 3.5 + t * 0.5)
                     + 0.06 * math.sin(l.u * 7 - t * 0.8) + 0.05 * math.sin(t * 0.15))
                for l in self.lamps]


class Matrix(Effect):
    category = "neon"
    BG, TRAIL, HEAD = (0, 0.035, 0.01), (0, 0.9, 0.25), (0.75, 1, 0.8)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.columns = {}
        for i, l in enumerate(lamps):
            self.columns.setdefault(round(l.x / PITCH), []).append(i)
        self.drops = {c: self.spawn(random.uniform(0, self.height)) for c in self.columns}

    def spawn(self, head=None):
        return [-random.uniform(5, 60) if head is None else head,
                random.uniform(45, 110), random.uniform(35, 75)]

    def render(self, t):
        dt = self.step(t)
        out = [self.BG] * len(self.lamps)
        for c, idx in self.columns.items():
            drop = self.drops[c]
            drop[0] += drop[1] * dt
            if drop[0] - drop[2] > self.height:
                drop = self.drops[c] = self.spawn()
            head, _, trail = drop
            for i in idx:
                d = head - self.lamps[i].y
                if -12 < d < trail:
                    trail_col = scale(self.TRAIL, (1 - max(d, 0) / trail) ** 1.6)
                    out[i] = add(self.BG, mix(trail_col, self.HEAD, max(0, 1 - abs(d) / 12)))
        return out


class Starfield(Effect):
    category = "space"
    NIGHT, NEBULA = (0.012, 0, 0.045), (0.5, 0.1, 0.9)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 1.0)
        self.rows = sorted({round(l.y) for l in lamps})
        self.shot, self.next_shot = None, random.uniform(3, 8)

    def render(self, t):
        if self.shot is None and t >= self.next_shot:
            self.shot = (t, random.choice(self.rows), random.choice((1, -1)))
        out = []
        for star, l in zip(self.stars, self.lamps):
            neb = noise(l.u * 2.5 + t * 0.03, l.v * 2)
            c = add(add(self.NIGHT, scale(self.NEBULA, 0.12 * neb * neb)), star_light(star, t))
            if self.shot:
                t0, row, direction = self.shot
                d = (t - t0) * 260 - 30 - (l.x if direction > 0 else self.width - l.x)
                if abs(l.y - row) < 6 and 0 <= d < 80:
                    c = add(c, scale((1, 1, 1), (1 - d / 80) ** 2))
            out.append(c)
        if self.shot and (t - self.shot[0]) * 260 - 110 > self.width:
            self.shot, self.next_shot = None, t + random.uniform(6, 14)
        return out


class Galaxy(Effect):
    category = "space"
    CORE, ARM_IN, ARM_OUT = (1, 0.8, 0.45), (0.3, 0.35, 1), (1, 0.25, 0.75)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.3)

    def render(self, t):
        out = []
        for l, star in zip(self.lamps, self.stars):
            dx, dy = (l.x - self.cx) / 100, (l.y - self.cy) * 2.2 / 100  # disc seen at a tilt
            r = math.hypot(dx, dy)
            arm = (0.5 + 0.5 * math.cos(2 * (math.atan2(dy, dx) - math.log(r + 0.08) * 2.2 + t * 0.35))) ** 1.5
            dust = noise(dx * 4 + t * 0.1, dy * 4)
            col = mix(self.ARM_IN, self.ARM_OUT, clamp(r * 0.9 + 0.4 * (dust - 0.5)))
            c = add(scale(self.CORE, 1.3 * math.exp(-(r / 0.38) ** 2)),
                    scale(col, 1.3 * arm * math.exp(-r * 0.8) * (0.6 + 0.6 * dust)))
            out.append(add(add(c, (0.01, 0, 0.035)), star_light(star, t)))
        return out


class Nebula(Effect):
    category = "space"
    DUST = [(0, (0.01, 0.0, 0.05)), (0.35, (0.18, 0.02, 0.45)), (0.55, (0.75, 0.08, 0.6)),
            (0.72, (0.15, 0.45, 1.0)), (0.88, (0.6, 0.85, 1.0)), (1, (1, 1, 1))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.3)

    def render(self, t):
        out = []
        for l, star in zip(self.lamps, self.stars):
            x, y = l.x / 60, l.y / 60
            q = fbm(x + t * 0.04, y - t * 0.03)
            r = fbm(x + 3.5 * q + t * 0.06 + 5.2, y + 3.5 * q - t * 0.05 + 1.3)  # domain warp
            out.append(add(ramp(self.DUST, smoothstep(0.25, 0.8, r)), star_light(star, t)))
        return out


class Opal(Effect):
    category = "mood"
    FIRE = [(0, (1, 0.45, 0.75)), (0.2, (0.65, 0.45, 1)), (0.4, (0.35, 0.7, 1)), (0.6, (0.35, 1, 0.75)),
            (0.8, (1, 0.85, 0.45)), (1, (1, 0.45, 0.75))]

    def render(self, t):
        out = []
        for l in self.lamps:
            x, y = l.x / 150, l.y / 150
            h = fbm(x + fbm(x * 0.7 + t * 0.04, y + 3) + t * 0.03, y + t * 0.03)
            fire = ramp(self.FIRE, (h * 2.2 + t * 0.03) % 1)  # play-of-colour drifting through the stone
            sheen = smoothstep(0.6, 0.85, noise(x * 5 - t * 0.25, y * 5 + t * 0.1))
            out.append(mix(fire, (1, 1, 1), 0.08 + 0.45 * sheen))  # travelling pearly glints
        return out


class Vortex(Effect):
    category = "neon"
    def render(self, t):
        out = []
        for l in self.lamps:
            dx, dy = (l.x - self.cx) / 100, (l.y - self.cy) * 1.8 / 100
            r, th = math.hypot(dx, dy), math.atan2(dy, dx)
            bands = 0.5 + 0.5 * math.cos(th * 2 + r * 5 - t * 2.5)
            out.append(hsv(th / TAU + r * 0.4 - t * 0.15, 0.9, 0.12 + 0.88 * bands ** 1.5))
        return out


class Synthwave(Effect):
    category = "neon"
    SKY = [(0, (0.06, 0.0, 0.2)), (1, (0.45, 0.0, 0.4))]
    SUN = [(0, (1, 0.85, 0.2)), (0.5, (1, 0.4, 0.2)), (1, (1, 0.05, 0.5))]
    SUN_R = 58

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.horizon = self.height * 0.5
        self.stars = make_stars(len(lamps), 0.25)

    def render(self, t):
        hz, out = self.horizon, []
        for l, star in zip(self.lamps, self.stars):
            if l.y < hz:
                c = add(ramp(self.SKY, l.y / hz), scale(star_light(star, t), 0.5))
                d = math.hypot(l.x - self.cx, l.y - hz)
                sv = 1 - (hz - l.y) / self.SUN_R  # 0 at the top of the sun, 1 at the horizon
                stripe = sv > 0.45 and math.sin(sv * 14 - t * 2) > 0.3
                if d < self.SUN_R + 10 and not stripe:
                    c = mix(c, ramp(self.SUN, sv), smoothstep(self.SUN_R + 10, self.SUN_R - 6, d))
            else:
                fy = (l.y - hz) / (self.height - hz) + 0.25
                h_line = (0.5 + 0.5 * math.cos(TAU * (1 / fy + t * 0.9))) ** 6
                v_line = (0.5 + 0.5 * math.cos(TAU * (l.x - self.cx) / (90 * fy))) ** 8
                c = add((0.05, 0.0, 0.1), add(scale((1, 0.1, 0.75), h_line), scale((0.1, 0.8, 1), 0.8 * v_line)))
            out.append(c)
        return out


class Comets(Effect):
    category = "space"
    HEADS = [(0.1, 0.9, 1), (1, 0.15, 0.7), (1, 0.75, 0.2)]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.3)

    def render(self, t):
        out = [add((0.008, 0, 0.03), star_light(s, t)) for s in self.stars]
        for k, col in enumerate(self.HEADS):
            for s in range(14):  # head, then tail samples further back along the orbit
                a = (t - s * 0.07) * (0.7 + 0.2 * k) + k * 2.1
                x = self.cx + self.width * 0.45 * math.cos(a)
                y = self.cy + self.height * 0.48 * math.sin(a * (1.6 + 0.3 * k))
                splat(self.lamps, out, x, y, mix(col, (1, 1, 1), 0.5) if s == 0 else col, 12, (1 - s / 14) ** 1.8)
        return out


class Fireflies(Effect):
    category = "nature"
    FOREST = (0.0, 0.025, 0.02)
    GLOWS = [(0.8, 1, 0.25), (1, 0.8, 0.3), (0.6, 1, 0.5)]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.flies = [(random.uniform(0, 100), random.uniform(0, 100), random.uniform(2.5, 6),
                       random.random(), random.choice(self.GLOWS)) for _ in range(10)]

    def render(self, t):
        out = [add(self.FOREST, scale((0, 0.06, 0.04), noise(l.u * 3 + t * 0.05, l.v * 2))) for l in self.lamps]
        for sx, sy, period, phase, col in self.flies:
            x = self.width * smoothstep(0.2, 0.8, noise(sx, t * 0.12))
            y = self.height * smoothstep(0.2, 0.8, noise(sy, t * 0.12))
            blink = smoothstep(0.2, 1.0, math.sin(TAU * (t / period + phase)))
            if blink > 0:
                splat(self.lamps, out, x, y, col, 13, blink)
        return out


class Sakura(Effect):
    category = "nature"
    PETALS = [(1, 0.55, 0.7), (1, 0.75, 0.85), (1, 0.4, 0.6), (1, 0.85, 0.9)]
    SKY = [(0, (0.06, 0.02, 0.18)), (1, (0.22, 0.04, 0.16))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.petals = [self.spawn(True) for _ in range(9)]

    def spawn(self, anywhere=False):
        return [random.uniform(-40, self.width) if anywhere else random.uniform(-60, self.width * 0.7),
                random.uniform(0, self.height) if anywhere else -random.uniform(10, 50),
                random.uniform(12, 25), random.uniform(20, 45),  # fall, drift (mm/s)
                random.uniform(0, TAU), random.choice(self.PETALS)]

    def render(self, t):
        dt = self.step(t)
        out = [ramp(self.SKY, l.v) for l in self.lamps]
        for n, p in enumerate(self.petals):
            p[0] += p[3] * dt
            p[1] += p[2] * dt
            if p[1] > self.height + 15 or p[0] > self.width + 15:
                p = self.petals[n] = self.spawn()
            flutter = 0.85 + 0.35 * math.sin(t * 4 + p[4])  # a turning petal catches the light
            splat(self.lamps, out, p[0] + 10 * math.sin(t * 1.5 + p[4]), p[1], p[5], 14, flutter)
        return out


class Storm(Effect):
    category = "nature"
    CLOUD, RAIN, BOLT = (0.03, 0.04, 0.08), (0.45, 0.65, 1), (0.75, 0.8, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.drops = [self.spawn(True) for _ in range(14)]
        self.flash, self.next_flash = None, random.uniform(3, 8)

    def spawn(self, anywhere=False):
        return [random.uniform(-20, self.width),
                random.uniform(0, self.height) if anywhere else -random.uniform(0, 40),
                random.uniform(140, 220)]

    def render(self, t):
        dt = self.step(t)
        out = [add((0, 0.02, 0.07), scale(self.CLOUD, noise(l.u * 3 + t * 0.15, l.v * 2))) for l in self.lamps]
        for n, d in enumerate(self.drops):
            d[0] += 25 * dt  # wind
            d[1] += d[2] * dt
            if d[1] > self.height + 20:
                d = self.drops[n] = self.spawn()
            splat(self.lamps, out, d[0], d[1], self.RAIN, 10, 0.9)
            splat(self.lamps, out, d[0] - 4, d[1] - 16, self.RAIN, 9, 0.3)
        if self.flash is None and t >= self.next_flash:
            self.flash, self.next_flash = (t, random.uniform(0, self.width)), t + random.uniform(5, 13)
        if self.flash:
            age, bolt_x = t - self.flash[0], self.flash[1]
            env = sum(max(0.0, 1 - (age - p) / 0.09) for p in (0, 0.12, 0.3) if age >= p)  # flicker
            for i, l in enumerate(self.lamps):
                out[i] = add(out[i], scale(self.BOLT, env * (0.35 + 0.65 * math.exp(-((l.x - bolt_x) / 60) ** 2))))
            if age > 0.6:
                self.flash = None
        return out


class Embers(Effect):
    category = "elements"
    COALS = [(0, (0, 0, 0)), (0.3, (0.25, 0, 0)), (0.7, (0.7, 0.08, 0)), (1, (1, 0.3, 0))]
    SPARK = [(0, (0.5, 0.02, 0)), (0.5, (1, 0.35, 0)), (1, (1, 0.9, 0.5))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.sparks, self.due = [], 0.0

    def render(self, t):
        dt = self.step(t)
        out = [ramp(self.COALS, l.v ** 2 * (0.55 + 0.45 * fbm(l.u * 6 + t * 0.2, t * 0.3))) for l in self.lamps]
        self.due += dt * 6  # sparks per second
        while self.due >= 1:
            self.due -= 1
            self.sparks.append([random.uniform(0, self.width), self.height + 5, random.uniform(-10, 10),
                                random.uniform(25, 60), t, random.uniform(1.5, 3)])
        alive = []
        for s in self.sparks:
            age = (t - s[4]) / s[5]
            if age >= 1:
                continue
            s[0] += (s[2] + 15 * math.sin(t * 3 + s[4])) * dt
            s[1] -= s[3] * dt
            splat(self.lamps, out, s[0], s[1], ramp(self.SPARK, 1 - age), 10, (1 - age) ** 0.7)
            alive.append(s)
        self.sparks = alive
        return out


class Heartbeat(Effect):
    category = "mood"
    PERIOD = 0.9  # ~67 bpm
    HEART = [(0, (0.1, 0, 0.025)), (0.45, (0.7, 0, 0.12)), (0.8, (1, 0.1, 0.35)), (1, (1, 0.55, 0.7))]

    def render(self, t):
        phase, out = t % self.PERIOD, []
        for l in self.lamps:
            d = math.hypot(l.x - self.cx, (l.y - self.cy) * 1.6)
            k = 0.0
            for start, strength in ((0.0, 1.0), (0.24, 0.75)):  # lub, dub
                age = phase - start
                if age >= 0:
                    k += strength * math.exp(-((d - age * 240) / 40) ** 2) * math.exp(-age * 2.5)
            out.append(ramp(self.HEART, k))
        return out


class Heatmap(Effect):
    category = "typing"
    needs_keys = True
    HEAT = [(0, (0, 0.02, 0.12)), (0.3, (0, 0.5, 0.9)), (0.55, (0.3, 1, 0.2)), (0.78, (1, 0.55, 0)),
            (1, (1, 0.1, 0.05))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.heat = [0.0] * len(lamps)
        self.near = neighbours(lamps, 24)

    def on_key(self, i, t):
        self.heat[i] = min(1.0, self.heat[i] + 0.3)
        for j in self.near[i]:
            self.heat[j] = min(1.0, self.heat[j] + 0.08)

    def render(self, t):
        k = math.exp(-self.step(t) / 8)
        self.heat = [h * k for h in self.heat]
        return [ramp(self.HEAT, h) for h in self.heat]


class Constellation(Effect):
    category = "typing"
    needs_keys = True
    LIFE, DRAW = 7.0, 0.3  # s a star lasts; s a link takes to draw itself
    LINK = (0.45, 0.55, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.2)
        self.points, self.links = [], []  # (lamp, time, colour); (from lamp, to lamp, time)

    def on_key(self, i, t):
        if self.points and t - self.points[-1][1] < 3 and self.points[-1][0] != i:
            self.links.append((self.points[-1][0], i, t))
        self.points.append((i, t, random.choice(STAR_COLORS)))
        del self.points[:-40], self.links[:-40]

    def render(self, t):
        out = [add((0.006, 0, 0.025), scale(star_light(s, t), 0.4)) for s in self.stars]
        self.points = [p for p in self.points if t - p[1] < self.LIFE]
        self.links = [k for k in self.links if t - k[2] < self.LIFE]
        for a, b, t0 in self.links:
            fade, drawn = (1 - (t - t0) / self.LIFE) ** 1.5, clamp((t - t0) / self.DRAW)
            la, lb = self.lamps[a], self.lamps[b]
            steps = max(2, int(math.hypot(lb.x - la.x, lb.y - la.y) / 8))
            for s in range(1, steps):
                p = s / steps
                if p <= drawn:
                    splat(self.lamps, out, la.x + (lb.x - la.x) * p, la.y + (lb.y - la.y) * p, self.LINK, 6, 0.4 * fade)
        for i, t0, col in self.points:
            age = t - t0
            k = (1 - age / self.LIFE) ** 1.5 * (1 + 1.5 * math.exp(-age * 6))  # flares, then glows
            out[i] = add(out[i], scale(col, k))
            l = self.lamps[i]
            splat(self.lamps, out, l.x, l.y, col, 12, 0.25 * k)
        return out


class Ink(Effect):
    category = "typing"
    needs_keys = True
    takes_color = True
    PAPER = (0.008, 0.006, 0.02)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.ink = [(0.0, 0.0, 0.0)] * len(lamps)
        self.near = neighbours(lamps, 24)
        self.hue = random.random()

    def on_key(self, i, t):
        self.hue = (self.hue + 0.618) % 1
        self.ink[i] = add(self.ink[i], scale(self.color or hsv(self.hue, 0.9), 1.3))

    def render(self, t):
        dt = self.step(t)
        bleed, dry = clamp(dt * 1.8), math.exp(-dt / 9)
        ink = self.ink
        new = []
        for i, c in enumerate(ink):
            near = self.near[i]
            if near:
                avg = scale(tuple(sum(ink[j][ch] for j in near) for ch in range(3)), 1 / len(near))
                c = mix(c, avg, bleed)
            new.append(scale(c, dry))
        self.ink = new
        # soft-clip pools of ink, and keep mixed inks vivid instead of going grey
        return [add(self.PAPER, tuple(1 - math.exp(-2 * v) for v in c)) for c in grade(new, 1.7)]


# ---------------------------------------------------------------- more space

class Blackhole(Effect):
    category = "space"
    DISK = [(0, (0, 0, 0)), (0.2, (0.3, 0.02, 0.0)), (0.5, (1, 0.3, 0.03)), (0.8, (1, 0.7, 0.3)), (1, (1, 0.95, 0.85))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.3)

    def render(self, t):
        out = []
        for l, star in zip(self.lamps, self.stars):
            dx, dy = (l.x - self.cx) / 100, (l.y - self.cy) * 1.7 / 100
            r, th = math.hypot(dx, dy), math.atan2(dy, dx)
            shadow = smoothstep(0.16, 0.26, r)  # nothing escapes the event horizon
            ring = math.exp(-((r - 0.29) / 0.06) ** 2)  # photon ring
            swirl = 0.5 + 0.5 * math.cos(3 * th - 5 * math.log(r + 0.05) + t * 1.8)
            disk = math.exp(-((r - 0.55) / 0.38) ** 2) * (0.3 + 0.7 * swirl)
            doppler = 0.5 + 0.5 * math.cos(th + 0.4)  # the side spinning towards us is brighter
            heat = clamp(disk * (0.35 + 0.75 * doppler) + ring * 0.95)
            c = add(add(ramp(self.DISK, heat), (0.008, 0, 0.03)), scale(star_light(star, t), clamp(1 - 2 * disk)))
            out.append(scale(c, shadow))
        return out


class Warp(Effect):
    category = "space"
    TINTS = STAR_COLORS + [(0.45, 0.55, 1), (0.6, 0.4, 1)]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.streaks = [self.spawn(random.uniform(3, 170)) for _ in range(24)]

    def spawn(self, dist=None):
        return [random.uniform(0, TAU), dist if dist is not None else random.uniform(2, 12),
                random.uniform(0.7, 1.4), random.choice(self.TINTS)]

    def render(self, t):
        dt = self.step(t)
        out = [add((0.01, 0, 0.04), scale((0.18, 0.06, 0.5), math.exp(-((l.x - self.cx) ** 2 / 2500
                                                                         + (l.y - self.cy) ** 2 / 500))))
               for l in self.lamps]
        for s in self.streaks:
            s[1] += (18 + s[1] * 1.7) * s[2] * dt  # stars rush past faster the closer they get
            if s[1] > 200:
                s[:] = self.spawn()
            ang, dist, _, col = s
            ca, sa, k = math.cos(ang), math.sin(ang) * 0.6, clamp(dist / 70)
            for back, w in ((0, 1.0), (0.14, 0.55), (0.3, 0.25)):
                d = dist * (1 - back)
                splat(self.lamps, out, self.cx + ca * d, self.cy + sa * d, col, 9, k * w)
        return out


class Supernova(Effect):
    category = "space"
    CYCLE, SWELL = 7.0, 1.0
    SHELL = [(0, (1, 1, 1)), (0.15, (0.6, 0.9, 1)), (0.4, (0.9, 0.3, 1)), (0.7, (1, 0.2, 0.4)), (1, (0.4, 0.02, 0.15))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.stars = make_stars(len(lamps), 0.3)

    def render(self, t):
        n, phase = divmod(t, self.CYCLE)
        rnd = random.Random(int(n))  # each explosion somewhere new
        x0, y0, hue = rnd.uniform(0.3, 0.7) * self.width, rnd.uniform(0.3, 0.7) * self.height, rnd.random()
        out = [add((0.008, 0, 0.03), star_light(s, t)) for s in self.stars]
        if phase < self.SWELL:  # the star swells and flickers
            k = (phase / self.SWELL) ** 2 * (0.8 + 0.2 * math.sin(phase * 40))
            splat(self.lamps, out, x0, y0, (1, 0.9, 0.7), 8 + 8 * k, 0.3 + 0.9 * k)
            return out
        age, life = phase - self.SWELL, self.CYCLE - self.SWELL
        radius, fade = max(1.0, age * 95), (1 - age / life) ** 1.3
        for i, l in enumerate(self.lamps):
            d = math.hypot(l.x - x0, (l.y - y0) * 1.3)
            shell = math.exp(-((d - radius) / (14 + age * 6)) ** 2)
            cloud = smoothstep(radius, radius * 0.3, d) * noise(l.x / 25 + n * 10, l.y / 25 + age * 0.3) * 0.5
            col = add(scale(ramp(self.SHELL, age / life + d / 600), shell), scale(hsv(hue + d / 400, 0.8), cloud))
            out[i] = add(out[i], scale(col, fade))
        splat(self.lamps, out, x0, y0, (1, 1, 1), 22, math.exp(-age * 5))
        return out


# ---------------------------------------------------------------- more nature

class Rain(Effect):
    category = "nature"
    NIGHT = [(0, (0.0, 0.02, 0.07)), (1, (0.01, 0.05, 0.12))]
    DROP, SPLASH = (0.35, 0.7, 1), (0.4, 0.75, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.drops = [self.spawn(True) for _ in range(9)]
        self.splashes = []  # (x, time)

    def spawn(self, anywhere=False):
        return [random.uniform(0, self.width), random.uniform(0, self.height) if anywhere else -random.uniform(5, 60),
                random.uniform(70, 120)]

    def render(self, t):
        dt = self.step(t)
        out = [ramp(self.NIGHT, l.v) for l in self.lamps]
        for n, d in enumerate(self.drops):
            d[1] += d[2] * dt
            if d[1] > self.height:
                self.splashes.append((d[0], t))
                d = self.drops[n] = self.spawn()
            splat(self.lamps, out, d[0], d[1], self.DROP, 8, 0.8)
            splat(self.lamps, out, d[0], d[1] - 12, self.DROP, 7, 0.3)
        self.splashes = [s for s in self.splashes if t - s[1] < 1.2]
        for x0, t0 in self.splashes:  # rings spreading over the puddle along the bottom row
            age = t - t0
            for i, l in enumerate(self.lamps):
                d = math.hypot(l.x - x0, (l.y - self.height) * 2)
                w = math.exp(-((d - age * 70) / 10) ** 2) * (1 - age / 1.2) ** 2 * 0.7
                if w > 0.01:
                    out[i] = add(out[i], scale(self.SPLASH, w))
        return out


class Snow(Effect):
    category = "nature"
    SKY = [(0, (0.01, 0.02, 0.09)), (1, (0.03, 0.05, 0.16))]
    FLAKE, DRIFT = (0.85, 0.9, 1), (0.55, 0.7, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.flakes = [self.spawn(True) for _ in range(12)]
        self.ground = [i for i, l in enumerate(lamps) if l.v > 0.9]
        self.snow = [0.0] * len(lamps)  # settled snow, melting slowly

    def spawn(self, anywhere=False):
        return [random.uniform(0, self.width), random.uniform(0, self.height) if anywhere else -random.uniform(5, 40),
                random.uniform(12, 26), random.uniform(0, TAU), random.uniform(8, 12)]

    def render(self, t):
        dt = self.step(t)
        melt = math.exp(-dt / 25)
        self.snow = [s * melt for s in self.snow]
        for n, f in enumerate(self.flakes):
            f[1] += f[2] * dt
            if f[1] > self.height and self.ground:
                x = f[0] + 9 * math.sin(t * 0.8 + f[3])
                i = min(self.ground, key=lambda j: abs(self.lamps[j].x - x))
                self.snow[i] = min(0.8, self.snow[i] + 0.25)
                self.flakes[n] = self.spawn()
        out = [add(ramp(self.SKY, l.v), scale(self.DRIFT, s)) for l, s in zip(self.lamps, self.snow)]
        for f in self.flakes:
            splat(self.lamps, out, f[0] + 9 * math.sin(t * 0.8 + f[3]), f[1], self.FLAKE, f[4], 0.85)
        return out


class Caustics(Effect):
    category = "nature"
    WATER = [(0, (0, 0.08, 0.16)), (1, (0, 0.015, 0.07))]
    LIGHT = (0.45, 1, 0.9)

    def render(self, t):
        out = []
        for l in self.lamps:
            x, y = l.x / 45, l.y / 45
            n = fbm(x + 0.5 * math.sin(t * 0.6 + y * 2), y + t * 0.2 + 0.5 * math.cos(t * 0.5 + x))
            m = fbm(x * 0.8 - t * 0.15 + 9.3, y * 0.8 + 4.1)
            light = (1 - abs(2 * n - 1)) ** 9 + 0.6 * (1 - abs(2 * m - 1)) ** 12  # bright where the ripples focus
            out.append(add(ramp(self.WATER, l.v), scale(self.LIGHT, light * (0.6 + 0.4 * (1 - l.v)))))
        return out


class Tide(Effect):
    category = "nature"
    PERIOD = 6.0
    SAND, WATER, GLOW = (0.02, 0.012, 0.0), (0.0, 0.02, 0.08), (0.1, 0.85, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.wet = [0.0] * len(lamps)
        self.phase = [random.random() for _ in lamps]

    def render(self, t):
        dt = self.step(t)
        reach = math.sin(math.pi * ((t / self.PERIOD) % 1)) ** 1.5  # a wave surges up the board, then draws back
        dry, out = math.exp(-dt / 2.5), []
        for i, l in enumerate(self.lamps):
            front = 1 - reach * 0.85 + 0.12 * (noise(l.u * 4, t * 0.3) - 0.5)
            under = smoothstep(front - 0.05, front + 0.1, l.v)
            self.wet[i] = max(self.wet[i] * dry, under)
            twinkle = 0.5 + 0.5 * math.sin(t * 7 + self.phase[i] * TAU)
            glow = math.exp(-((l.v - front) / 0.08) ** 2) * (0.7 + 0.3 * twinkle) \
                + self.wet[i] * 0.35 * twinkle * (1 - under)  # plankton glitter left on the wet sand
            out.append(add(mix(self.SAND, self.WATER, under), scale(self.GLOW, glow)))
        return out


# ---------------------------------------------------------------- more elements

class Ice(Effect):
    category = "elements"
    FROST = [(0, (0.0, 0.05, 0.2)), (0.4, (0.05, 0.35, 0.75)), (0.7, (0.4, 0.8, 1)), (1, (0.9, 0.97, 1))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.glints = make_stars(len(lamps), 0.35)

    def render(self, t):
        out = []
        for l, glint in zip(self.lamps, self.glints):
            x, y = l.x / 70, l.y / 70
            f = fbm(x * 1.5 + t * 0.03, y * 1.5 - t * 0.02)
            facet = abs(math.sin((x * 2.3 + y * 1.7) * 3 + f * 6))  # crystal facets catching the light
            c = ramp(self.FROST, 0.35 * f + 0.45 * facet ** 3 + 0.2 * noise(x * 3 + t * 0.2, y * 3))
            out.append(add(c, scale(star_light(glint, t), 0.8)))
        return out


class Toxic(Effect):
    category = "elements"
    SLUDGE = [(0, (0.04, 0, 0.08)), (0.45, (0.1, 0.22, 0.0)), (0.75, (0.35, 0.9, 0.0)), (1, (0.8, 1, 0.3))]
    BUBBLE, POP = (0.55, 1, 0.1), (0.9, 1, 0.6)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.bubbles, self.due = [], 0.0

    def render(self, t):
        dt = self.step(t)
        out = [ramp(self.SLUDGE, l.v * 0.6 + 0.45 * fbm(l.u * 5 + t * 0.15, l.v * 2 - t * 0.25)) for l in self.lamps]
        self.due += dt * 3
        while self.due >= 1:
            self.due -= 1
            self.bubbles.append([random.uniform(0, self.width), self.height + 5, random.uniform(20, 45), t,
                                 random.uniform(1.8, 3.5), random.uniform(0, TAU)])
        alive = []
        for b in self.bubbles:
            age = (t - b[3]) / b[4]
            if age >= 1:
                continue
            b[1] -= b[2] * dt
            x = b[0] + 6 * math.sin(t * 2 + b[5])
            if age < 0.88:
                splat(self.lamps, out, x, b[1], self.BUBBLE, 8 + 4 * age, 0.4 + 0.5 * age)
            else:  # pop
                splat(self.lamps, out, x, b[1], self.POP, 16, (1 - age) * 8)
            alive.append(b)
        self.bubbles = alive
        return out


# ---------------------------------------------------------------- more neon

class Cyberpunk(Effect):
    category = "neon"
    PINK, VIOLET, CYAN = (1, 0.05, 0.55), (0.35, 0.05, 0.9), (0, 0.9, 1)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.rows = sorted({round(l.y) for l in lamps})
        self.glitches, self.next = [], 0.0
        self.city = [(0, self.PINK), (0.5, self.VIOLET), (1, self.CYAN)]

    def render(self, t):
        scan = (t * 45) % (self.height + 60) - 30
        out = []
        for l in self.lamps:
            g = 0.5 + 0.5 * math.sin(l.u * 5 - t * 0.6 + math.sin(l.v * 3 + t * 0.4))
            k = 0.35 + 0.25 * math.sin(l.y / 6 + t * 3) ** 2 + 0.6 * math.exp(-((l.y - scan) / 8) ** 2)
            out.append(scale(ramp(self.city, g), k))
        if t >= self.next:  # glitch: a slice of a row tears and flashes
            x0 = random.uniform(-20, self.width)
            self.glitches.append((random.choice(self.rows), x0, x0 + random.uniform(30, 120), t,
                                  random.choice((self.PINK, self.CYAN, (1, 1, 1)))))
            self.next = t + random.uniform(0.05, 0.4)
        self.glitches = [g for g in self.glitches if t - g[3] < 0.18]
        for row, x0, x1, _, col in self.glitches:
            for i, l in enumerate(self.lamps):
                if abs(l.y - row) < 5 and x0 <= l.x <= x1:
                    out[i] = col
        return out


class Scanner(Effect):
    category = "neon"
    takes_color = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.level = [0.0] * len(lamps)

    def render(self, t):
        decay = math.exp(-self.step(t) * 5)
        x = self.cx + (self.cx + 15) * math.sin(t * 2.2)
        col, out = self.color or COLORS["red"], []
        for i, l in enumerate(self.lamps):
            hit = math.exp(-((l.x - x) / 11) ** 2)
            self.level[i] = max(self.level[i] * decay, hit)  # the eye leaves a fading trail
            out.append(add(scale(col, 0.03 + self.level[i]), scale((1, 1, 1), 0.35 * hit ** 4)))
        return out


class Lightcycles(Effect):
    category = "neon"
    BIKES = [(0, 0.85, 1), (1, 0.45, 0), (1, 0.1, 0.65)]
    STEP = 0.09  # s per cell

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.cols, self.rows, self.lamp_at, _ = grid_cells(lamps)
        self.trail, self.tint = [0.0] * len(lamps), [(0, 0, 0)] * len(lamps)
        self.bikes = [[random.randrange(self.cols), random.randrange(self.rows), *random.choice(
            ((1, 0), (-1, 0), (0, 1), (0, -1))), col] for col in self.BIKES]
        self.acc = 0.0

    def move(self, b):
        straight, turns = (b[2], b[3]), [(b[3], b[2]), (-b[3], -b[2])]
        random.shuffle(turns)
        for dx, dy in ([straight] if random.random() < 0.8 else []) + turns + [straight, (-b[2], -b[3])]:
            c, r = b[0] + dx, b[1] + dy
            if 0 <= c < self.cols and 0 <= r < self.rows:
                b[:4] = c, r, dx, dy
                return

    def render(self, t):
        dt = self.step(t)
        self.acc += dt
        while self.acc >= self.STEP:
            self.acc -= self.STEP
            for b in self.bikes:
                self.move(b)
                i = self.lamp_at.get((b[0], b[1]))
                if i is not None:
                    self.trail[i], self.tint[i] = 1.0, b[4]
        decay = math.exp(-dt * 1.6)
        self.trail = [v * decay for v in self.trail]
        out = [add((0.0, 0.015, 0.04), scale(c, v ** 1.5)) for c, v in zip(self.tint, self.trail)]
        for b in self.bikes:
            i = self.lamp_at.get((b[0], b[1]))
            if i is not None:
                out[i] = mix(b[4], (1, 1, 1), 0.6)
        return out


class Disco(Effect):
    category = "neon"
    BEAT = 0.5

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.tiles = [hsv(random.random()) for _ in lamps]
        self.flash, self.seen = [0.0] * len(lamps), -1

    def render(self, t):
        dt, beat = self.step(t), int(t / self.BEAT)
        if beat != self.seen:  # on every beat a third of the floor changes colour
            self.seen = beat
            for i in range(len(self.lamps)):
                if random.random() < 0.35:
                    self.tiles[i], self.flash[i] = hsv(random.random(), random.uniform(0.85, 1)), 1.0
        k = math.exp(-dt * 3)
        self.flash = [f * k for f in self.flash]
        out = [scale(c, 0.42 + 0.58 * f) for c, f in zip(self.tiles, self.flash)]
        for n in range(3):  # mirror-ball spots
            a = t * 0.9 + n * TAU / 3
            splat(self.lamps, out, self.cx + self.width * 0.4 * math.cos(a),
                  self.cy + self.height * 0.4 * math.sin(a * 1.3), (1, 1, 1), 12, 0.6)
        return out


# ---------------------------------------------------------------- more mood / classic

class Pastel(Effect):
    category = "mood"
    CANDY = [(0, (1, 0.55, 0.75)), (0.2, (1, 0.8, 0.55)), (0.4, (0.65, 1, 0.75)), (0.6, (0.55, 0.8, 1)),
             (0.8, (0.8, 0.6, 1)), (1, (1, 0.55, 0.75))]

    def render(self, t):
        return [scale(ramp(self.CANDY, (fbm(l.x / 90 + t * 0.05, l.y / 90 - t * 0.03) * 1.6 + t * 0.02) % 1), 0.85)
                for l in self.lamps]


class Candle(Effect):
    category = "mood"
    FLAME = [(0, (0.03, 0.005, 0)), (0.4, (0.5, 0.12, 0.0)), (0.75, (1, 0.45, 0.05)), (1, (1, 0.8, 0.45))]

    def render(self, t):
        flicker = 0.75 + 0.25 * noise(t * 4, 3.3) + 0.1 * noise(t * 11, 7.7)
        fx, fy = self.cx + 15 * (noise(t * 1.5, 1.1) - 0.5), self.height * 0.75
        return [ramp(self.FLAME, flicker * math.exp(-math.hypot(l.x - fx, (l.y - fy) * 1.4) / 90)
                     + 0.08 * noise(l.u * 6, t * 2)) for l in self.lamps]


class Prism(Effect):
    category = "classic"

    def render(self, t):
        px, py, out = self.width * 0.36, self.cy, []
        spread = 0.55 + 0.1 * math.sin(t * 0.7)
        for l in self.lamps:
            if l.x < px:  # a white beam coming in from the left
                d = abs(l.y - (py + (l.x - px) * 0.18))
                c = scale((1, 1, 1), 0.04 + 0.8 * math.exp(-(d / 9) ** 2))
            else:  # fanning out into a rainbow
                ang = math.atan2(l.y - py, (l.x - px) * 1.4) / spread
                shimmer = 0.75 + 0.25 * math.sin(math.hypot(l.x - px, l.y - py) / 12 - t * 3)
                c = scale(hsv(0.8 * (ang + 1) / 2), smoothstep(1.2, 0.9, abs(ang)) * shimmer)
            out.append(c)
        splat(self.lamps, out, px, py, (1, 1, 1), 10, 0.7)
        return out


# ---------------------------------------------------------------- games

class Life(Effect):
    category = "games"
    TICK = 0.4

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.cols, self.rows, _, self.cell_of = grid_cells(lamps)
        self.level, self.tint = [0.0] * len(lamps), [(0, 0, 0)] * len(lamps)
        self.next = None
        self.seed()

    def seed(self):
        self.hue, self.gen, self.seen = random.random(), 0, []
        self.alive = {(c, r): self.hue for c in range(self.cols) for r in range(self.rows) if random.random() < 0.38}

    def tick(self):
        counts = {}
        for c, r in self.alive:
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    if dc or dr:
                        cell = ((c + dc) % self.cols, (r + dr) % self.rows)  # the board wraps around
                        counts[cell] = counts.get(cell, 0) + 1
        self.gen += 1
        born = (self.hue + self.gen * 0.025) % 1
        alive = {cell: self.alive.get(cell, born) for cell, n in counts.items()
                 if n == 3 or n == 2 and cell in self.alive}
        shape = frozenset(alive)
        if not alive or shape in self.seen or self.gen > 90:  # died out or got stuck: start over
            self.seed()
        else:
            self.alive = alive
            self.seen = self.seen[-5:] + [shape]

    def render(self, t):
        dt = self.step(t)
        if self.next is None or t >= self.next:
            if self.next is not None:
                self.tick()
            self.next = t + self.TICK
        ease, out = clamp(dt * 7), []
        for i, cell in enumerate(self.cell_of):
            hue = self.alive.get(cell)
            if hue is not None:
                self.tint[i] = hsv(hue, 0.75)
            self.level[i] += ((1.0 if hue is not None else 0.0) - self.level[i]) * ease
            out.append(add((0.01, 0, 0.03), scale(self.tint[i], self.level[i])))
        return out


class Snake(Effect):
    category = "games"
    TICK = 0.13
    FOOD = (1, 0.1, 0.35)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.cols, self.rows, self.lamp_at, _ = grid_cells(lamps)
        self.next = None
        self.reset()

    def reset(self):
        r = self.rows // 2
        self.body, self.dir, self.dead = [(3, r), (2, r), (1, r)], (1, 0), None
        self.food = self.place()

    def place(self):
        free = [cell for cell in self.lamp_at if cell not in self.body]
        return random.choice(free) if free else None

    def free_around(self, cell, body):
        return sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if 0 <= cell[0] + dx < self.cols and 0 <= cell[1] + dy < self.rows
                   and (cell[0] + dx, cell[1] + dy) not in body)

    def tick(self, t):
        (hx, hy), fx, fy = self.body[0], *self.food
        moves = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cell = (hx + dx, hy + dy)
            if 0 <= cell[0] < self.cols and 0 <= cell[1] < self.rows and cell not in self.body[:-1]:
                room = self.free_around(cell, self.body[:-1])
                moves.append((abs(cell[0] - fx) + abs(cell[1] - fy) - 0.8 * room + random.random() * 0.5, cell))
        if not moves:
            self.dead = t
            return
        head = min(moves)[1]
        self.body.insert(0, head)
        if head == self.food:
            self.food = self.place()
            if self.food is None or len(self.body) > 32:  # board full: a victory lap, then again
                self.dead = t
        else:
            self.body.pop()

    def render(self, t):
        if self.next is None:
            self.next = t
        if self.dead is not None:
            if t - self.dead > 1.2:
                self.reset()
        elif t >= self.next:
            self.tick(t)
            self.next = t + self.TICK
        out = [(0.0, 0.02, 0.012)] * len(self.lamps)
        n = len(self.body)
        flash = 0.5 + 0.5 * math.cos((t - self.dead) * 18) if self.dead is not None else 1.0
        for k, cell in enumerate(self.body):
            i = self.lamp_at.get(cell)
            if i is not None:
                col = (0.75, 1, 0.85) if k == 0 else hsv(0.33 + 0.22 * k / n, 0.9, 1 - 0.55 * k / n)
                out[i] = scale(col, flash)
        i = self.lamp_at.get(self.food) if self.food else None
        if i is not None:
            out[i] = scale(self.FOOD, 0.55 + 0.45 * math.sin(t * 8))
        return out


class Bounce(Effect):
    category = "games"

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.balls = [[random.uniform(0, self.width), random.uniform(0, self.height),
                       random.choice((1, -1)) * random.uniform(60, 110), random.choice((1, -1)) * random.uniform(35, 70),
                       random.random()] for _ in range(3)]

    def render(self, t):
        dt = self.step(t)
        out = [(0.01, 0.0, 0.03)] * len(self.lamps)
        for b in self.balls:
            for axis, size in ((0, self.width), (1, self.height)):
                b[axis] += b[axis + 2] * dt
                if not 0 <= b[axis] <= size:  # a new colour on every wall it hits
                    b[axis], b[axis + 2], b[4] = clamp(b[axis], 0, size), -b[axis + 2], (b[4] + 0.17) % 1
            col = hsv(b[4], 0.8)
            for back, k in ((0.0, 1.0), (0.07, 0.45), (0.14, 0.2)):
                splat(self.lamps, out, b[0] - b[2] * back, b[1] - b[3] * back, col, 12, k)
        return out


class Pong(Effect):
    category = "games"
    LEFT, RIGHT = (0, 0.85, 1), (1, 0.15, 0.6)
    PADDLE = 26  # mm half-height

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.paddles = [self.cy, self.cy]
        self.flash = None  # (side that missed, time)
        self.serve(random.choice((1, -1)))

    def serve(self, direction):
        self.ball = [self.cx, self.cy, direction * 120, random.uniform(-60, 60)]

    def render(self, t):
        dt = self.step(t)
        b = self.ball
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        if not 0 <= b[1] <= self.height:
            b[1], b[3] = clamp(b[1], 0, self.height), -b[3]
        for side, x_edge in ((0, PITCH * 0.5), (1, self.width - PITCH * 0.5)):
            towards = b[2] < 0 if side == 0 else b[2] > 0
            target = b[1] if towards else self.cy
            p = self.paddles[side]
            self.paddles[side] = p + clamp(target - p, -75 * dt, 75 * dt)  # a little slower than the ball
            if towards and (b[0] <= x_edge if side == 0 else b[0] >= x_edge):
                if abs(b[1] - self.paddles[side]) <= self.PADDLE:
                    b[2] = -b[2] * 1.05
                    b[3] += (b[1] - self.paddles[side]) * 2.5
                    b[2] = math.copysign(min(abs(b[2]), 260), b[2])
                else:
                    self.flash = (side, t)
                    self.serve(1 if side == 0 else -1)
        out = []
        for l in self.lamps:
            c = (0.008, 0.0, 0.03)
            if abs(l.x - self.cx) < PITCH * 0.6 and round(l.y / PITCH) % 2 == 0:
                c = (0.06, 0.06, 0.08)  # the net
            if l.x < PITCH * 0.6 and abs(l.y - self.paddles[0]) <= self.PADDLE:
                c = self.LEFT
            if l.x > self.width - PITCH * 0.6 and abs(l.y - self.paddles[1]) <= self.PADDLE:
                c = self.RIGHT
            out.append(c)
        if self.flash and t - self.flash[1] < 0.6:  # a point: the winner's side lights up
            side, age = self.flash[0], t - self.flash[1]
            col = self.RIGHT if side == 0 else self.LEFT
            for i, l in enumerate(self.lamps):
                if (l.x > self.cx) == (side == 0):
                    out[i] = add(out[i], scale(col, 0.5 * (1 - age / 0.6)))
        splat(self.lamps, out, b[0], b[1], (1, 1, 1), 9, 1.0)
        return out


# ---------------------------------------------------------------- music effects

class Equalizer(Effect):
    category = "music"
    needs_audio = True
    LEVELS = [(0, (0, 0.9, 1)), (0.45, (0.55, 0.1, 1)), (0.75, (1, 0.1, 0.6)), (1, (1, 0.55, 0.75))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        cols = max(round(l.x / PITCH) for l in lamps) + 1
        self.rows = max(round(l.y / PITCH) for l in lamps) + 1
        # (band, height in rows from the bottom) per lamp: bass on the left, treble on the right
        self.cells = [(min(Audio.NBANDS - 1, round(l.x / PITCH) * Audio.NBANDS // cols),
                       self.rows - 1 - round(l.y / PITCH)) for l in lamps]
        self.peaks = [0.0] * Audio.NBANDS

    def render(self, t):
        dt, bands = self.step(t), self.audio.bands
        self.peaks = [max(b, p - dt * 0.5) for b, p in zip(bands, self.peaks)]
        out = []
        for band, h in self.cells:
            col = ramp(self.LEVELS, h / max(1, self.rows - 1))  # one row: a plain level meter
            c = scale(col, 0.05 + 0.95 * clamp(bands[band] * self.rows - h))
            if self.peaks[band] > 0.08 and h == min(self.rows - 1, int(self.peaks[band] * self.rows)):
                c = add(c, (0.7, 0.7, 0.7))
            out.append(c)
        return out


class Pulse(Effect):
    category = "music"
    needs_audio = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.hue, self.seen = random.random(), 0

    def render(self, t):
        a = self.audio
        if a.beats != self.seen:
            self.seen, self.hue = a.beats, (self.hue + 0.618) % 1
        reach, out = 0.2 + 0.9 * a.bass, []
        for l in self.lamps:
            d = math.hypot((l.x - self.cx) / self.cx, (l.y - self.cy) / self.cy) / math.sqrt(2)
            k = smoothstep(reach, reach - 0.4, d)  # bass pushes the glow outwards
            c = scale(hsv(self.hue + d * 0.18, 0.85), 0.04 + k * (0.35 + 0.65 * a.level) + 0.3 * a.beat)
            if random.random() < a.treble * 0.06:
                c = add(c, (0.5, 0.5, 0.5))
            out.append(c)
        return out


class Shockwave(Effect):
    category = "music"
    needs_audio = True
    SPEED, WIDTH, LIFE = 230, 20, 1.3  # mm/s, mm, s

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.rings, self.seen, self.hue = [], 0, random.random()

    def render(self, t):
        a = self.audio
        if a.beats != self.seen:
            self.seen, self.hue = a.beats, (self.hue + 0.618) % 1
            self.rings.append((t, self.hue, a.strength))
        self.rings = [r for r in self.rings if t - r[0] < self.LIFE]
        out = [scale(hsv(self.hue + l.u * 0.15, 0.9), 0.03 + 0.12 * a.level) for l in self.lamps]
        for t0, hue, strength in self.rings:
            age = t - t0
            radius, fade = age * self.SPEED, (1 - age / self.LIFE) ** 1.5 * (0.5 + 0.5 * strength)
            for i, l in enumerate(self.lamps):
                d = math.hypot(l.x - self.cx, (l.y - self.cy) * 1.5)
                w = math.exp(-((d - radius) / self.WIDTH) ** 2) * fade
                if w > 0.01:
                    out[i] = add(out[i], scale(hsv(hue + d / 700), w))
        return out


class Fireworks(Effect):
    category = "music"
    needs_audio = True
    SKY = (0.008, 0.0, 0.03)

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.parts, self.seen, self.last_burst = [], 0, 0.0

    def burst(self, t, strength):
        x, y = random.uniform(0.12, 0.88) * self.width, random.uniform(0.15, 0.7) * self.height
        hue = random.random()
        for _ in range(10 + int(14 * strength)):
            ang, v = random.uniform(0, TAU), random.uniform(50, 150) * (0.7 + 0.6 * strength)
            col = hsv(hue + random.uniform(-0.05, 0.05), random.uniform(0.6, 0.95))
            self.parts.append([x, y, v * math.cos(ang), v * math.sin(ang) * 0.7, t, random.uniform(0.8, 1.4), col])
        self.last_burst = t

    def render(self, t):
        a, dt = self.audio, self.step(t)
        if a.beats != self.seen:
            self.seen = a.beats
            self.burst(t, a.strength)
        elif a.level > 0.15 and t - self.last_burst > 1.5:  # keep going through beatless passages
            self.burst(t, 0.3)
        out, drag, alive = [self.SKY] * len(self.lamps), math.exp(-dt * 1.8), []
        for p in self.parts:
            age = (t - p[4]) / p[5]
            if age >= 1:
                continue
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[2] *= drag
            p[3] = p[3] * drag + 45 * dt  # gravity
            crackle = 0.6 + 0.4 * random.random() if age > 0.5 else 1.0
            splat(self.lamps, out, p[0], p[1], p[6], 10, (1 - age) ** 1.4 * crackle)
            alive.append(p)
        self.parts = alive[-150:]
        return out


class Vibe(Effect):
    category = "music"
    needs_audio = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.phase = 0.0

    def render(self, t):
        a = self.audio
        self.phase += self.step(t) * (0.25 + 2.5 * a.level)  # louder -> flows faster
        p = self.phase
        shift = a.centroid * 0.6 + p * 0.02  # brighter music shifts the palette
        glow = 0.12 + 0.88 * clamp(a.level * 1.3) + 0.35 * a.beat
        return [scale(ramp(Plasma.NEON, (Plasma.field(l, p) / 6 + 0.5 + shift) % 1), glow) for l in self.lamps]


class VU(Effect):
    category = "music"
    needs_audio = True
    METER = [(0, (0, 1, 0.55)), (0.6, (0.9, 0.9, 0)), (0.82, (1, 0.35, 0)), (1, (1, 0, 0.15))]

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.peaks = [0.0, 0.0]

    def render(self, t):
        dt, levels = self.step(t), (self.audio.left, self.audio.right)
        self.peaks = [max(lv, p - dt * 0.4) for lv, p in zip(levels, self.peaks)]
        out = []
        for l in self.lamps:
            side = 0 if l.x < self.cx else 1  # left channel grows leftwards from the centre
            pos = abs(l.x - self.cx) / self.cx
            col = ramp(self.METER, pos)
            c = add(scale(col, 0.05), scale(col, 0.95 * clamp((levels[side] - pos) * 10 + 0.5)))
            if self.peaks[side] > 0.05 and abs(self.peaks[side] - pos) < 0.067:
                c = add(c, (0.8, 0.8, 0.8))
            out.append(c)
        return out


class Waterfall(Effect):
    category = "music"
    needs_audio = True
    HEAT = [(0, (0, 0, 0.03)), (0.3, (0.2, 0, 0.6)), (0.6, (1, 0.1, 0.5)), (0.85, (1, 0.6, 0.1)), (1, (1, 1, 0.8))]
    ROW_TIME = 0.08  # s of history per row

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        cols, rows, _, cells = grid_cells(lamps)
        self.cells = [(min(Audio.NBANDS - 1, c * Audio.NBANDS // cols), r) for c, r in cells]
        self.history, self.acc = [[0.0] * Audio.NBANDS for _ in range(rows)], 0.0

    def render(self, t):
        self.acc += self.step(t)
        if self.acc >= self.ROW_TIME:  # newest sound on the top row, flowing down
            self.acc = 0.0
            self.history = [list(self.audio.bands)] + self.history[:-1]
        return [ramp(self.HEAT, self.history[r][band]) for band, r in self.cells]


class Mirror(Effect):
    category = "music"
    needs_audio = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        # bass in the middle, treble at both ends; bars grow up and down from the centre row
        self.cells = [(min(Audio.NBANDS - 1, int(abs(l.x - self.cx) / self.cx * Audio.NBANDS)),
                       abs(l.y - self.cy) / (self.height / 2)) for l in lamps]

    def render(self, t):
        a, out = self.audio, []
        for band, dv in self.cells:
            lit = clamp((a.bands[band] * 1.15 - dv * 0.85) * 5)
            col = hsv(0.85 - band / Audio.NBANDS * 0.55 + t * 0.02, 0.9)
            out.append(add(scale(col, 0.04 + 0.96 * lit), scale((1, 1, 1), 0.3 * a.beat * (1 - dv) * (band < 3))))
        return out


class Aura(Effect):
    category = "music"
    needs_audio = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.phase = 0.0

    def render(self, t):
        a = self.audio
        self.phase += self.step(t) * (0.1 + 0.6 * a.level)
        p, hue = self.phase, 0.78 - a.centroid * 0.6  # bassy music glows violet, bright music cyan-green
        out = []
        for l in self.lamps:
            n = fbm(l.x / 80 + p, l.y / 80 - p * 0.6)
            v = 0.08 + 0.92 * clamp(a.level * 1.2) * (0.5 + 0.5 * n) + 0.3 * a.beat * (1 - l.v * 0.5)
            out.append(hsv(hue + (n - 0.5) * 0.25, 0.85, v))
        return out


# ---------------------------------------------------------------- layers (drawn over an effect)

class Glow(Effect):
    category = "typing"
    takes_color = True
    needs_keys = True
    HOLD, FADE = 0.08, 1.0  # s

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.near = neighbours(lamps, 24)
        self.hits = {}  # lamp -> (time, strength, colour)
        self.hue = random.random()

    def level(self, i, t):
        t0, s, _ = self.hits.get(i, (0.0, 0.0, None))
        return s * clamp(1 - max(0.0, t - t0 - self.HOLD) / self.FADE) ** 1.6

    def on_key(self, i, t):
        self.hue = (self.hue + 0.618) % 1
        col = self.color or hsv(self.hue, 0.65)
        self.hits[i] = (t, 1.0, col)
        for j in self.near[i]:
            if self.level(j, t) < 0.35:
                self.hits[j] = (t, 0.35, col)

    def active(self, t):
        return bool(self.hits)

    def apply(self, frame, t):
        for i in list(self.hits):
            a = self.level(i, t)
            if a <= 0:
                del self.hits[i]
            else:
                frame[i] = mix(frame[i], self.hits[i][2], a)


class Ripple(Effect):
    category = "typing"
    takes_color = True
    needs_keys = True
    SPEED, WIDTH, LIFE = 180, 16, 1.6  # mm/s, mm, s

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.rings = []
        self.hue = random.random()

    def on_key(self, i, t):
        self.hue = (self.hue + 0.618) % 1  # golden-ratio steps keep consecutive rings distinct
        self.rings.append((self.lamps[i].x, self.lamps[i].y, t, self.hue))

    def active(self, t):
        return bool(self.rings)

    def apply(self, frame, t):
        self.rings = [r for r in self.rings if t - r[2] < self.LIFE]
        for x0, y0, t0, hue in self.rings:
            radius, fade = (t - t0) * self.SPEED, (1 - (t - t0) / self.LIFE) ** 1.5
            for i, l in enumerate(self.lamps):
                d = math.hypot(l.x - x0, l.y - y0)
                a = math.exp(-((d - radius) / self.WIDTH) ** 2) * fade
                if a > 0.01:
                    frame[i] = mix(frame[i], self.color or hsv(hue + d / 500), a)


class Sparks(Effect):
    category = "typing"
    takes_color = True
    needs_keys = True

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.parts = []
        self.hue = random.random()

    def on_key(self, i, t):
        self.hue = (self.hue + 0.618) % 1
        l = self.lamps[i]
        for _ in range(9):
            ang, v = random.uniform(0, TAU), random.uniform(70, 170)
            col = self.color or hsv(self.hue + random.uniform(-0.06, 0.06), 0.8)
            self.parts.append([l.x, l.y, v * math.cos(ang), v * math.sin(ang), t, random.uniform(0.6, 1.1), col])
        del self.parts[:-90]

    def active(self, t):
        return bool(self.parts)

    def apply(self, frame, t):
        dt = self.step(t)
        drag, alive = math.exp(-dt * 2.5), []
        for p in self.parts:
            age = (t - p[4]) / p[5]
            if age >= 1:
                continue
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[2] *= drag
            p[3] = p[3] * drag + 60 * dt
            splat(self.lamps, frame, p[0], p[1], p[6], 11, (1 - age) ** 1.3)
            alive.append(p)
        self.parts = alive


class Beat(Effect):
    category = "music"
    needs_audio = True

    def active(self, t):
        return True

    def apply(self, frame, t):
        k = 0.3 + 0.7 * clamp(self.audio.level * 1.3) + 0.45 * self.audio.beat
        frame[:] = [scale(c, k) for c in frame]


class Laser(Effect):
    category = "typing"
    takes_color = True
    needs_keys = True
    SPEED, LIFE = 320, 0.9  # mm/s, s

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.beams = []
        self.hue = random.random()

    def on_key(self, i, t):
        self.hue = (self.hue + 0.618) % 1
        self.beams.append((self.lamps[i].x, self.lamps[i].y, t, self.color or hsv(self.hue, 0.7)))
        del self.beams[:-12]

    def active(self, t):
        return bool(self.beams)

    def apply(self, frame, t):
        self.beams = [b for b in self.beams if t - b[2] < self.LIFE]
        for x0, y0, t0, col in self.beams:
            age = t - t0
            reach, fade = age * self.SPEED, (1 - age / self.LIFE) ** 1.5
            for i, l in enumerate(self.lamps):
                w = 0.0
                if abs(l.y - y0) < 6:  # beams shooting both ways along the row
                    d = abs(l.x - x0)
                    w = 0.3 + 0.7 * math.exp(-(reach - d) / 25) if d <= reach else math.exp(-((d - reach) / 8) ** 2)
                elif abs(l.x - x0) < 9:  # and a short flare up and down the column
                    w = math.exp(-((abs(l.y - y0) - age * 150) / 10) ** 2)
                if w * fade > 0.01:
                    frame[i] = mix(frame[i], col, clamp(w * fade))


class Sparkle(Effect):
    category = "music"
    needs_audio = True
    LIFE = 0.35

    def __init__(self, lamps, color=None):
        super().__init__(lamps, color)
        self.glints = {}  # lamp -> (time, colour)

    def active(self, t):
        return True

    def apply(self, frame, t):
        a = self.audio
        for _ in range(int(a.treble * 2.5 + a.beat * 4 + random.random())):  # hi-hats and beats throw glitter
            self.glints[random.randrange(len(self.lamps))] = (t, random.choice(STAR_COLORS))
        for i, (t0, col) in list(self.glints.items()):
            age = (t - t0) / self.LIFE
            if age >= 1:
                del self.glints[i]
            else:
                frame[i] = mix(frame[i], col, (1 - age) ** 2)


CATEGORIES = {  # gallery order: glyph, title, blurb
    "space": ("✦", "Space", "galaxies, nebulae and starlight"),
    "nature": ("❀", "Nature", "skies, seas, rain and blossoms"),
    "elements": ("♨", "Elements", "fire, lava, ice and acid"),
    "neon": ("ϟ", "Neon", "synthwave, glitches and laser light"),
    "mood": ("☾", "Mood", "soft, warm and slow"),
    "classic": ("◐", "Classic", "rainbows and single colours"),
    "games": ("◈", "Games", "little games that play themselves"),
    "music": ("♫", "Music", "reacts to whatever is playing"),
    "typing": ("⌨", "Typing", "reacts to your keys"),
}
EFFECTS = {
    "galaxy": (Galaxy, "a spiral galaxy slowly turning: golden core, violet arms, stars"),
    "nebula": (Nebula, "glowing space clouds swirling in magenta and blue, with stars"),
    "blackhole": (Blackhole, "a black hole ringed by a blazing, swirling accretion disk"),
    "warp": (Warp, "jumping to hyperspace: stars streak past from the centre"),
    "supernova": (Supernova, "a star swells, then explodes into a glowing shell"),
    "starfield": (Starfield, "twinkling stars, a faint nebula and the odd shooting star"),
    "comets": (Comets, "three neon comets trailing light around the stars"),
    "aurora": (Aurora, "northern-lights curtains drifting over a night sky"),
    "caustics": (Caustics, "sunlight dancing on the floor of a tropical sea"),
    "tide": (Tide, "glowing plankton washing up a midnight beach"),
    "ocean": (Ocean, "deep blue swells with glowing crests"),
    "rain": (Rain, "gentle night rain rippling a puddle along the bottom"),
    "storm": (Storm, "rain streaks and lightning flashes"),
    "snow": (Snow, "snowflakes drifting down and settling on the bottom row"),
    "sakura": (Sakura, "cherry-blossom petals drifting across a dusk sky"),
    "fireflies": (Fireflies, "fireflies drifting and blinking over a dark forest"),
    "fire": (Fire, "flames licking up from the bottom row"),
    "lava": (Lava, "molten blobs merging and splitting like a lava lamp"),
    "embers": (Embers, "glowing coals with sparks rising"),
    "ice": (Ice, "frozen crystal facets with glinting frost"),
    "toxic": (Toxic, "bubbling radioactive sludge"),
    "synthwave": (Synthwave, "retro neon sun over a scrolling grid"),
    "cyberpunk": (Cyberpunk, "neon city glow with scanlines and glitches"),
    "plasma": (Plasma, "flowing neon plasma in pink, violet and cyan"),
    "lightcycles": (Lightcycles, "neon bikes racing across the keys, leaving light trails"),
    "scanner": (Scanner, "a sweeping scanner eye, like a certain talking car (default red)"),
    "disco": (Disco, "a flashing dance floor with mirror-ball spots"),
    "vortex": (Vortex, "hypnotic rainbow spiral"),
    "matrix": (Matrix, "green code rain"),
    "sunset": (Sunset, "a slowly shimmering dusk gradient"),
    "opal": (Opal, "iridescent mother-of-pearl shimmer"),
    "pastel": (Pastel, "cotton-candy pastels drifting softly"),
    "candle": (Candle, "warm candlelight flickering in the middle"),
    "heartbeat": (Heartbeat, "a slow crimson heartbeat pulsing out from the centre"),
    "prism": (Prism, "a beam of white light splitting into a rainbow"),
    "wave": (Wave, "rainbow sweeping across the keys"),
    "spectrum": (Spectrum, "whole board gliding through every hue"),
    "breathe": (Breathe, "slow breathing in one colour (default violet)"),
    "rainbow": (Rainbow, "static rainbow"),
    "life": (Life, "Conway's Game of Life, every generation a new hue"),
    "snake": (Snake, "a snake that plays itself, hunting for food"),
    "pong": (Pong, "two paddles playing Pong down the sides"),
    "bounce": (Bounce, "glowing balls bouncing off the edges, changing colour"),
    "equalizer": (Equalizer, "spectrum analyser: bass on the left, treble on the right"),
    "mirror": (Mirror, "a mirrored equaliser: bass in the middle, bars growing up and down"),
    "waterfall": (Waterfall, "a scrolling spectrogram: sound falling down the board"),
    "pulse": (Pulse, "the board throbs with the bass and changes colour on every beat"),
    "shockwave": (Shockwave, "every beat sends a ring of light out from the centre"),
    "fireworks": (Fireworks, "beats launch bursts of sparks"),
    "vibe": (Vibe, "neon plasma that flows faster and glows brighter with the music"),
    "aura": (Aura, "a soft glow whose colour follows the music's tone"),
    "vu": (VU, "stereo level meter: left channel to the left, right to the right"),
    "heatmap": (Heatmap, "keys heat up the more you type them, then cool down"),
    "constellation": (Constellation, "each key becomes a star, joined to the last one"),
    "ink": (Ink, "every key drops ink that bleeds into its neighbours"),
}
LAYERS = {
    "ripple": (Ripple, "every key press sends out a ring of colour"),
    "glow": (Glow, "pressed keys flash and fade"),
    "sparks": (Sparks, "key presses burst into sparks"),
    "laser": (Laser, "key presses fire laser beams along their row"),
    "beat": (Beat, "makes any effect pump with the music"),
    "sparkle": (Sparkle, "glitter that twinkles with the hi-hats and beats"),
}
# a look: a whole setup saved under a name; looks without "keys" leave the pinned keys alone
LOOK_FIELDS = {"spec": "galaxy", "color": None, "brightness": 100, "speed": 1.0, "saturation": 100, "hue": 0}
STARTER_LOOKS = {
    "Deep space": {"spec": "nebula+sparks", "speed": 0.8},
    "Gamer": {"spec": "cyberpunk+laser", "speed": 1.2},
    "Night owl": {"spec": "dim+glow", "color": "warm", "brightness": 45},
    "Cozy": {"spec": "candle", "brightness": 75},
    "Party": {"spec": "disco+beat"},
    "Rave": {"spec": "shockwave+sparkle"},
    "Zen": {"spec": "sakura", "speed": 0.7, "saturation": 80},
    "Hacker": {"spec": "matrix+ripple", "color": "green"},
    "Lagoon": {"spec": "caustics+ripple", "color": "cyan"},
    "Retro": {"spec": "synthwave+glow"},
    "Arctic": {"spec": "ice+sparks", "color": "white"},
    "Inferno": {"spec": "fire+sparks", "color": "amber", "speed": 1.3},
}


# ---------------------------------------------------------------- config / scene

def parse_color(name):
    name = name.lower()
    if name in COLORS or name in HIDDEN_COLORS:
        return COLORS.get(name) or HIDDEN_COLORS[name]
    h = name.lstrip("#")
    if len(h) == 6:
        try:
            return tuple(b / 255 for b in bytes.fromhex(h))
        except ValueError:
            pass
    raise ValueError(name)


def parse_spec(spec, color):
    """'galaxy+ripple+beat', 'cyan', 'sparks' ... -> (base class, base colour, [(layer class, colour)])."""
    base, *layers = spec.lower().split("+")
    if base in LAYERS:
        base, layers = "dim", [base, *layers]
    for name in layers:
        if name not in LAYERS:
            raise ValueError(f"unknown layer '{name}'")
    try:
        color = parse_color(color) if color else None
    except ValueError:
        raise ValueError(f"unknown colour '{color}'") from None
    if base in EFFECTS:
        base_cls, base_color = EFFECTS[base][0], None
    else:
        try:
            base_cls, base_color = Solid, parse_color(base)
        except ValueError:
            raise ValueError(f"unknown effect or colour '{base}'") from None
    layer_classes = [LAYERS[name][0] for name in layers]
    takers = [c for c in layer_classes if c.takes_color] if layers else [c for c in [base_cls] if c.takes_color]
    if color and not takers:
        raise ValueError(f"'{spec}' doesn't take a colour")
    if color and not layers:
        base_color = color
    return base_cls, base_color, [(c, color if c.takes_color else None) for c in layer_classes]


def parse_keys(text, usages):
    """'wasd', 'w,a,s,d', 'esc,arrows' ... -> HID usages; usages: what 'all' means."""
    by_name = {name: usage for usage, (name, _) in KEYS.items()}
    out = []
    for name in text.lower().split(","):
        name = name.strip()
        if name in KEY_GROUPS:
            out += KEY_GROUPS[name] or usages
        elif name in by_name:
            out.append(by_name[name])
        else:
            raise ValueError(f"unknown key '{name}'")
    return list(dict.fromkeys(out))


def parse_pins(keys, lamps):
    """Config 'keys' ({usage: colour}) -> {lamp index: (r, g, b)}; entries that don't parse are skipped."""
    lamp_of = {l.usage: i for i, l in enumerate(lamps) if l.usage}
    pins = {}
    for usage, color in (keys or {}).items():
        try:
            i = lamp_of.get(int(usage))
            if i is not None:
                pins[i] = parse_color(color)
        except ValueError:
            continue
    return pins


def load_config():
    try:
        with open(CONFIG) as f:
            cfg = {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        cfg = dict(DEFAULTS)
    cfg["keys"] = dict(cfg["keys"] or {})  # never hand out DEFAULTS' own dict
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG + ".tmp", "w") as f:
        json.dump(cfg, f)
    os.replace(CONFIG + ".tmp", CONFIG)


def config_mtime():
    try:
        return os.stat(CONFIG).st_mtime_ns
    except OSError:
        return None


def split_spec(spec):
    """'galaxy+ripple' -> ('galaxy', ['ripple']); a bare layer runs over 'dim'; auto/off -> (None, [])."""
    if spec in ("auto", "off"):
        return None, []
    base, *layers = spec.lower().split("+")
    if base in LAYERS:
        base, layers = "dim", [base, *layers]
    return base, layers


def effect_pool(cfg, pool, base=None):
    """Effect names to shuffle through: 'favorites' (all if none), 'all', a category, or 'category' (base's)."""
    names = list(EFFECTS)
    if pool == "favorites":
        return [n for n in cfg["favorites"] if n in EFFECTS] or names
    if pool == "category" and base in EFFECTS:
        pool = EFFECTS[base][0].category
    if pool in CATEGORIES:
        return [n for n in names if EFFECTS[n][0].category == pool]
    return names


def with_base(cfg, name):
    """(spec, colour) with the base effect swapped for name, keeping the layers and, if it still fits, the colour."""
    spec = "+".join([name, *split_spec(cfg["spec"])[1]])
    for color in (cfg["color"], None):
        try:
            parse_spec(spec, color)
            return spec, color
        except ValueError:
            continue
    return name, None


def shuffled(cfg, pool, step=0):
    """cfg moved to another effect from the pool: the next (step 1) or previous (-1) one, or a random one."""
    base = split_spec(cfg["spec"])[0]
    names = effect_pool(cfg, pool, base)
    if step:
        name = names[(names.index(base) + step) % len(names)] if base in names else names[0]
    else:
        name = random.choice([n for n in names if n != base] or names)
    spec, color = with_base(cfg, name)
    return {**cfg, "spec": spec, "color": color}


def snapshot_look(cfg):
    look = {f: cfg[f] for f in LOOK_FIELDS}
    if look["spec"] in ("auto", "off"):
        look.update((cfg.get("resume") or {"spec": "galaxy", "color": None}))
    look["keys"] = dict(cfg["keys"])
    return look


def apply_look(cfg, look):
    cfg = {**cfg, **{f: look.get(f, default) for f, default in LOOK_FIELDS.items()}}
    if "keys" in look:
        cfg["keys"] = dict(look["keys"])
    return cfg


def all_looks(cfg):
    return {**STARTER_LOOKS, **cfg["looks"]}


class Scene:
    def __init__(self, cfg, lamps, audio=None):
        """audio: the previous scene's listener, reused so switching music effects doesn't reset it."""
        self.key = self.key_of(cfg)
        self.auto = cfg["spec"] == "auto"
        self.clock, self.audio, self.layers = 0.0, None, []
        self.tune(cfg, lamps)
        if self.auto:
            return
        base_cls, base_color, layers = parse_spec(cfg["spec"], cfg["color"])
        flip = cfg["flip"] if not base_cls.needs_keys else ""  # typing effects must stay under your fingers
        self.base = base_cls(flipped(lamps, flip) if flip else lamps, base_color)
        self.layers = [cls(lamps, color) for cls, color in layers]
        if any(e.needs_audio for e in (self.base, *self.layers)):
            self.audio = audio or Audio()
            self.audio.sensitivity = cfg["sensitivity"]
            for e in (self.base, *self.layers):
                e.audio = self.audio

    @staticmethod
    def key_of(cfg):
        """What needs a new scene when it changes; everything else is tuned in place."""
        return cfg["spec"], cfg["color"], cfg["flip"]

    def tune(self, cfg, lamps):
        self.brightness, self.speed = cfg["brightness"] / 100, cfg["speed"]
        self.saturation, self.hue = cfg["saturation"] / 100, cfg["hue"] / 360
        # keys pinned to a colour sit on top of everything, except when the lights are off
        self.pins = {} if cfg["spec"] == "off" else parse_pins(cfg["keys"], lamps)
        if self.audio:
            self.audio.sensitivity = cfg["sensitivity"]

    def press(self, i, now):
        self.base.on_key(i, self.clock)
        for layer in self.layers:
            layer.on_key(i, now)

    def animated(self, now):
        return self.base.animated or any(layer.active(now) for layer in self.layers)

    def colors(self, now, dt, dim=1.0):
        """The next frame as linear colours, brightness applied."""
        self.clock += dt * self.speed
        colors = list(self.base.render(self.clock))
        for layer in self.layers:
            layer.apply(colors, now)
        colors = grade(colors, self.saturation, self.hue)
        for i, c in self.pins.items():
            colors[i] = c
        k = self.brightness * dim
        return [scale(c, k) for c in colors]

    def close(self):
        if self.audio:
            self.audio.close()


# ---------------------------------------------------------------- daemon

def publish(leds):
    """Mirror the frame for lumikey-gui (tmpfs, so this is cheap); empty means built-in effects."""
    with open(FRAME_FILE + ".tmp", "wb") as f:
        f.write(bytes(c for rgb in leds for c in rgb))
    os.replace(FRAME_FILE + ".tmp", FRAME_FILE)


def save_layout(lamps):
    os.makedirs(os.path.dirname(LAYOUT_CACHE), exist_ok=True)
    with open(LAYOUT_CACHE + ".tmp", "w") as f:
        json.dump([[round(l.x, 2), round(l.y, 2), l.usage] for l in lamps], f)
    os.replace(LAYOUT_CACHE + ".tmp", LAYOUT_CACHE)


def run(kb, prefer=""):
    cfg = load_config()
    others = cfg["all_keyboards"]
    keys = KeyInput(kb.lamps, others, kb.sysfs)
    if keys.denied:
        log("no access to key events, so typing effects won't respond; run: lumikey setup")
    if keys.others_denied:
        log("no access to other keyboards, so they won't light this one; run: lumikey setup --all-keyboards")
    mtime, own_mtime, scene, prev = -1, None, None, None
    fading = None  # (previous scene, start, seconds) while crossfading into the new one
    try:
        last_tick = last_send = next_frame = keys_checked = 0.0
        last_input = last_change = time.monotonic()
        while True:
            pressed = keys.wait(clamp(next_frame - time.monotonic(), 0.0, 0.5))
            now = time.monotonic()
            if (keys.denied or keys.others_denied) and now - keys_checked > 10:  # access granted later (udev rule)
                was = keys.denied, keys.others_denied
                keys.close()
                keys, keys_checked = KeyInput(kb.lamps, others, kb.sysfs), now
                if was[0] and not keys.denied:
                    log("key events available; typing effects are live")
                if was[1] and not keys.others_denied:
                    log("other keyboards available; they light this one too")
            if config_mtime() != mtime:
                mtime = config_mtime()
                cfg = load_config()
                if mtime != own_mtime:
                    last_input = now  # changing a setting counts as being at the keyboard
                if cfg["device"] != prefer:
                    return  # another device was picked: reconnect
                if cfg["all_keyboards"] != others:
                    others = cfg["all_keyboards"]
                    keys.close()
                    keys = KeyInput(kb.lamps, others, kb.sysfs)
                if scene and scene.key == Scene.key_of(cfg):
                    scene.tune(cfg, kb.lamps)  # keep the animation running
                    prev, next_frame = None, now
                else:
                    old = scene
                    old_audio = scene.audio if scene else None
                    try:
                        scene = Scene(cfg, kb.lamps, old_audio)
                    except ValueError as e:
                        log(f"bad config: {e}")
                        scene = Scene({**cfg, **DEFAULTS}, kb.lamps)
                    prev, next_frame, last_change = None, now, now
                    if old_audio and scene.audio is not old_audio:
                        old_audio.close()  # the new scene doesn't listen to music
                    fade = clamp(float(cfg["fade"]), 0, 5)
                    fading = (old, now, fade) if old and not old.auto and not scene.auto and fade else None
                    if scene.auto:
                        kb.autonomous()
                        publish([])
            if scene.auto:
                next_frame = now + 0.5
                continue
            if pressed:
                last_input = now
            for i in pressed:
                scene.press(i, now)
            idle = cfg["idle"] * 60
            awake = clamp(1 - (now - last_input - idle) / 2.5) if idle else 1.0  # fades out over 2.5 s
            if cfg["cycle"] and cfg["spec"] != "off" and awake == 1 and now - last_change > cfg["cycle"] * 60:
                last_change = now
                save_config(shuffled(cfg, cfg["cycle_pool"]))  # picked up on the next pass
                own_mtime = config_mtime()
            if pressed and now - last_send >= kb.frame:
                next_frame = now
            if now < next_frame:
                continue
            if not awake and prev and not any(map(any, prev)):
                next_frame = now + 1.0  # asleep and already dark: nothing to draw until a key wakes it
                continue
            dt = now - last_tick if last_tick else 0.0
            last_tick = now
            colors = scene.colors(now, dt, awake)
            if fading:
                old, start, length = fading
                a = smoothstep(0, 1, (now - start) / length)
                if a >= 1:
                    fading = None
                else:
                    colors = [mix(o, c, a) for o, c in zip(old.colors(now, dt, awake), colors)]
            leds = [to_led(c, 1.0) for c in colors]
            if leds != prev or now - last_send > 5:
                kb.send(leds)
                publish(leds)
                prev, last_send = leds, now
            moving = scene.animated(now) or fading or 0 < awake < 1
            next_frame = now + (kb.frame if moving else 1.0)
    finally:
        keys.close()
        if scene:
            scene.close()
        try:
            os.remove(FRAME_FILE)
        except OSError:
            pass


def daemon():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    last_error = None
    while True:
        prefer = load_config()["device"]
        try:
            kb = Keyboard(prefer)
        except (LookupError, OSError) as e:
            if str(e) != last_error:
                log(f"waiting for a keyboard: {e}")
                last_error = str(e)
            time.sleep(3)
            continue
        last_error = None
        log(f"connected: {kb.name} ({kb.dev.ids}, {kb.path}), {len(kb.lamps)} lamps")
        save_layout(kb.lamps)
        try:
            run(kb, prefer)
        except OSError as e:
            log(f"keyboard lost ({e})")
        finally:
            try:
                kb.autonomous()  # on exit, leave the keyboard on its own effects
            except OSError:
                pass
            kb.close()
        time.sleep(1)


# ---------------------------------------------------------------- setup

def die(msg):
    sys.exit(f"lumikey: {msg}")


def installed_ids():
    """vid:pid of the devices the installed udev rule already covers."""
    try:
        with open(UDEV_RULE) as f:
            text = f.read()
    except OSError:
        return set()
    return {f"{v}:{p}".lower() for v, p in
            (m.split(":") for m in set(re.findall(r'KERNELS=="\*:([0-9A-F]{4}:[0-9A-F]{4})\.\*"', text)))}


def udev_rules(ids, all_keyboards):
    lines = ["# Lumikey: let the logged-in user light these Dynamic Lighting (HID LampArray) devices",
             "# and read their key presses for typing effects. Written by `lumikey setup`.",
             "# 70- so uaccess applies before 73-seat-late."]
    for vidpid in sorted(ids):
        v, p = vidpid.upper().split(":")
        lines += [f'SUBSYSTEM=="hidraw", KERNELS=="*:{v}:{p}.*", TAG+="uaccess"',
                  f'SUBSYSTEM=="input", KERNEL=="event*", KERNELS=="*:{v}:{p}.*", TAG+="uaccess"']
    if all_keyboards:
        lines += ["# every keyboard's key presses, so typing on any keyboard (e.g. a laptop's) lights the effects",
                  'SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", TAG+="uaccess"']
    return "\n".join(lines) + "\n"


def install_user_files():
    """The user service, launcher entry and icon, unless a package already put them in /usr."""
    data = importlib.resources.files("lumikey") / "data"
    exe = shutil.which("lumikey")
    command = f"{exe} daemon" if exe else f"{sys.executable} -m lumikey daemon"
    gui = shutil.which("lumikey-gui") or f"{sys.executable} -m lumikey.gui"
    files = []
    if not os.path.exists(f"/usr/lib/systemd/user/{SERVICE}.service"):
        unit = (data / "lumikey.service").read_text().replace("ExecStart=/usr/bin/lumikey daemon",
                                                               f"ExecStart={command}")
        files.append((os.path.join(CONFIG_HOME, "systemd", "user", f"{SERVICE}.service"), unit))
    if not os.path.exists(f"/usr/share/applications/{APP_ID}.desktop"):
        desktop = (data / f"{APP_ID}.desktop").read_text().replace("Exec=lumikey-gui", f"Exec={gui}")
        files.append((os.path.join(DATA_HOME, "applications", f"{APP_ID}.desktop"), desktop))
        files.append((os.path.join(DATA_HOME, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg"),
                      (data / f"{APP_ID}.svg").read_text()))
    for path, text in files:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    if files:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return [p for p, _ in files]


def setup(args):
    """Install the udev rule (via pkexec, or print the sudo command) and the user service."""
    all_keyboards = "--all-keyboards" in args
    found = [c for c in lamparray.find() if not c.quirk]
    ids = installed_ids() | {c.ids for c in found if c.ids != "?"}
    for path in install_user_files():
        print(f"installed {path}")
    if not ids:
        print("No Dynamic Lighting keyboard is plugged in. Plug it in and run `lumikey setup` again.\n"
              "Not sure if yours has Dynamic Lighting? `lumikey devices` lists what Lumikey can see.")
        return
    for c in found:
        print(f"found {c.name} ({c.ids})")
    rules = udev_rules(ids, all_keyboards)
    try:
        with open(UDEV_RULE) as f:
            current = f.read()
    except OSError:
        current = ""
    if current == rules:
        print(f"{UDEV_RULE} is already up to date")
    else:
        tmp = os.path.join(CACHE_HOME, "lumikey", "70-lumikey.rules")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w") as f:
            f.write(rules)
        script = (f"install -m644 '{tmp}' {UDEV_RULE} && udevadm control --reload"
                  " && udevadm trigger --action=change --subsystem-match=hidraw --subsystem-match=input")
        if "--print" in args or not shutil.which("pkexec"):
            print(f"\nRun this once to allow access (it installs {UDEV_RULE}):\n  sudo sh -c \"{script}\"")
            return
        print(f"\nInstalling {UDEV_RULE} (your password is needed once)...")
        if subprocess.run(["pkexec", "sh", "-c", script]).returncode != 0:
            print(f"That didn't work. Run this in a terminal instead:\n  sudo sh -c \"{script}\"")
            return
        time.sleep(1)  # let udev apply the new permissions
    subprocess.run(["systemctl", "--user", "enable", "--now", "--quiet", SERVICE], check=False)
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=False)
    print("\nAll set. Try:  lumikey galaxy   or open the control panel:  lumikey-gui")


def access_hint(cfg):
    found = [c for c in lamparray.find() if not c.quirk or cfg["device"] in (c.ids, c.path)]
    if not found:
        return "no Dynamic Lighting keyboard found (is it plugged in? see: lumikey devices)"
    if not any(os.access(c.path, os.R_OK | os.W_OK) for c in found):
        return f"no permission to use {found[0].name}; run once:  lumikey setup"
    if cfg["spec"] != "auto":
        base_cls, _, layers = parse_spec(cfg["spec"], cfg["color"])
        if any(c.needs_keys for c in (base_cls, *(cls for cls, _ in layers))):
            probe = KeyInput([], False, found[0].sysfs)
            probe.close()
            if probe.denied:
                return "typing effects can't see key presses yet; run once:  lumikey setup"
    return None


def service_state():
    return subprocess.run(["systemctl", "--user", "is-active", SERVICE], capture_output=True,
                          text=True).stdout.strip() or "unknown"


def ensure_daemon():
    if service_state() != "active":
        if subprocess.run(["systemctl", "--user", "cat", SERVICE], capture_output=True).returncode != 0:
            install_user_files()
        subprocess.run(["systemctl", "--user", "enable", "--now", "--quiet", SERVICE], check=False)


def show_devices(cfg):
    found = lamparray.find()
    if not found:
        return print("No Dynamic Lighting (HID LampArray) devices found.\n"
                     "Your keyboard may use a vendor protocol instead; please open an issue with `lumikey probe`.")
    for c in found:
        detail = f"skipped: {c.quirk}" if c.quirk else "no permission (run: lumikey setup)"
        if not c.quirk or cfg["device"] in (c.ids, c.path):
            try:
                dev = lamparray.LampArray(c)
                detail = f"{dev.kind}, {dev.count} lamps"
                dev.close()
            except PermissionError:
                pass
            except (OSError, LookupError, ValueError) as e:
                detail = f"error: {e}"
        mark = "*" if cfg["device"] in (c.ids, c.path) else " "
        print(f"{mark} {c.ids}  {c.path:13} {c.name}  ({detail})")
    if not cfg["device"]:
        print("\nLumikey lights the first working keyboard; pick another with: lumikey device <vid:pid>")


def probe():
    """Everything needed to add or debug support for a device, ready to paste into an issue."""
    import platform
    print(f"lumikey {__version__}, Python {platform.python_version()}, kernel {platform.release()}\n")
    nodes = sorted(glob.glob("/sys/class/hidraw/hidraw*"))
    for node in nodes:
        try:
            with open(f"{node}/device/uevent") as f:
                name = next((ln.split("=", 1)[1].strip() for ln in f if ln.startswith("HID_NAME=")), "?")
            with open(f"{node}/device/report_descriptor", "rb") as f:
                desc = f.read()
        except OSError:
            continue
        ids = lamparray.hid_ids(f"{node}/device")
        reports = lamparray.parse_descriptor(desc)
        print(f"## /dev/{os.path.basename(node)}: {name} ({ids[1] if ids else '?'}, bus {ids[0] if ids else '?'})"
              f"{'  LampArray' if reports else ''}")
        if not reports:
            continue
        print("descriptor:", desc.hex())
        for usage, r in reports.items():
            print(f"  report 0x{usage:02x}: id {r.id}, {r.size} bytes, {len(r.fields)} fields")
        try:
            dev = lamparray.LampArray(lamparray.Candidate(f"/dev/{os.path.basename(node)}", f"{node}/device",
                                                          name, ids[1] if ids else "?", None))
        except Exception as e:  # noqa: BLE001 - report anything
            print(f"  could not read lamps: {e}\n")
            continue
        print(f"  {dev.kind}, {dev.count} lamps, size {dev.size_mm[0]:.0f}x{dev.size_mm[1]:.0f} mm,"
              f" min update {dev.min_interval * 1000:.1f} ms, levels {sorted(set(dev.levels))}")
        print("  lamps (x mm, y mm, key):", " ".join(f"{l.x:.0f},{l.y:.0f},{l.usage}" for l in dev.lamps))
        dev.close()
        print()


def health_checks(cfg=None):
    """[(ok, what, fix)] for each piece Lumikey needs; fix is a command, or '' when nothing can be done here."""
    cfg = cfg or load_config()
    out = []
    found = lamparray.find()
    usable = [c for c in found if not c.quirk or cfg["device"] in (c.ids, c.path)]
    if usable:
        out.append((True, "Keyboard: " + ", ".join(f"{c.name} ({c.ids})" for c in usable), ""))
        c = usable[0]
        if os.access(c.path, os.R_OK | os.W_OK):
            out.append((True, "Permission to light it", ""))
        else:
            out.append((False, f"No permission to light it ({c.path})", "lumikey setup"))
        probe_keys = KeyInput([], False, c.sysfs)
        probe_keys.close()
        out.append((not probe_keys.denied, "Permission to see its key presses (typing effects)"
                    if not probe_keys.denied else "Typing effects can't see key presses", "lumikey setup"))
    else:
        out.append((False, "No Dynamic Lighting keyboard found" + (f" (skipped: {found[0].quirk})" if found else ""),
                    "plug it in; if it is, it may need a vendor protocol: open an issue with `lumikey probe`"))
    state = service_state()
    out.append((state == "active", "Background service is running" if state == "active"
                else f"Background service is {state}", f"systemctl --user enable --now {SERVICE}"))
    out.append((shutil.which("parec") is not None, "Music effects: parec found" if shutil.which("parec")
                else "Music effects: parec missing", "install pulseaudio-utils / libpulse (works with PipeWire)"))
    try:
        import numpy  # noqa: F401
        out.append((True, "Music effects: numpy found", ""))
    except ImportError:
        out.append((False, "Music effects: numpy missing", "install python-numpy"))
    return out


def doctor():
    """Check each piece and say how to fix what's missing."""
    checks = health_checks()
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        checks.append((True, "Control panel: GTK 4 + libadwaita found", ""))
    except (ImportError, ValueError):
        checks.append((False, "Control panel: GTK 4 / libadwaita / PyGObject missing",
                       "install python-gobject, gtk4 and libadwaita"))
    for ok, what, fix in checks:
        print(f"{'✓' if ok else '✗'} {what}" + (f"\n    fix: {fix}" if fix and not ok else ""))
    problems = sum(not ok for ok, _, _ in checks)
    print("\nEverything looks good." if not problems else f"\n{problems} thing(s) to fix.")


def show_status():
    cfg = load_config()
    color = f" {cfg['color']}" if cfg["color"] else ""
    state = service_state()
    extras = [f"{len(cfg['keys'])} pinned keys" if cfg["keys"] else "",
              f"saturation {cfg['saturation']}%" if cfg["saturation"] != 100 else "",
              f"hue +{cfg['hue']}°" if cfg["hue"] else "", f"flip {cfg['flip']}" if cfg["flip"] else "",
              f"idle {cfg['idle']} min" if cfg["idle"] else "",
              f"cycle {cfg['cycle']} min ({cfg['cycle_pool']})" if cfg["cycle"] else ""]
    extras = "".join(f"  {e}" for e in extras if e)
    print(f"{cfg['spec']}{color}  brightness {cfg['brightness']}  speed {cfg['speed']}{extras}  (service {state})")


def key_name(usage):
    return KEYS.get(int(usage), (f"#{usage}",))[0]


def show_keys(cfg):
    if not cfg["keys"]:
        return print("no pinned keys (pin some with: lumikey key wasd pink)")
    by_color = {}
    for usage, color in cfg["keys"].items():
        by_color.setdefault(color, []).append(key_name(usage))
    for color, names in by_color.items():
        print(f"{color:10} {','.join(names)}")
    if cfg["spec"] in ("auto", "off"):
        print(f"(not showing: pinned keys need a lumikey effect, and the lights are '{cfg['spec']}')")


def known_usages():
    """Every key the keyboard has, from the daemon's layout cache; else every named key."""
    try:
        with open(LAYOUT_CACHE) as f:
            return [u for _, _, u in json.load(f) if u]
    except (OSError, ValueError):
        return list(KEYS)


def print_list():
    for cat, (glyph, title, blurb) in CATEGORIES.items():
        print(f"{glyph} {title} ({blurb}):")
        for name, (cls, desc) in EFFECTS.items():
            if cls.category == cat:
                print(f"  {name:13} {desc}")
        print()
    print("Layers (stack on any effect with +, e.g. galaxy+ripple, nebula+sparks+beat):")
    for name, (_, desc) in LAYERS.items():
        print(f"  {name:13} {desc}")
    print(f"\nColours: {' '.join(COLORS)}, or hex like ff8800")
    print(f"\nKey groups for lumikey key: {' '.join(KEY_GROUPS)}")
    print(f"Keys: {' '.join(name for name, _ in KEYS.values())}")


def main():
    args = sys.argv[1:]
    if not args:
        return show_status()
    cmd = args[0].lower()
    if cmd in ("-h", "--help", "help"):
        return print(__doc__.strip())
    if cmd == "daemon":
        return daemon()
    if cmd == "list":
        return print_list()
    if cmd == "setup":
        return setup(args[1:])
    if cmd == "doctor":
        return doctor()
    if cmd == "probe":
        return probe()
    if cmd in ("-v", "--version", "version"):
        return print(f"lumikey {__version__}")
    cfg = load_config()
    if cmd == "devices":
        return show_devices(cfg)
    numeric = {"brightness": (0, 100, round), "speed": (0.25, 4, float), "saturation": (0, 200, round),
               "hue": (0, 359, round), "fade": (0, 5, float), "idle": (0, 600, float), "sensitivity": (0.3, 3, float)}
    if cmd == "looks":
        for name, look in all_looks(cfg).items():
            tag = "" if name in cfg["looks"] else "  (starter)"
            print(f"{name:14} {look['spec']}{' ' + look['color'] if look.get('color') else ''}{tag}")
        return
    if cmd == "fav" and len(args) == 1:
        return print(" ".join(cfg["favorites"]) or "no favourites yet (star one with: lumikey fav galaxy)")
    if cmd in ("save", "load", "forget"):
        if len(args) < 2:
            die(f"usage: lumikey {cmd} <name>")
        name = " ".join(args[1:])
        looks = all_looks(cfg)
        match = next((n for n in looks if n.lower() == name.lower()), None)
        if cmd == "save":
            cfg["looks"] = {**cfg["looks"], name: snapshot_look(cfg)}
        elif match is None:
            die(f"no look called '{name}' (see lumikey looks)")
        elif cmd == "load":
            cfg = apply_look(cfg, looks[match])
        elif match not in cfg["looks"]:
            die(f"'{match}' is a starter look; it can't be deleted")
        else:
            cfg["looks"] = {n: v for n, v in cfg["looks"].items() if n != match}
    elif cmd == "fav":
        name = args[1].lower()
        if name not in EFFECTS:
            die(f"unknown effect '{name}' (see lumikey list)")
        favs = cfg["favorites"]
        cfg["favorites"] = [n for n in favs if n != name] if name in favs else [*favs, name]
        print(f"{'★ starred' if name in cfg['favorites'] else '☆ unstarred'} {name}")
    elif cmd in ("next", "prev", "random"):
        pool = args[1].lower() if len(args) > 1 else "all"
        if pool not in ("all", "favorites", *CATEGORIES):
            die(f"unknown category '{pool}' (one of: favorites {' '.join(CATEGORIES)})")
        cfg = shuffled(cfg, pool, {"next": 1, "prev": -1, "random": 0}[cmd])
        print(cfg["spec"])
    elif cmd == "flip":
        if len(args) != 2 or args[1] not in ("none", "h", "v", "hv"):
            die("usage: lumikey flip none|h|v|hv")
        cfg["flip"] = "" if args[1] == "none" else args[1]
    elif cmd == "cycle":
        if len(args) not in (2, 3):
            die("usage: lumikey cycle <minutes> [favorites|all|category|<category>]")
        try:
            cfg["cycle"] = clamp(float(args[1]), 0, 1440)
        except ValueError:
            die(f"'{args[1]}' isn't a number")
        if len(args) == 3:
            if args[2] not in ("favorites", "all", "category", *CATEGORIES):
                die(f"unknown pool '{args[2]}'")
            cfg["cycle_pool"] = args[2]
    elif cmd == "keys":
        if len(args) == 1:
            return show_keys(cfg)
        if args[1:] != ["clear"]:
            die("usage: lumikey keys [clear]")
        cfg["keys"] = {}
    elif cmd == "key":
        if len(args) != 3:
            die("usage: lumikey key <keys> <colour|clear>, e.g. lumikey key wasd pink")
        try:
            usages = parse_keys(args[1], known_usages())
        except ValueError as e:
            die(f"{e} (see lumikey list)")
        color = args[2].lower()
        if color in ("clear", "none"):
            for u in usages:
                cfg["keys"].pop(str(u), None)
        else:
            try:
                parse_color(color)
            except ValueError:
                die(f"unknown colour '{color}' (see lumikey list)")
            cfg["keys"].update({str(u): color.lstrip("#") for u in usages})
        if cfg["spec"] in ("auto", "off"):
            print(f"lumikey: saved, but pinned keys only show over a lumikey effect (lights are '{cfg['spec']}')",
                  file=sys.stderr)
    elif cmd == "device":
        if len(args) != 2:
            die("usage: lumikey device <vid:pid|auto> (see lumikey devices)")
        cfg["device"] = "" if args[1] == "auto" else args[1].lower()
    elif cmd == "all-keyboards":
        if len(args) != 2 or args[1] not in ("on", "off"):
            die("usage: lumikey all-keyboards on|off")
        cfg["all_keyboards"] = args[1] == "on"
    elif cmd in numeric:
        if len(args) != 2:
            die(f"usage: lumikey {cmd} <value>")
        lo, hi, kind = numeric[cmd]
        try:
            cfg[cmd] = kind(clamp(float(args[1]), lo, hi))
        except ValueError:
            die(f"'{args[1]}' isn't a number")
    elif len(args) > 2:
        die("too many arguments (see lumikey --help)")
    else:
        color = args[1] if len(args) > 1 else None
        if cmd != "auto":
            try:
                parse_spec(cmd, color)
            except ValueError as e:
                die(f"{e} (see lumikey list)")
        cfg["spec"], cfg["color"] = cmd, color
    save_config(cfg)
    ensure_daemon()
    hint = access_hint(cfg)
    if hint:
        print(f"lumikey: {hint}", file=sys.stderr)


if __name__ == "__main__":
    main()
