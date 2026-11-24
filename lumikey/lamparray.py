# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""HID LampArray ("Dynamic Lighting") devices over Linux hidraw.

LampArray is the open HID standard behind Windows Dynamic Lighting (HID Usage Tables,
page 0x59 "Lighting And Illumination"). A device describes its own lighting reports in
its HID report descriptor, so instead of hard-coding report numbers and layouts per
model, Lumikey parses the descriptor and builds the reports from it. Any device that
follows the standard works without model-specific code.
"""
import fcntl
import glob
import os
import re
import struct
from collections import namedtuple

PAGE = 0x59  # Lighting And Illumination
LAMP_ARRAY = 0x01
# report collections
ATTRIBUTES, REQUEST, RESPONSE, MULTI_UPDATE, RANGE_UPDATE, CONTROL = 0x02, 0x20, 0x22, 0x50, 0x60, 0x70
# fields
LAMP_COUNT, BOX_W, BOX_H, BOX_D, KIND, MIN_INTERVAL = 0x03, 0x04, 0x05, 0x06, 0x07, 0x08
LAMP_ID, POS_X, POS_Y, POS_Z, PURPOSES, LATENCY = 0x21, 0x23, 0x24, 0x25, 0x26, 0x27
RED_LEVELS, GREEN_LEVELS, BLUE_LEVELS, INTENSITY_LEVELS, PROGRAMMABLE, INPUT_BINDING = range(0x28, 0x2E)
RED, GREEN, BLUE, INTENSITY, UPDATE_FLAGS = 0x51, 0x52, 0x53, 0x54, 0x55
RANGE_START, RANGE_END, AUTONOMOUS = 0x61, 0x62, 0x71

KINDS = {1: "keyboard", 2: "mouse", 3: "game controller", 4: "peripheral", 5: "scene", 6: "notification",
         7: "chassis", 8: "wearable", 9: "furniture", 10: "art"}

# Devices that expose a LampArray interface which doesn't work. They're skipped unless picked
# by hand with `lumikey device <vid:pid>`.
QUIRKS = {
    "048d:c195": "ITE 8258 (Legion laptop keyboard): its LampArray interface ignores writes",
}

PITCH = 19.05  # a key's width in mm

Field = namedtuple("Field", "usage offset size")  # bit offset and size, not counting the report ID byte
Report = namedtuple("Report", "id size fields")  # size in bytes, not counting the report ID byte
Lamp = namedtuple("Lamp", "x y u v usage")  # x, y in mm from the top-left lamp; u, v in 0..1; usage = HID key


# ---------------------------------------------------------------- descriptor

def parse_descriptor(desc):
    """The feature reports of the first LampArray in a HID report descriptor, as
    {collection usage: Report}, e.g. {ATTRIBUTES: Report(1, 22, [...]), ...}. Empty if none."""
    glob_state = {"page": 0, "size": 0, "count": 0, "id": 0}
    stack, usages, usage_min, collections = [], [], None, []
    offsets, reports, in_lamp_array, done = {}, {}, False, False
    i = 0
    while i < len(desc) and not done:
        prefix = desc[i]
        if prefix == 0xFE:  # long item
            i += 3 + (desc[i + 1] if i + 1 < len(desc) else 0)
            continue
        n = (0, 1, 2, 4)[prefix & 3]
        data = desc[i + 1:i + 1 + n]
        value = int.from_bytes(data, "little")
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        i += 1 + n

        def full_usage(v):
            return v if n == 4 else (glob_state["page"] << 16) | v

        if kind == 1:  # global
            if tag == 0:
                glob_state["page"] = value
            elif tag == 7:
                glob_state["size"] = value
            elif tag == 8:
                glob_state["id"] = value
            elif tag == 9:
                glob_state["count"] = value
            elif tag == 10:
                stack.append(dict(glob_state))
            elif tag == 11 and stack:
                glob_state = stack.pop()
        elif kind == 2:  # local
            if tag == 0:
                usages.append(full_usage(value))
            elif tag == 1:
                usage_min = full_usage(value)
            elif tag == 2 and usage_min is not None:
                usages.extend(range(usage_min, full_usage(value) + 1))
                usage_min = None
        elif kind == 0:  # main
            if tag == 10:  # collection
                usage = usages[0] if usages else 0
                collections.append(usage)
                if usage == (PAGE << 16) | LAMP_ARRAY:
                    in_lamp_array = True
            elif tag == 12:  # end collection
                if collections and collections.pop() == (PAGE << 16) | LAMP_ARRAY:
                    done = bool(reports)  # only the first LampArray
                    in_lamp_array = False
            elif tag in (8, 9, 11):  # input, output, feature
                rid, size, count = glob_state["id"], glob_state["size"], glob_state["count"]
                key = (tag, rid)
                start = offsets.get(key, 0)
                offsets[key] = start + size * count
                if tag == 11 and in_lamp_array and usages:  # no usages = padding
                    group = next((c & 0xFFFF for c in reversed(collections) if c >> 16 == PAGE
                                  and c & 0xFFFF != LAMP_ARRAY), None)
                    if group is not None:
                        old = reports.get(group)
                        fields = list(old.fields) if old else []
                        for f in range(count):
                            u = usages[min(f, len(usages) - 1)]
                            if u >> 16 == PAGE:
                                fields.append(Field(u & 0xFFFF, start + f * size, size))
                        reports[group] = Report(rid, 0, fields)
            usages, usage_min = [], None
    return {g: r._replace(size=(offsets.get((11, r.id), 0) + 7) // 8) for g, r in reports.items()}


def unpack(report, data):
    """{usage: value} for a report's fields; repeated usages give a list."""
    number = int.from_bytes(data, "little")
    out = {}
    for f in report.fields:
        v = (number >> f.offset) & ((1 << f.size) - 1)
        if f.usage in out:
            out[f.usage] = (out[f.usage] if isinstance(out[f.usage], list) else [out[f.usage]]) + [v]
        else:
            out[f.usage] = v
    return out


def pack(report, values):
    """Bytes for a report (without the ID) from {usage: value or [values for each repeat]}."""
    number, seen = 0, {}
    for f in report.fields:
        v = values.get(f.usage, 0)
        if isinstance(v, (list, tuple)):
            k = seen.get(f.usage, 0)
            seen[f.usage] = k + 1
            v = v[k] if k < len(v) else 0
        number |= (int(v) & ((1 << f.size) - 1)) << f.offset
    return number.to_bytes(report.size, "little")


# ---------------------------------------------------------------- hidraw

def _ioc(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


def get_feature(fd, report):
    buf = bytearray(1 + report.size)
    buf[0] = report.id
    fcntl.ioctl(fd, _ioc(0x07, len(buf)), buf)
    return bytes(buf[1:])


def set_feature(fd, report, values):
    buf = bytearray([report.id]) + pack(report, values)
    fcntl.ioctl(fd, _ioc(0x06, len(buf)), buf)


# ---------------------------------------------------------------- discovery

HID_DIR = re.compile(r"^([0-9A-F]{4}):([0-9A-F]{4}):([0-9A-F]{4})\.[0-9A-F]+$")


def hid_ids(sysdev):
    """(bus, 'vid:pid') of the HID device above a sysfs device, or None."""
    path = os.path.realpath(sysdev)
    while path != "/":
        m = HID_DIR.match(os.path.basename(path))
        if m:
            return m.group(1), f"{m.group(2)}:{m.group(3)}".lower()
        path = os.path.dirname(path)
    return None


def physical(sysdev):
    """A path identifying the physical device a HID / input node belongs to (all its interfaces
    share it), so we can tell which keyboard's key presses belong to the lit device."""
    path = os.path.realpath(sysdev)
    p = path
    while p != "/":
        if os.path.exists(f"{p}/idVendor"):  # USB device
            return p
        p = os.path.dirname(p)
    p = path
    while p != "/":
        if HID_DIR.match(os.path.basename(p)):
            return os.path.dirname(p)  # e.g. the Bluetooth connection or I2C device
        p = os.path.dirname(p)
    return None


Candidate = namedtuple("Candidate", "path sysfs name ids quirk")


def find() -> list:
    """Every hidraw node with a LampArray, best guesses first; nothing is opened."""
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        try:
            with open(f"{node}/device/report_descriptor", "rb") as f:
                if not parse_descriptor(f.read()):
                    continue
            with open(f"{node}/device/uevent") as f:
                name = next((line.split("=", 1)[1].strip() for line in f if line.startswith("HID_NAME=")), "?")
        except OSError:
            continue
        words = name.split()
        if len(words) > 1 and words[0].lower() == words[1].lower():
            name = " ".join(words[1:])  # "Lenovo Lenovo Legion ..." -> "Lenovo Legion ..."
        ids = hid_ids(f"{node}/device")
        vidpid = ids[1] if ids else "?"
        found.append(Candidate("/dev/" + os.path.basename(node), f"{node}/device", name, vidpid,
                               QUIRKS.get(vidpid)))
    return sorted(found, key=lambda c: c.quirk is not None)


# ---------------------------------------------------------------- device

class LampArray:
    """One LampArray device: its lamps (positions, key bindings, colour depth) and how to light them."""

    def __init__(self, candidate):
        self.candidate = candidate
        self.path, self.name, self.ids = candidate.path, candidate.name, candidate.ids
        with open(f"{candidate.sysfs}/report_descriptor", "rb") as f:
            self.reports = parse_descriptor(f.read())
        missing = {ATTRIBUTES, REQUEST, RESPONSE, MULTI_UPDATE} - set(self.reports)
        if missing:
            raise LookupError(f"{self.name}: LampArray without reports {sorted(missing)}")
        self.fd = os.open(self.path, os.O_RDWR)
        try:
            self._read_layout()
        except Exception:
            os.close(self.fd)
            raise
        self.host = False

    def _read_layout(self):
        attrs = unpack(self.reports[ATTRIBUTES], get_feature(self.fd, self.reports[ATTRIBUTES]))
        self.count = attrs.get(LAMP_COUNT, 0)
        self.kind = KINDS.get(attrs.get(KIND), "unknown")
        self.size_mm = (attrs.get(BOX_W, 0) / 1000, attrs.get(BOX_H, 0) / 1000)
        self.min_interval = attrs.get(MIN_INTERVAL, 0) / 1e6  # seconds
        if not self.count:
            raise LookupError(f"{self.name}: reports no lamps")
        req, resp = self.reports[REQUEST], self.reports[RESPONSE]
        set_feature(self.fd, req, {LAMP_ID: 0})  # the device answers lamp 0, then auto-increments
        raw = []
        for n in range(self.count):
            a = unpack(resp, get_feature(self.fd, resp))
            if a.get(LAMP_ID) != n:  # no auto-increment: ask for each lamp
                set_feature(self.fd, req, {LAMP_ID: n})
                a = unpack(resp, get_feature(self.fd, resp))
            raw.append(a)
        self.levels = [tuple(max(1, a.get(u, 256)) for u in (RED_LEVELS, GREEN_LEVELS, BLUE_LEVELS, INTENSITY_LEVELS))
                       for a in raw]
        self.programmable = [bool(a.get(PROGRAMMABLE, 1)) for a in raw]
        points = []
        for a in raw:
            x, y = a.get(POS_X, 0) / 1000, a.get(POS_Y, 0) / 1000
            while any(abs(x - px) < 1 and abs(y - py) < 1 for px, py, _ in points):
                x += PITCH  # some firmware (e.g. the K510) reports two keys at one spot; move one right
            usage = a.get(INPUT_BINDING, 0)
            points.append((x, y, usage if 0 < usage < 0xFFFF else 0))
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        w = (max(p[0] for p in points) - x0) or 1
        h = (max(p[1] for p in points) - y0) or 1
        self.lamps = [Lamp(x - x0, y - y0, (x - x0) / w, (y - y0) / h, usage) for x, y, usage in points]

    def send(self, leds):
        """Light every lamp: leds is one (r, g, b) 0-255 per lamp, all shown together."""
        if not self.host:
            self.autonomous(False)
        report = self.reports[MULTI_UPDATE]
        slots = sum(1 for f in report.fields if f.usage == LAMP_ID)
        ids = [i for i in range(min(len(leds), self.count)) if self.programmable[i]]
        for start in range(0, len(ids), slots):
            chunk = ids[start:start + slots]
            values = {LAMP_COUNT: len(chunk), UPDATE_FLAGS: int(start + slots >= len(ids)), LAMP_ID: chunk,
                      RED: [], GREEN: [], BLUE: [], INTENSITY: []}
            for i in chunk:
                (r, g, b), (rl, gl, bl, il) = leds[i], self.levels[i]
                values[RED].append(r * (rl - 1) // 255)
                values[GREEN].append(g * (gl - 1) // 255)
                values[BLUE].append(b * (bl - 1) // 255)
                values[INTENSITY].append(il - 1)
            set_feature(self.fd, report, values)

    def autonomous(self, on=True):
        """on: hand the lights back to the device's own effects; off: the host draws them."""
        if CONTROL in self.reports:
            set_feature(self.fd, self.reports[CONTROL], {AUTONOMOUS: int(on)})
        self.host = not on

    def close(self):
        os.close(self.fd)


def open_device(prefer=""):
    """The device to light: `prefer` ('vid:pid' or a hidraw path) if given, else the first
    keyboard, else the first working LampArray. Raises LookupError if there's none."""
    candidates = find()
    if prefer:
        chosen = [c for c in candidates if prefer in (c.ids, c.path)]
        if not chosen:
            raise LookupError(f"{prefer} isn't plugged in (or has no LampArray)")
        return LampArray(chosen[0])
    errors, fallback = [], None
    for c in candidates:
        if c.quirk:
            continue
        try:
            dev = LampArray(c)
        except PermissionError:
            errors.append(f"no permission for {c.path} ({c.name}); run: lumikey setup")
            continue
        except (OSError, LookupError, ValueError, struct.error) as e:
            errors.append(f"{c.name}: {e}")
            continue
        if dev.kind == "keyboard":
            if fallback:
                fallback.close()
            return dev
        if fallback:
            dev.close()
        else:
            fallback = dev
    if fallback:
        return fallback
    if errors:
        raise LookupError("; ".join(errors))
    raise LookupError("no Dynamic Lighting (HID LampArray) device found")
