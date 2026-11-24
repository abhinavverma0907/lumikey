#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""Lumikey control panel.

  lumikey-gui    open the panel; run it again to close it (bind it to a key, e.g. SUPER+K)

Effects: click a card to put that effect on the keyboard. Search, filter by category,
star favourites and choose how many columns. Layers, colours, brightness, speed,
saturation and hue apply instantly; the big preview mirrors what the keyboard shows
right now. Cards are local previews: music cards dance to a made-up beat, typing cards
type by themselves. Per-key paints single keys a colour that stays on top of any effect.
Looks saves and recalls whole setups. Settings: transitions, direction, auto-cycle,
sleep when idle, music sensitivity. Ctrl+/ lists the keyboard shortcuts.
"""
import json
import math
import os
import random
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo  # noqa: E402

from . import core as k  # noqa: E402

APP_ID = k.APP_ID

CAT_COLORS = {"space": "#c4a5ff", "nature": "#86efac", "elements": "#fdba74", "neon": "#f0abfc", "mood": "#fda4af",
              "classic": "#e2e8f0", "games": "#bef264", "music": "#f9a8d4", "typing": "#67e8f9"}
TITLES = {"vu": "VU Meter", "dim": "Darkness", "solid": "Solid Colour", "blackhole": "Black Hole",
          "lightcycles": "Light Cycles"}
UTILITY = {"solid": ("classic", "every key in one colour; pick it below"),
           "dim": ("typing", "a near-black board, made for typing layers")}
# key widths (in keys) by HID usage, for drawing the 75% layout
WIDE = {42: 2, 43: 1.5, 49: 1.5, 57: 1.75, 40: 2.25, 225: 2.25, 229: 1.75, 224: 1.25, 226: 1.25, 227: 1.25,
        44: 6.25}
UNGAMMA = [(v / 255) ** (1 / k.GAMMA) for v in range(256)]  # LED value -> on-screen brightness
QUICK = [("wasd", "WASD"), ("esdf", "ESDF"), ("arrows", "Arrows"), ("homerow", "Home row"), ("numbers", "Numbers"),
         ("fkeys", "F-keys"), ("letters", "Letters"), ("mods", "Modifiers"), ("nav", "Nav keys"), ("all", "Every key")]
SLIDERS = {  # config key: title, min, max, step, value format
    "brightness": ("Brightness", 0, 100, 1, "{:.0f}%"),
    "speed": ("Speed", 0.25, 4, 0.05, "{:.2f}×"),
    "saturation": ("Saturation", 0, 200, 1, "{:.0f}%"),
    "hue": ("Hue shift", 0, 359, 1, "{:.0f}°"),
}
COLUMNS = [3, 4, 5, 6, 7, 8, 10]
PAGES = [("effects", "✦  Effects", "Effects that light the whole board"),
         ("keys", "◆  Per-key", "Pin single keys to a colour"),
         ("looks", "❖  Looks", "Save and recall whole setups"),
         ("settings", "⚙  Settings", "Transitions, direction, auto-cycle, sleep and more")]
SETTINGS = [  # section, [(config key, title, description, [(value, label)])]
    ("MOTION", [
        ("fade", "Transitions", "How one effect blends into the next when you switch",
         [(0, "Cut"), (0.3, "Quick"), (0.6, "Smooth"), (1.5, "Dreamy")]),
        ("flip", "Direction", "Turn effects around. Typing effects always stay under your fingers",
         [("", "Normal"), ("h", "Mirrored"), ("v", "Upside down"), ("hv", "Rotated")]),
    ]),
    ("AUTOMATION", [
        ("cycle", "Auto-cycle", "Move on to another effect every so often; your layers and colour stay",
         [(0, "Off"), (1, "1 min"), (5, "5 min"), (15, "15 min"), (60, "1 hour")]),
        ("cycle_pool", "Cycle through", "Where auto-cycle picks the next effect from (no favourites = everything)",
         [("favorites", "★  Favourites"), ("all", "Everything"), ("category", "Same category")]),
        ("idle", "Sleep when idle", "Fade the lights out when you haven't typed for a while; any key wakes them",
         [(0, "Never"), (1, "1 min"), (5, "5 min"), (15, "15 min"), (30, "30 min")]),
    ]),
    ("INPUT", [
        ("all_keyboards", "Other keyboards", "Typing on other keyboards (e.g. a laptop's) lights this one too, in typing effects",
         [(True, "On"), (False, "Off")]),
        ("sensitivity", "Music sensitivity", "How hard music effects react to what's playing",
         [(0.6, "Subtle"), (1.0, "Normal"), (1.6, "Punchy"), (2.4, "Wild")]),
    ]),
    ("THIS WINDOW", [
        ("previews", "Card previews", "Animate the gallery cards; Still saves a little CPU",
         [(True, "Animated"), (False, "Still")]),
    ]),
]
SHORTCUTS = [("Ctrl+F", "search effects"), ("Ctrl+R", "surprise me"), ("Ctrl+← / →", "previous / next effect"),
             ("Ctrl+D", "favourite the current effect"), ("Ctrl+B", "show / hide the side panel"),
             ("Ctrl+1…4", "Effects, Per-key, Looks, Settings"), ("Ctrl+S", "save the current look"),
             ("Ctrl+Z", "undo key painting"), ("Ctrl+/", "these shortcuts"), ("Esc", "clear search, or close")]

CSS = """
window.lumikey {
  background-color: #0a0911;
  background-image:
    radial-gradient(circle at 8% 0%, alpha(#7c3aed, 0.30), transparent 50%),
    radial-gradient(circle at 96% 4%, alpha(#db2777, 0.20), transparent 42%),
    radial-gradient(circle at 50% 100%, alpha(#0891b2, 0.14), transparent 55%);
  color: white;
}
window.lumikey headerbar { background: none; box-shadow: none; }
.eyebrow { font-size: 11px; font-weight: 800; letter-spacing: 2px; color: alpha(white, 0.5); }
.hero-title { font-size: 30px; font-weight: 800; letter-spacing: -0.5px; }
.hero-layers { font-size: 12.5px; font-weight: 800; color: #f0abfc; }
.hero-desc { font-size: 13px; color: alpha(white, 0.62); }
.live { font-size: 10px; font-weight: 800; letter-spacing: 2px; color: #fda4af;
        padding: 3px 9px; border-radius: 99px; background: alpha(#fb7185, 0.14); }
.live-dot { background: #fb7185; border-radius: 99px; min-width: 7px; min-height: 7px; }
.row-label { font-size: 12px; font-weight: 700; color: alpha(white, 0.72); }
.row-value { font-size: 12px; font-feature-settings: "tnum"; color: alpha(white, 0.6); }
.hint { font-size: 11px; color: alpha(white, 0.35); }
.check { font-size: 13px; padding: 3px 0; }
.check.ok { color: #86efac; }
.check.bad { color: #fda4af; font-weight: 700; }
.check-fix { font-family: monospace; font-size: 11px; color: alpha(white, 0.55); margin-left: 22px; }
.side { background: alpha(white, 0.035); border: 1px solid alpha(white, 0.06); border-radius: 22px; padding: 16px; }
.count { font-size: 12px; color: alpha(white, 0.4); }
scale trough { background-color: alpha(white, 0.10); min-height: 6px; border-radius: 99px; }
scale highlight { background-image: linear-gradient(to right, #8b5cf6, #ec4899); border-radius: 99px; }
scale slider { background: white; min-width: 18px; min-height: 18px; border-radius: 99px;
               box-shadow: 0 0 0 4px alpha(#ec4899, 0.22), 0 0 14px alpha(#ec4899, 0.6); }
scale.hue trough { background-image: linear-gradient(to right, #ff2050, #ffb000, #b0ff00, #00ff90, #00c8ff,
                                                     #4b4bff, #c020ff, #ff2050); }
scale.hue highlight { background-image: none; background-color: transparent; }
scale.saturation trough { background-image: linear-gradient(to right, #6b7280, #a855f7, #ec4899); }
scale.saturation highlight { background-image: none; background-color: transparent; }
.pill { border-radius: 99px; padding: 5px 13px; background: alpha(white, 0.06);
        border: 1px solid alpha(white, 0.08); font-weight: 700; font-size: 12px; transition: all 160ms ease; }
.pill:hover { background: alpha(white, 0.11); }
.pill:checked { background-image: linear-gradient(to right, #8b5cf6, #ec4899); border-color: transparent;
                color: white; box-shadow: 0 0 18px alpha(#ec4899, 0.45); }
.pill.small { padding: 2px 10px; font-size: 11px; }
.swatch { min-width: 24px; min-height: 24px; padding: 0; border-radius: 99px;
          border: 2px solid alpha(white, 0.14); transition: all 140ms ease; }
.swatch:hover { border-color: alpha(white, 0.6); }
.swatch.active { border-color: white; box-shadow: 0 0 0 2px alpha(white, 0.22), 0 0 14px alpha(white, 0.45); }
.sw-rainbow { background-image: linear-gradient(135deg, #ff2a6d, #ffb800, #3cff6a, #00d5ff, #8a2bff); }
.sw-custom { background: alpha(white, 0.08); color: white; }
.card { background: alpha(white, 0.04); border: 1px solid alpha(white, 0.07); border-radius: 18px;
        padding: 10px 10px 12px 10px; transition: all 180ms ease; }
.card:hover { background: alpha(white, 0.075); border-color: alpha(#c084fc, 0.45);
              box-shadow: 0 10px 28px alpha(black, 0.35); }
.card.selected { background: alpha(#a855f7, 0.13); border-color: alpha(#e879f9, 0.85);
                 box-shadow: 0 0 0 1px alpha(#e879f9, 0.55), 0 0 26px alpha(#a855f7, 0.5); }
.card-title { font-weight: 800; font-size: 14px; }
.card-desc { font-size: 11.5px; color: alpha(white, 0.55); }
.card-glyph { font-size: 13px; }
.star, .delete { min-width: 26px; min-height: 26px; padding: 0; border-radius: 99px; background: alpha(black, 0.5);
                 color: alpha(white, 0.85); opacity: 0; transition: all 160ms ease; }
.card:hover .star, .card:hover .delete, .star:checked { opacity: 1; }
.star:hover, .star:checked { color: #fde047; }
.delete:hover { background: #e11d48; color: white; }
.nav { min-width: 32px; min-height: 32px; padding: 0; border-radius: 99px; background: alpha(white, 0.06); }
.nav:hover { background: alpha(white, 0.14); }
.nav.starred { color: #fde047; }
.eyebrow.keys { color: #f9a8d4; }
.notice { font-size: 12px; color: #fde68a; padding: 7px 12px; border-radius: 12px; background: alpha(#f59e0b, 0.12); }
.sw-brush { background: alpha(white, 0.08); color: white; }
.sw-eraser { background: alpha(white, 0.03); border-style: dashed; color: alpha(white, 0.75); }
.page-switch .pill { padding: 5px 16px; }
.search { border-radius: 99px; min-height: 34px; background: alpha(white, 0.06); border: 1px solid alpha(white, 0.08); }
.columns > button { border-radius: 99px; background: alpha(white, 0.06); border: 1px solid alpha(white, 0.08);
                    font-weight: 700; font-size: 12px; min-height: 30px; }
.empty { font-size: 13px; color: alpha(white, 0.5); padding: 48px 24px; }
.entry-name { border-radius: 99px; min-height: 34px; padding: 0 14px; background: alpha(white, 0.06);
              border: 1px solid alpha(white, 0.08); }
.accent { background-image: linear-gradient(to right, #8b5cf6, #ec4899); color: white; font-weight: 800;
          border-radius: 99px; padding: 6px 18px; box-shadow: 0 0 18px alpha(#ec4899, 0.4); }
.accent:hover { box-shadow: 0 0 24px alpha(#ec4899, 0.65); }
.section { font-size: 11px; font-weight: 800; letter-spacing: 2px; color: #c4a5ff; margin-top: 8px; }
.setting { padding: 10px 2px; border-bottom: 1px solid alpha(white, 0.05); }
.setting-title { font-weight: 800; font-size: 14px; }
.setting-desc { font-size: 12px; color: alpha(white, 0.55); }
.kbd { font-family: monospace; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 7px;
       background: alpha(white, 0.09); border: 1px solid alpha(white, 0.14); border-bottom-width: 2px; }
.cat-favorites:checked { background-image: linear-gradient(to right, #f59e0b, #ec4899); }
"""


def extra_css():
    rules = [f".sw-{name} {{ background: rgb({r * 255:.0f}, {g * 255:.0f}, {b * 255:.0f}); }}"
             for name, (r, g, b) in k.COLORS.items()]
    for cat, color in CAT_COLORS.items():
        rules.append(f".eyebrow.{cat}, .card-glyph.{cat} {{ color: {color}; }}")
        rules.append(f".cat-{cat}:checked {{ background-image: linear-gradient(to right, alpha({color}, 0.55), #8b5cf6); }}")
    return "\n".join(rules)


def title_of(key):
    return TITLES.get(key, key.capitalize())


def group_of(cls):
    return cls.category


def describe(spec, color=None):
    """'nebula+sparks' -> 'Nebula + Sparks', for looks."""
    base, layers = k.split_spec(spec)
    if base is None:
        return "Built-in" if spec == "auto" else "Off"
    name = title_of(base) if base in k.EFFECTS or base == "dim" else f"Solid {base}"
    return "  +  ".join([name, *(n.capitalize() for n in layers)]) + (f"  ·  {color}" if color else "")


def parse_state(spec):
    """(card key, solid colour, layers) for a spec; card key is None for auto/off."""
    if spec in ("auto", "off"):
        return None, None, []
    base, layers = k.split_spec(spec)
    if base in k.EFFECTS or base == "dim":
        return base, None, layers
    if base == "off":
        return "dim", None, layers
    return "solid", base, layers


def load_lamps():
    """Key layout cached by the daemon; else read the keyboard; else a plain 16x6 grid."""
    try:
        with open(k.LAYOUT_CACHE) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        try:
            kb = k.Keyboard()
            kb.close()
            return kb.lamps
        except (LookupError, OSError):
            raw = [[c * k.PITCH, r * k.PITCH, 0] for r in range(6) for c in range(16)]
    w, h = max(x for x, _, _ in raw) or 1, max(y for _, y, _ in raw) or 1
    return [k.Lamp(x, y, x / w, y / h, usage) for x, y, usage in raw]


def key_rects(lamps):
    """(x, row, width) in keys per lamp: each row packed left to right with real key widths."""
    rows = {}
    for i, l in enumerate(lamps):
        rows.setdefault(round(l.y / k.PITCH), []).append(i)
    rects = [None] * len(lamps)
    for row, idx in rows.items():
        idx.sort(key=lambda i: lamps[i].x)
        x, packed = 0.0, []
        for i in idx:
            packed.append((i, x, WIDE.get(lamps[i].usage, 1.0)))
            x += packed[-1][2]
        if abs(x - 16) > 0.3:  # not the layout we know: put keys at their LEDs instead
            packed = [(i, lamps[i].x / k.PITCH, 1.0) for i in idx]
        for i, x0, w in packed:
            rects[i] = (x0, row, w)
    return rects


def hexcolor(c):
    return "%02x%02x%02x" % tuple(round(k.clamp(v) * 255) for v in c)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class FakeMusic:
    """A made-up 120 bpm track, so music cards have something to dance to."""

    def __init__(self):
        self.bands = [0.0] * k.Audio.NBANDS
        self.level = self.left = self.right = self.bass = self.mid = self.treble = self.beat = 0.0
        self.beats, self.strength, self.centroid = 0, 0.8, 0.4

    def update(self, t):
        beat, ph = divmod(t, 0.5)
        self.beats, self.beat = int(beat), math.exp(-ph * 9)
        self.bass = 0.25 + 0.75 * math.exp(-ph * 6)
        self.mid = 0.5 + 0.3 * math.sin(t * 3.1)
        self.treble = 0.4 + 0.3 * math.sin(t * 7.3)
        self.bands = [k.clamp((self.bass if i < 3 else 1 - i / 22) * (0.6 + 0.4 * math.sin(t * 2.3 + i * 1.7)))
                      for i in range(k.Audio.NBANDS)]
        self.level = (self.bass + self.mid + self.treble) / 3
        self.left = k.clamp(self.level * 0.95 + 0.1 * math.sin(t * 5))
        self.right = k.clamp(self.level * 0.85 + 0.1 * math.cos(t * 4))


class Preview:
    """One spec running locally: for a card, or for the big preview while the service is down."""

    def __init__(self, lamps, spec, color, music):
        base_cls, base_color, layers = k.parse_spec(spec, color)
        self.base = base_cls(lamps, base_color)
        self.layers = [cls(lamps, c) for cls, c in layers]
        for e in (self.base, *self.layers):
            e.audio = music
        self.typing = any(e.needs_keys for e in (self.base, *self.layers))
        self.n, self.next_key = len(lamps), 0.0

    def frame(self, t):
        if self.typing and t >= self.next_key:  # type by itself
            i = random.randrange(self.n)
            self.base.on_key(i, t)
            for layer in self.layers:
                layer.on_key(i, t)
            self.next_key = t + random.uniform(0.2, 0.55)
        colors = list(self.base.render(t))
        for layer in self.layers:
            layer.apply(colors, t)
        return colors


def rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


def rounded_rect(x, y, w, h, r):
    rr = Gsk.RoundedRect()  # keep this struct itself: init_from_rect() returns a borrowed view of it
    rr.init_from_rect(rect(x, y, w, h), r)
    return rr


def rgba(c, alpha=1.0):
    color = Gdk.RGBA()
    color.red, color.green, color.blue, color.alpha = c[0], c[1], c[2], alpha
    return color


class KeyboardView(Gtk.Widget):
    """A keyboard drawn from lamp colours on the GPU. The body, keycap shapes and gloss are
    cached textures; each frame only uploads two tiny colour textures (4 texels per key)."""
    __gtype_name__ = "KbrgbKeyboardView"
    CAP = (0.075, 0.07, 0.095)  # an unlit keycap
    Q = 4
    static = {}  # (size, scale, hero) -> cached layers, shared by every card

    def __init__(self, rects, hero, width, height):
        super().__init__()
        self.set_size_request(width, height)
        self.rects, self.hero = rects, hero
        self.cols = max(x + w for x, _, w in rects)
        self.rows = max(r for _, r, _ in rects) + 1
        self.tw, self.th = round(self.cols * self.Q), round(self.rows * self.Q)
        self.cells = [(round(x * self.Q), round((x + w) * self.Q), r) for x, r, w in rects]
        self.led = self.caps = None

    def set_colors(self, colors):
        self.led = self.texture(colors, None)
        self.caps = self.texture(colors, self.CAP)
        self.queue_draw()

    def texture(self, colors, base):
        stride = self.tw * 4
        buf = bytearray(stride * self.th)
        for (x0, x1, row), c in zip(self.cells, colors):
            if base:
                c = (base[0] + c[0] * 0.92, base[1] + c[1] * 0.92, base[2] + c[2] * 0.92)
            px = bytes([0 if v <= 0 else 255 if v >= 1 else int(v * 255) for v in c] + [255]) * (x1 - x0)
            for y in range(row * self.Q, (row + 1) * self.Q):
                buf[y * stride + x0 * 4:y * stride + x1 * 4] = px
        return Gdk.MemoryTexture.new(self.tw, self.th, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(buf)), stride)

    @staticmethod
    def cairo_texture(w, h, scale, paint):
        surface = cairo.ImageSurface(cairo.Format.ARGB32, w * scale, h * scale)
        cr = cairo.Context(surface)
        cr.scale(scale, scale)
        paint(cr)
        surface.flush()
        return Gdk.MemoryTexture.new(surface.get_width(), surface.get_height(),
                                     Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                     GLib.Bytes.new(bytes(surface.get_data())), surface.get_stride())

    def layers(self, w, h, scale):
        key = (w, h, scale, self.hero)
        if key in self.static:
            return self.static[key]
        pad = 0.45 if self.hero else 0.3
        unit = min(w / (self.cols + 2 * pad), h / (self.rows + 2 * pad))
        bw, bh = (self.cols + 2 * pad) * unit, (self.rows + 2 * pad) * unit
        bx, by = (w - bw) / 2, (h - bh) / 2
        kx, ky = bx + pad * unit, by + pad * unit
        gap, radius = unit * 0.13, unit * (0.16 if self.hero else 0.12)
        caps = [(kx + x * unit + gap / 2, ky + r * unit + gap / 2, kw * unit - gap, unit - gap) for x, r, kw in self.rects]

        def body(cr):
            rounded(cr, bx, by, bw, bh, unit * 0.32)
            g = cairo.LinearGradient(0, by, 0, by + bh)
            g.add_color_stop_rgba(0, 0.11, 0.10, 0.15, 0.97)
            g.add_color_stop_rgba(1, 0.05, 0.045, 0.07, 0.97)
            cr.set_source(g)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.set_line_width(1)
            cr.stroke()

        def shapes(cr):
            for x, y, cw, ch in caps:
                rounded(cr, x, y, cw, ch, radius)
            cr.set_source_rgb(1, 1, 1)
            cr.fill()

        def gloss(cr):
            for x, y, cw, ch in caps:
                g = cairo.LinearGradient(0, y, 0, y + ch * 0.55)
                g.add_color_stop_rgba(0, 1, 1, 1, 0.17)
                g.add_color_stop_rgba(1, 1, 1, 1, 0.0)
                rounded(cr, x + 1, y + 1, cw - 2, ch * 0.55, radius * 0.8)
                cr.set_source(g)
                cr.fill()

        spill = 1.6 * unit
        while len(self.static) >= 16:  # cards resize with the window: keep only recent sizes
            del self.static[next(iter(self.static))]
        self.static[key] = layer = {
            "body": self.cairo_texture(w, h, scale, body),
            "shapes": self.cairo_texture(w, h, scale, shapes),
            "gloss": self.cairo_texture(w, h, scale, gloss) if self.hero else None,
            "clip": rounded_rect(bx, by, bw, bh, unit * 0.32),
            "caps": caps,
            "unit": unit,
            "radius": radius,
            "bloom": unit * 0.28,
            "keys": rect(kx, ky, self.cols * unit, self.rows * unit),
            "spill": rect(max(0, kx - spill), max(0, ky - spill), min(w, self.cols * unit + 2 * spill),
                          min(h, self.rows * unit + 2 * spill)),
            "blur": unit * 1.1,
            "all": rect(0, 0, w, h),
        }
        return layer

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0 or not self.led:
            return
        layer = self.layers(w, h, self.get_scale_factor())
        if self.hero:  # light spilling onto the desk around the keyboard
            snapshot.push_opacity(0.55)
            snapshot.push_blur(layer["blur"])
            snapshot.append_scaled_texture(self.led, Gsk.ScalingFilter.LINEAR, layer["spill"])
            snapshot.pop()
            snapshot.pop()
        snapshot.append_texture(layer["body"], layer["all"])
        snapshot.push_rounded_clip(layer["clip"])  # backlight blooming between the caps
        snapshot.push_blur(layer["bloom"])
        snapshot.append_scaled_texture(self.led, Gsk.ScalingFilter.LINEAR, layer["keys"])
        snapshot.pop()
        snapshot.pop()
        snapshot.push_mask(Gsk.MaskMode.ALPHA)  # keycaps: one colour per key, cut to cap shapes
        snapshot.append_texture(layer["shapes"], layer["all"])
        snapshot.pop()
        snapshot.append_scaled_texture(self.caps, Gsk.ScalingFilter.NEAREST, layer["keys"])
        snapshot.pop()
        if layer["gloss"]:
            snapshot.append_texture(layer["gloss"], layer["all"])


class KeyEditor(KeyboardView):
    """The big keyboard on the Per-key page: legends on the caps, click or drag to paint keys.
    A stroke paints with the brush, unless its first key already has the brush colour (or it
    is a right-click, or the brush is the eraser): then the stroke clears keys instead."""
    __gtype_name__ = "KbrgbKeyEditor"
    PIN_RING = (1, 1, 1)

    def __init__(self, win):
        super().__init__(win.rects, True, 560, 240)
        self.static = {}  # it resizes with the window: cache only the current size, not every one
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_cursor_from_name("pointer")
        self.win, self.lamps = win, win.lamps
        self.hover = self.mode = self.origin = None
        self.done, self.erasing = set(), False
        drag = Gtk.GestureDrag(button=0)
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self.set_hover(self.key_at(x, y)))
        motion.connect("leave", lambda *_: self.set_hover(None))
        self.add_controller(motion)

    def layers(self, w, h, scale):
        if (w, h, scale, self.hero) not in self.static:
            self.static.clear()
        layer = super().layers(w, h, scale)
        if "legends" not in layer:
            layer["legends"] = self.cairo_texture(w, h, scale, lambda cr: self.draw_legends(cr, layer))
            layer["outlines"] = [rounded_rect(x, y, cw, ch, layer["radius"]) for x, y, cw, ch in layer["caps"]]
        return layer

    def draw_legends(self, cr, layer):
        for (x, y, cw, ch), lamp in zip(layer["caps"], self.lamps):
            text = k.KEYS.get(lamp.usage, ("", ""))[1]
            if not text:
                continue
            font = Pango.FontDescription.from_string("Sans Bold")
            font.set_absolute_size(layer["unit"] * (0.3 if len(text) == 1 else 0.21) * Pango.SCALE)
            pl = PangoCairo.create_layout(cr)
            pl.set_font_description(font)
            pl.set_text(text, -1)
            tw, th = pl.get_pixel_size()
            tx, ty = x + (cw - tw) / 2, y + (ch - th) / 2
            cr.move_to(tx, ty + 1)  # a soft shadow keeps legends readable on bright keys
            cr.set_source_rgba(0, 0, 0, 0.55)
            PangoCairo.show_layout(cr, pl)
            cr.move_to(tx, ty)
            cr.set_source_rgba(1, 1, 1, 0.9)
            PangoCairo.show_layout(cr, pl)

    def key_at(self, x, y):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return None
        for i, (cx, cy, cw, ch) in enumerate(self.layers(w, h, self.get_scale_factor())["caps"]):
            if cx <= x < cx + cw and cy <= y < cy + ch and self.lamps[i].usage:
                return i
        return None

    def set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def on_begin(self, gesture, x, y):
        self.origin, self.mode, self.done = (x, y), None, set()
        self.erasing = gesture.get_current_button() == 3
        self.visit(self.key_at(x, y))

    def on_update(self, _gesture, dx, dy):
        i = self.key_at(self.origin[0] + dx, self.origin[1] + dy)
        self.set_hover(i)
        self.visit(i)

    def visit(self, i):
        if i is None or i in self.done:
            return
        self.done.add(i)
        if self.mode is None:  # the stroke's first key decides between painting and clearing
            brush = self.win.brush
            clear = self.erasing or brush is None or self.win.pins.get(i) == k.parse_color(brush)
            self.mode = "clear" if clear else brush
            self.win.checkpoint()
        self.win.paint([i], None if self.mode == "clear" else self.mode)

    def do_snapshot(self, snapshot):
        super().do_snapshot(snapshot)
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0 or not self.led:
            return
        layer = self.layers(w, h, self.get_scale_factor())
        snapshot.append_texture(layer["legends"], layer["all"])
        ring = rgba(self.PIN_RING, 0.55)
        for i in self.win.pins:
            snapshot.append_border(layer["outlines"][i], [1.5] * 4, [ring] * 4)
        if self.hover is not None:
            brush = self.win.brush
            hover = rgba(k.parse_color(brush) if brush else (1, 1, 1))
            snapshot.append_border(layer["outlines"][self.hover], [2.5] * 4, [hover] * 4)


class CardView(KeyboardView):
    """A card's keyboard: as wide as the card it sits in, and as tall as that width needs."""
    __gtype_name__ = "KbrgbCardView"
    natural = 136  # px; the window lowers it when a fixed number of columns is chosen
    MIN, ASPECT = 84, 66 / 186

    def __init__(self, rects):
        super().__init__(rects, False, -1, -1)
        self.set_hexpand(True)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.MIN, max(self.MIN, CardView.natural), -1, -1
        h = round((for_size if for_size > 0 else CardView.natural) * self.ASPECT)
        return h, h, -1, -1


def corner_button(css, icon, tip):
    return dict(css_classes=[css], icon_name=icon, tooltip_text=tip, halign=Gtk.Align.END, valign=Gtk.Align.START,
                margin_top=5, margin_end=5)


class Card(Gtk.Button):
    def __init__(self, win, key, title, desc, group, spec):
        super().__init__(css_classes=["card"], tooltip_text=desc[0].upper() + desc[1:])
        self.key, self.group, self.win = key, group, win
        self.haystack = " ".join((key, title, desc, k.CATEGORIES[group][1])).lower()
        self.view = CardView(win.rects)
        self.set_preview(spec)
        overlay = Gtk.Overlay(child=self.view)
        self.star = None
        if key in k.EFFECTS:
            self.star = Gtk.ToggleButton(**corner_button("star", "non-starred-symbolic", "Favourite (Ctrl+D)"))
            self.star.connect("toggled", self.on_star)
            overlay.add_overlay(self.star)
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["card-title"]))
        head.append(Gtk.Label(label=k.CATEGORIES[group][0], css_classes=["card-glyph", group]))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(overlay)
        box.append(head)
        box.append(Gtk.Label(label=desc[0].upper() + desc[1:], xalign=0, wrap=True, lines=2, max_width_chars=10,
                             ellipsize=Pango.EllipsizeMode.END, css_classes=["card-desc"]))
        self.set_child(box)
        self.connect("clicked", lambda *_: win.choose(key))

    def set_preview(self, spec):
        self.preview = Preview(self.win.lamps, spec, None, self.win.music)
        self.view.set_colors(self.preview.frame(0.0))

    def advance(self, t):
        self.view.set_colors(self.preview.frame(t))

    def on_star(self, button):
        button.set_icon_name("starred-symbolic" if button.get_active() else "non-starred-symbolic")
        if not self.win.syncing:
            self.win.set_favorite(self.key, button.get_active())


class LookCard(Gtk.Button):
    """A saved (or starter) look: its effect, colour grading and painted keys, ready to apply."""

    def __init__(self, win, name, look, user):
        super().__init__(css_classes=["card"], tooltip_text=f"Switch to “{name}”")
        self.win, self.name, self.look = win, name, look
        try:
            self.preview = Preview(win.lamps, look["spec"], look.get("color"), win.music)
        except (ValueError, KeyError):
            self.preview = Preview(win.lamps, "off", None, win.music)
        self.pins = k.parse_pins(look.get("keys"), win.lamps)
        self.view = CardView(win.rects)
        overlay = Gtk.Overlay(child=self.view)
        if user:
            delete = Gtk.Button(**corner_button("delete", "user-trash-symbolic", "Delete this look"))
            delete.connect("clicked", lambda *_: win.forget_look(name))
            overlay.add_overlay(delete)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(overlay)
        box.append(Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["card-title"]))
        bits = [describe(look.get("spec", "off"), look.get("color"))]
        if "keys" in look and look["keys"]:
            bits.append(f"◆ {len(look['keys'])} keys")
        box.append(Gtk.Label(label="  ·  ".join(bits), xalign=0, wrap=True, lines=2, max_width_chars=10,
                             ellipsize=Pango.EllipsizeMode.END, css_classes=["card-desc"]))
        self.set_child(box)
        self.connect("clicked", lambda *_: win.apply_look(name))
        self.advance(0.0)

    def matches(self, cfg):
        same = all(cfg[f] == self.look.get(f, default) for f, default in k.LOOK_FIELDS.items())
        return same and ("keys" not in self.look or self.look["keys"] == cfg["keys"])

    def advance(self, t):
        look = self.look
        colors = k.grade(self.preview.frame(t), look.get("saturation", 100) / 100, look.get("hue", 0) / 360)
        dim = 0.4 + 0.6 * look.get("brightness", 100) / 100  # dim looks stay readable on a card
        colors = [k.scale(c, dim) for c in colors]
        for i, c in self.pins.items():
            colors[i] = c
        self.view.set_colors(colors)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Lumikey", default_width=1240, default_height=780)
        self.add_css_class("lumikey")
        self.custom_css = app.custom_css
        self.lamps = load_lamps()
        self.music = FakeMusic()
        self.rects = key_rects(self.lamps)
        self.t0, self.ticks, self.closed, self.rr = time.monotonic(), 0, False, 0
        self.cfg, self.cfg_mtime = k.load_config(), k.config_mtime()
        self.syncing, self.service_checked, self.last_solid, self.setting_up = False, False, None, False
        self.pending, self.scales, self.values = {}, {}, {}
        self.filter, self.choices, self.shown_looks, self.look_cards = "all", {}, None, []
        self.pins, self.brush, self.history = {}, "pink", []  # brush None = eraser
        self.brush_css = app.brush_css
        CardView.natural = 136 if not self.cfg["columns"] else 96

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
        self.pages.add_named(self.build_gallery(), "effects")
        self.pages.add_named(self.build_editor(), "keys")
        self.pages.add_named(self.build_looks(), "looks")
        self.pages.add_named(self.build_settings(), "settings")
        self.side = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT, child=self.build_side(),
                                 reveal_child=self.cfg["sidebar"])
        body = Gtk.Box(margin_start=22, margin_end=22, margin_bottom=20, vexpand=True)
        body.append(self.side)
        body.append(self.pages)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self.build_header())
        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self.on_setup)
        root.append(self.banner)
        root.append(body)
        self.refresh_setup()
        self.toasts = Adw.ToastOverlay(child=root)
        self.set_content(self.toasts)

        for name, callback in (("setup-check", lambda *_: self.show_setup()), ("about", lambda *_: self.show_about()),
                               ("report", lambda *_: self.report_device()),
                               ("shortcuts", lambda *_: self.on_key(None, Gdk.KEY_slash, 0,
                                                                    Gdk.ModifierType.CONTROL_MASK))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("close-request", self.on_close)
        self.apply_columns(self.cfg["columns"])
        self.sync_from_config()
        self.refilter()
        self.set_brush("pink")
        colors = self.hero_frame(0.0)[0]
        self.stage.set_colors(colors)
        self.editor.set_colors(colors)
        GLib.timeout_add(33, self.tick)

    # ------------------------------------------------------------ layout

    def build_header(self):
        switch = Gtk.Box(spacing=6, css_classes=["page-switch"])
        self.page_buttons, first = {}, None
        for n, (name, text, tip) in enumerate(PAGES, 1):
            b = Gtk.ToggleButton(label=text, css_classes=["pill"], group=first, active=first is None,
                                 tooltip_text=f"{tip} (Ctrl+{n})")
            b.connect("toggled", self.on_page, name)
            first = first or b
            self.page_buttons[name] = b
            switch.append(b)
        header = Adw.HeaderBar(title_widget=switch)
        self.power = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Lights on / off")
        self.power.connect("notify::active", self.on_power)
        header.pack_start(self.power)
        self.side_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", css_classes=["pill"],
                                            valign=Gtk.Align.CENTER, active=self.cfg["sidebar"],
                                            tooltip_text="Show / hide the side panel (Ctrl+B)")
        self.side_toggle.connect("toggled", self.on_side_toggle)
        header.pack_start(self.side_toggle)
        menu, more = Gio.Menu(), Gio.Menu()
        menu.append("Device & setup check", "win.setup-check")
        menu.append("Keyboard shortcuts", "win.shortcuts")
        more.append("Report my keyboard…", "win.report")
        more.append("About Lumikey", "win.about")
        menu.append_section(None, more)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                                       tooltip_text="Menu"))
        self.builtin = Gtk.ToggleButton(label="Built-in", css_classes=["pill"], valign=Gtk.Align.CENTER,
                                        tooltip_text="Hand lighting back to the keyboard's own effects")
        self.builtin.connect("toggled", self.on_builtin)
        header.pack_end(self.builtin)
        self.laptop = Gtk.ToggleButton(label="⌨  All keyboards", css_classes=["pill"], valign=Gtk.Align.CENTER,
                                       tooltip_text="Typing on other keyboards (e.g. a laptop's) lights this one too")
        self.laptop.connect("toggled", self.on_laptop)
        header.pack_end(self.laptop)
        return header

    def build_side(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["side"])
        self.stage = KeyboardView(self.rects, True, 392, 170)
        self.stage.set_margin_bottom(6)
        panel.append(self.stage)
        top = Gtk.Box(spacing=10)
        self.eyebrow = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["eyebrow"])
        self.live = Gtk.Box(spacing=6, css_classes=["live"], valign=Gtk.Align.CENTER,
                            tooltip_text="Mirroring what your keyboard shows right now")
        self.live_dot = Gtk.Box(css_classes=["live-dot"], valign=Gtk.Align.CENTER)
        self.live.append(self.live_dot)
        self.live.append(Gtk.Label(label="LIVE"))
        top.append(self.eyebrow)
        top.append(self.live)
        title_row = Gtk.Box(spacing=6)
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["hero-title"])
        title_row.append(self.title)
        for icon, tip, action in (("go-previous-symbolic", "Previous effect (Ctrl+←)", lambda *_: self.step(-1)),
                                  ("non-starred-symbolic", "Favourite (Ctrl+D)", lambda *_: self.toggle_current_fav()),
                                  ("go-next-symbolic", "Next effect (Ctrl+→)", lambda *_: self.step(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["nav"], valign=Gtk.Align.CENTER)
            b.connect("clicked", action)
            title_row.append(b)
            if icon.startswith("non-starred"):
                self.hero_star = b
        self.layers_label = Gtk.Label(xalign=0, wrap=True, css_classes=["hero-layers"])
        self.desc = Gtk.Label(xalign=0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END, max_width_chars=40,
                              css_classes=["hero-desc"])
        for w in (top, title_row, self.layers_label, self.desc):
            panel.append(w)

        tuning = Gtk.Box(spacing=8, margin_top=6)
        tuning.append(Gtk.Label(label="TUNING", xalign=0, hexpand=True, css_classes=["eyebrow"]))
        reset = Gtk.Button(label="↺  Reset", css_classes=["pill", "small"],
                           tooltip_text="Speed 1×, saturation 100%, no hue shift")
        reset.connect("clicked", lambda *_: self.commit(speed=1.0, saturation=100, hue=0))
        tuning.append(reset)
        panel.append(tuning)
        for key in SLIDERS:
            panel.append(self.slider(key))
        self.scales["speed"].add_mark(1.0, Gtk.PositionType.BOTTOM, None)
        self.scales["saturation"].add_mark(100, Gtk.PositionType.BOTTOM, None)

        self.color_title = Gtk.Label(xalign=0, css_classes=["eyebrow"], margin_top=6)
        panel.append(self.color_title)
        self.color_box = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.swatches = {}
        for name in ["rainbow", *k.COLORS, "custom"]:
            tip = {"rainbow": "automatic colours", "custom": "pick any colour"}.get(name, name)
            b = Gtk.Button(css_classes=["swatch", f"sw-{name}"], tooltip_text=tip)
            if name == "custom":
                b.set_icon_name("list-add-symbolic")
            b.connect("clicked", self.on_swatch, name)
            self.swatches[name] = b
            self.color_box.append(b)
        panel.append(self.color_box)

        panel.append(Gtk.Label(label="LAYERS  ·  STACK ON ANY EFFECT", xalign=0, css_classes=["eyebrow"], margin_top=6))
        layer_row = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.layer_buttons = {}
        for name, (cls, desc) in k.LAYERS.items():
            b = Gtk.ToggleButton(label=f"{k.CATEGORIES[cls.category][0]} {name.capitalize()}", css_classes=["pill"],
                                 tooltip_text=desc)
            b.connect("toggled", self.on_layer, name)
            self.layer_buttons[name] = b
            layer_row.append(b)
        panel.append(layer_row)
        panel.append(Gtk.Label(label="Esc closes this window  ·  Ctrl+/ for shortcuts", xalign=0,
                               vexpand=True, valign=Gtk.Align.END, wrap=True, css_classes=["hint"]))
        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False, margin_end=20)
        side.set_child(Adw.Clamp(maximum_size=430, tightening_threshold=430, child=panel))
        return side

    def slider(self, key):
        title, lo, hi, step, _ = SLIDERS[key]
        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label=title, xalign=0, width_chars=10, css_classes=["row-label"]))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.add_css_class(key)
        scale.connect("value-changed", self.on_scale, key)
        value = Gtk.Label(xalign=1, width_chars=5, css_classes=["row-value"])
        row.append(scale)
        row.append(value)
        self.scales[key], self.values[key] = scale, value
        return row

    def build_gallery(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
        self.search = Gtk.SearchEntry(hexpand=True, css_classes=["search"])  # before the grid: its filter reads it
        grid = self.build_grid()
        self.search.set_placeholder_text(f"Search {len(self.cards)} effects…   Ctrl+F")
        self.search.connect("search-changed", lambda *_: self.refilter())
        tools = Gtk.Box(spacing=8, margin_top=2)
        tools.append(self.search)
        surprise = Gtk.Button(css_classes=["pill"], tooltip_text="A random effect from the ones shown (Ctrl+R)",
                              child=Adw.ButtonContent(icon_name="media-playlist-shuffle-symbolic", label="Surprise me"))
        surprise.connect("clicked", lambda *_: self.surprise())
        tools.append(surprise)
        self.columns = Gtk.DropDown.new_from_strings(["Auto columns", *[f"{n} columns" for n in COLUMNS]])
        self.columns.add_css_class("columns")
        self.columns.set_tooltip_text("Cards per row. Hide the side panel (Ctrl+B) to fit even more")
        self.columns.set_selected(COLUMNS.index(self.cfg["columns"]) + 1 if self.cfg["columns"] in COLUMNS else 0)
        self.columns.connect("notify::selected", self.on_columns)
        tools.append(self.columns)
        page.append(tools)

        cats = Gtk.Box(spacing=10)
        pills = Adw.WrapBox(child_spacing=8, line_spacing=8, hexpand=True)
        first = None
        filters = [("all", "All", "every effect"), ("favorites", "★  Favourites", "the ones you starred")]
        filters += [(c, f"{glyph}  {title}", blurb) for c, (glyph, title, blurb) in k.CATEGORIES.items()]
        for key, text, tip in filters:
            b = Gtk.ToggleButton(label=text, css_classes=["pill", f"cat-{key}"], group=first, active=first is None,
                                 tooltip_text=tip[0].upper() + tip[1:])
            b.connect("toggled", self.on_filter, key)
            first = first or b
            pills.append(b)
        cats.append(pills)
        self.count = Gtk.Label(xalign=1, valign=Gtk.Align.START, margin_top=6, css_classes=["count"])
        cats.append(self.count)
        page.append(cats)
        self.empty = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["empty"], visible=False)
        page.append(self.empty)
        page.append(grid)
        return page

    def build_grid(self):
        self.flow = self.flowbox()
        self.flow.set_filter_func(lambda child: self.visible_card(child.get_child()))
        self.cards = {}
        for cat in k.CATEGORIES:
            entries = [(name, desc, name) for name, (cls, desc) in k.EFFECTS.items() if cls.category == cat]
            entries = [(key, desc, spec) for key, (c, desc), spec in
                       (("solid", UTILITY["solid"], "violet"), ("dim", UTILITY["dim"], "dim+ripple")) if c == cat] + entries
            for name, desc, spec in entries:
                card = Card(self, name, title_of(name), desc, cat, spec)
                self.flow.append(card)
                card.get_parent().set_focusable(False)
                self.cards[name] = card
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_child(self.flow)
        return self.scroller

    @staticmethod
    def flowbox():
        return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                           min_children_per_line=1, max_children_per_line=12, column_spacing=12, row_spacing=12,
                           margin_top=2, margin_bottom=8, margin_end=6)

    def build_editor(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["side"], margin_top=2)
        top = Gtk.Box(spacing=10)
        top.append(Gtk.Label(label="PER-KEY COLOURS", xalign=0, hexpand=True, css_classes=["eyebrow", "keys"]))
        self.pin_count = Gtk.Label(css_classes=["count"])
        top.append(self.pin_count)
        page.append(top)
        page.append(Gtk.Label(label="Paint your keys", xalign=0, css_classes=["hero-title"]))
        page.append(Gtk.Label(label="Painted keys keep their colour on top of any effect. Click or drag to paint; "
                                    "click a painted key again, or right-click, to give it back to the effect.",
                              xalign=0, wrap=True, css_classes=["hero-desc"]))
        self.notice = Gtk.Label(xalign=0, wrap=True, css_classes=["notice"], margin_top=2)
        page.append(self.notice)
        self.editor = KeyEditor(self)
        self.editor.set_margin_top(4)
        page.append(self.editor)

        page.append(Gtk.Label(label="BRUSH", xalign=0, css_classes=["eyebrow"], margin_top=4))
        brushes = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.brushes = {}
        for name in [*k.COLORS, "custom", "eraser"]:
            tip = {"custom": "pick any colour", "eraser": "eraser: keys follow the effect again"}.get(name, name)
            b = Gtk.Button(css_classes=["swatch", "sw-brush" if name == "custom" else f"sw-{name}"], tooltip_text=tip)
            if name in ("custom", "eraser"):
                b.set_icon_name("list-add-symbolic" if name == "custom" else "edit-clear-symbolic")
            b.connect("clicked", self.on_brush, name)
            self.brushes[name] = b
            brushes.append(b)
        page.append(brushes)

        page.append(Gtk.Label(label="QUICK PAINT", xalign=0, css_classes=["eyebrow"], margin_top=4))
        quick = Adw.WrapBox(child_spacing=8, line_spacing=8)
        for group, text in QUICK:
            b = Gtk.Button(label=text, css_classes=["pill"], tooltip_text="paint with the brush; again to clear")
            b.connect("clicked", self.on_quick, group)
            quick.append(b)
        for kind, text, tip in (("rainbow", "🌈  Rainbow", "paint every key a rainbow, left to right"),
                                ("random", "🎲  Random", "paint every key a random colour"),
                                ("gradient", "◐  Brush fade", "fade every key from the brush colour to black, left to right")):
            b = Gtk.Button(label=text, css_classes=["pill"], tooltip_text=tip)
            b.connect("clicked", self.on_pattern, kind)
            quick.append(b)
        page.append(quick)

        actions = Gtk.Box(spacing=8, margin_top=6)
        self.undo_button = Gtk.Button(css_classes=["pill"], tooltip_text="Undo (Ctrl+Z)",
                                      child=Adw.ButtonContent(icon_name="edit-undo-symbolic", label="Undo"))
        self.undo_button.connect("clicked", lambda *_: self.undo())
        self.clear_button = Gtk.Button(css_classes=["pill"], tooltip_text="Give every key back to the effect",
                                       child=Adw.ButtonContent(icon_name="edit-clear-all-symbolic", label="Clear all"))
        self.clear_button.connect("clicked", lambda *_: self.clear_pins())
        actions.append(self.undo_button)
        actions.append(self.clear_button)
        page.append(actions)
        return page

    def build_looks(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=2, margin_end=6)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["side"])
        head.append(Gtk.Label(label="LOOKS", xalign=0, css_classes=["eyebrow", "keys"]))
        head.append(Gtk.Label(label="Save your favourite setups", xalign=0, css_classes=["hero-title"]))
        head.append(Gtk.Label(label="A look remembers the effect, layers, colour, brightness, speed, saturation, hue "
                                    "and your painted keys. Starter looks leave your painted keys as they are.",
                              xalign=0, wrap=True, css_classes=["hero-desc"]))
        row = Gtk.Box(spacing=8, margin_top=4)
        self.look_name = Gtk.Entry(placeholder_text="Name this look…", hexpand=True, css_classes=["entry-name"])
        self.look_name.connect("activate", lambda *_: self.save_look())
        row.append(self.look_name)
        save = Gtk.Button(css_classes=["accent"], tooltip_text="Save what's on the keyboard now (Ctrl+S)",
                          child=Adw.ButtonContent(icon_name="document-save-symbolic", label="Save current look"))
        save.connect("clicked", lambda *_: self.save_look())
        row.append(save)
        for icon, label, tip, callback in (
                ("document-open-symbolic", "Import…", "Add looks from a file someone shared", self.import_looks),
                ("document-send-symbolic", "Export…", "Save your looks to a file to share or back up", self.export_looks)):
            b = Gtk.Button(css_classes=["pill"], tooltip_text=tip, child=Adw.ButtonContent(icon_name=icon, label=label))
            b.connect("clicked", lambda *_, cb=callback: cb())
            row.append(b)
        head.append(row)
        page.append(head)
        page.append(Gtk.Label(label="YOUR LOOKS", xalign=0, css_classes=["section"]))
        self.no_looks = Gtk.Label(label="Nothing saved yet. Set things up the way you like, give it a name above "
                                        "and hit Save.", xalign=0, wrap=True, css_classes=["hint"])
        page.append(self.no_looks)
        self.user_flow = self.flowbox()
        page.append(self.user_flow)
        page.append(Gtk.Label(label="STARTER LOOKS", xalign=0, css_classes=["section"]))
        self.starter_flow = self.flowbox()
        page.append(self.starter_flow)
        self.looks_scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=page)
        return self.looks_scroller

    def build_settings(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["side"], margin_top=2,
                       margin_end=6, valign=Gtk.Align.START)
        page.append(Gtk.Label(label="SETTINGS", xalign=0, css_classes=["eyebrow", "keys"]))
        page.append(Gtk.Label(label="Make it yours", xalign=0, css_classes=["hero-title"]))
        page.append(Gtk.Label(label="DEVICE", xalign=0, css_classes=["section"], margin_top=14))
        row = Gtk.Box(spacing=16, css_classes=["setting"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(Gtk.Label(label="Light this device", xalign=0, css_classes=["setting-title"]))
        self.device_desc = Gtk.Label(xalign=0, wrap=True, css_classes=["setting-desc"])
        text.append(self.device_desc)
        row.append(text)
        self.device_ids, self.filling_devices = [], False
        self.device_drop = Gtk.DropDown(model=Gtk.StringList(), valign=Gtk.Align.CENTER)
        self.device_drop.connect("notify::selected", self.on_device)
        row.append(self.device_drop)
        page.append(row)
        page.append(Gtk.Label(label="SETUP CHECK", xalign=0, css_classes=["section"], margin_top=14))
        self.health = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["setting"])
        page.append(self.health)
        buttons = Gtk.Box(spacing=8, margin_top=8)
        for label, tip, callback in (
                ("↻  Check again", "Look for devices and re-run the checks", lambda *_: self.refresh_setup()),
                ("🔑  Set up access", "Allow Lumikey to use your keyboard (asks for your password once)",
                 lambda *_: self.on_setup(self.banner)),
                ("✉  Report my keyboard", "Copy your device details and open a GitHub issue, so support can be added",
                 lambda *_: self.report_device())):
            b = Gtk.Button(label=label, css_classes=["pill"], tooltip_text=tip)
            b.connect("clicked", callback)
            buttons.append(b)
        page.append(buttons)
        for section, rows in SETTINGS:
            page.append(Gtk.Label(label=section, xalign=0, css_classes=["section"], margin_top=14))
            for key, title, desc, options in rows:
                page.append(self.choice_row(key, title, desc, options))
        page.append(Gtk.Label(label="KEYBOARD SHORTCUTS", xalign=0, css_classes=["section"], margin_top=18))
        grid = Gtk.Grid(column_spacing=14, row_spacing=8, margin_top=6)
        for n, (combo, what) in enumerate(SHORTCUTS):
            grid.attach(Gtk.Label(label=combo, css_classes=["kbd"], halign=Gtk.Align.START), (n % 2) * 2, n // 2, 1, 1)
            grid.attach(Gtk.Label(label=what, xalign=0, hexpand=True, css_classes=["setting-desc"]),
                        (n % 2) * 2 + 1, n // 2, 1, 1)
        page.append(grid)
        reset = Gtk.Button(label="↺  Reset these settings", css_classes=["pill"], halign=Gtk.Align.START, margin_top=18,
                           tooltip_text="Put every setting on this page back to its default")
        reset.connect("clicked", lambda *_: self.commit(**{key: k.DEFAULTS[key] for _, rows in SETTINGS
                                                            for key, *_ in rows}))
        page.append(reset)
        self.settings_scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=page)
        return self.settings_scroller

    def choice_row(self, key, title, desc, options):
        row = Gtk.Box(spacing=16, css_classes=["setting"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(Gtk.Label(label=title, xalign=0, css_classes=["setting-title"]))
        text.append(Gtk.Label(label=desc, xalign=0, wrap=True, css_classes=["setting-desc"]))
        row.append(text)
        pills = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        first, buttons = None, {}
        for value, label in options:
            b = Gtk.ToggleButton(label=label, css_classes=["pill"], group=first)
            b.connect("toggled", self.on_choice, key, value)
            first = first or b
            buttons[value] = b
            pills.append(b)
        row.append(pills)
        self.choices[key] = buttons
        return row

    # ------------------------------------------------------------ state

    def state(self):
        return parse_state(self.cfg["spec"])

    def resume_state(self):
        r = self.cfg.get("resume") or {}
        base, solid, layers = parse_state(r.get("spec", "galaxy"))
        return (base, solid, layers) if base else ("galaxy", None, [])

    @staticmethod
    def color_target(base, layers):
        if base == "solid":
            return "solid"
        if any(k.LAYERS[n][0].takes_color for n in layers):
            return "layer"
        if base in k.EFFECTS and k.EFFECTS[base][0].takes_color:
            return "base"
        return None

    def commit(self, sync=True, **changes):
        cfg = k.load_config()
        cfg.update(changes)
        k.save_config(cfg)
        self.cfg, self.cfg_mtime = cfg, k.config_mtime()
        if not self.service_checked:
            k.ensure_daemon()
            self.service_checked = True
        if sync:
            self.sync_from_config()

    def put(self, base, solid, layers, color):
        """Write a new spec; the daemon picks it up within a frame."""
        layers = [n for n in k.LAYERS if n in layers]
        spec = "+".join([solid if base == "solid" else base, *layers])
        for c in (color, None):  # drop a colour the new spec can't take
            try:
                k.parse_spec(spec, c)
            except ValueError:
                continue
            self.commit(spec=spec, color=c)
            return

    def remember(self):
        """What to come back to after Off / Built-in."""
        if self.cfg["spec"] in ("auto", "off"):
            return self.cfg.get("resume")
        return {"spec": self.cfg["spec"], "color": self.cfg["color"]}

    def sync_from_config(self):
        self.syncing = True
        try:
            cfg = self.cfg
            spec, color = cfg["spec"], cfg["color"]
            base, solid, layers = self.state()
            self.power.set_active(spec != "off")
            self.builtin.set_active(spec == "auto")
            self.laptop.set_active(cfg["all_keyboards"])
            favs = set(cfg["favorites"])
            for key, card in self.cards.items():
                (card.add_css_class if key == base else card.remove_css_class)("selected")
                if card.star:
                    card.star.set_active(key in favs)
            on_fav = base in favs
            self.hero_star.set_icon_name("starred-symbolic" if on_fav else "non-starred-symbolic")
            (self.hero_star.add_css_class if on_fav else self.hero_star.remove_css_class)("starred")
            self.hero_star.set_sensitive(base in k.EFFECTS)

            if spec == "auto":
                group, title, desc = None, "Built-in effects", "The keyboard is running its own lighting; its Fn shortcuts work"
            elif spec == "off":
                group, title, desc = None, "Lights off", "Flip the switch to bring them back"
            else:
                group = UTILITY[base][0] if base in UTILITY else group_of(k.EFFECTS[base][0])
                title = title_of(base)
                desc = UTILITY[base][1] if base in UTILITY else k.EFFECTS[base][1]
            for g in k.CATEGORIES:
                self.eyebrow.remove_css_class(g)
            if group:
                self.eyebrow.add_css_class(group)
                glyph, name, blurb = k.CATEGORIES[group]
                self.eyebrow.set_label(f"{glyph}  {name.upper()}  ·  {blurb.upper()}")
            else:
                self.eyebrow.set_label("KEYBOARD")
            self.title.set_label(title)
            self.desc.set_label(desc[0].upper() + desc[1:])
            self.layers_label.set_label("   ".join(self.tags(layers)))
            self.layers_label.set_visible(bool(self.layers_label.get_label()))
            self.sync_pins()

            for key, (_, _, _, _, fmt) in SLIDERS.items():
                if not self.pending.get(key):
                    self.scales[key].set_value(cfg[key])
                self.values[key].set_label(fmt.format(cfg[key]))
            for name, b in self.layer_buttons.items():
                b.set_active(name in layers)
            for key, buttons in self.choices.items():
                value = cfg[key]
                if value not in buttons:  # set to something else on the command line: show the nearest
                    numbers = [v for v in buttons if isinstance(v, (int, float)) and not isinstance(v, bool)]
                    value = min(numbers, key=lambda v: abs(v - value)) if numbers and isinstance(value, (int, float)) \
                        else next(iter(buttons))
                buttons[value].set_active(True)

            target = self.color_target(base, layers)
            current = (solid if target == "solid" else color) or None
            custom = current if current and current.lower() not in k.COLORS else None
            for name, b in self.swatches.items():
                on = (name == "rainbow" and current is None and target in ("layer", "base")) \
                    or (current is not None and name == current.lower()) or (name == "custom" and custom)
                (b.add_css_class if on else b.remove_css_class)("active")
            self.swatches["rainbow"].set_visible(target != "solid")
            self.custom_css.load_from_string(f".sw-custom {{ background: #{custom.lstrip('#')}; }}" if custom else "")
            self.color_box.set_sensitive(target is not None)
            self.color_title.set_label({"solid": "KEY COLOUR", "layer": "LAYER COLOUR", "base": "COLOUR"}
                                       .get(target, "COLOUR  ·  THIS EFFECT PAINTS ITS OWN"))

            if solid and solid != self.last_solid:  # the Solid card shows the chosen colour
                self.last_solid = solid
                self.cards["solid"].set_preview(solid)
            try:
                self.hero_sim = Preview(self.lamps, "wave" if spec == "auto" else spec, color, self.music)
            except ValueError:
                self.hero_sim = Preview(self.lamps, "off", None, self.music)
            self.sync_looks()
            if self.filter == "favorites":
                self.refilter()
        finally:
            self.syncing = False

    def tags(self, layers):
        cfg, spec = self.cfg, self.cfg["spec"]
        if spec in ("auto", "off"):
            return []
        tags = [f"+ {n.capitalize()}" for n in layers]
        if cfg["keys"]:
            tags.append(f"◆ {len(cfg['keys'])} pinned")
        if cfg["hue"]:
            tags.append(f"◐ hue +{cfg['hue']}°")
        if cfg["saturation"] != 100:
            tags.append(f"◑ sat {cfg['saturation']}%")
        if cfg["flip"]:
            tags.append({"h": "⇆ mirrored", "v": "⇅ upside down", "hv": "↻ rotated"}[cfg["flip"]])
        if cfg["cycle"]:
            tags.append(f"⟳ every {cfg['cycle']:g} min")
        if cfg["idle"]:
            tags.append(f"☾ sleeps after {cfg['idle']:g} min")
        return tags

    def sync_pins(self):
        self.pins = k.parse_pins(self.cfg["keys"], self.lamps)
        n = len(self.cfg["keys"])
        self.pin_count.set_label(f"{n} key{'' if n == 1 else 's'} pinned" if n else "no keys pinned")
        spec = self.cfg["spec"]
        self.notice.set_label("The keyboard is on its built-in effects, which can't show painted keys. "
                              "Painting switches back to your last effect." if spec == "auto" else
                              "The lights are off. Painting turns them back on." if spec == "off" else "")
        self.notice.set_visible(spec in ("auto", "off"))
        self.undo_button.set_sensitive(bool(self.history))
        self.clear_button.set_sensitive(bool(self.cfg["keys"]))
        self.editor.queue_draw()

    def sync_looks(self):
        if self.shown_looks != self.cfg["looks"]:  # rebuild the cards only when the saved looks change
            self.shown_looks = dict(self.cfg["looks"])
            self.user_flow.remove_all()
            self.starter_flow.remove_all()
            self.look_cards = []
            for flow, looks, user in ((self.user_flow, self.cfg["looks"], True), (self.starter_flow, k.STARTER_LOOKS, False)):
                for name, look in looks.items():
                    card = LookCard(self, name, look, user)
                    flow.append(card)
                    card.get_parent().set_focusable(False)
                    self.look_cards.append(card)
            self.no_looks.set_visible(not self.cfg["looks"])
        for card in self.look_cards:
            (card.add_css_class if card.matches(self.cfg) else card.remove_css_class)("selected")

    # ------------------------------------------------------------ gallery

    def visible_card(self, card):
        if self.filter == "favorites" and card.key not in self.cfg["favorites"]:
            return False
        if self.filter not in ("all", "favorites") and card.group != self.filter:
            return False
        words = self.search.get_text().lower().split()
        return all(w in card.haystack for w in words)

    def shown_cards(self):
        return [c for c in self.cards.values() if self.visible_card(c)]

    def refilter(self):
        self.flow.invalidate_filter()
        n = len(self.shown_cards())
        self.count.set_label(f"{n} effect{'' if n == 1 else 's'}")
        if n:
            self.empty.set_visible(False)
            return
        if self.filter == "favorites" and not self.search.get_text():
            self.empty.set_label("No favourites yet.\nHover over a card and tap its ☆, or press Ctrl+D for the "
                                 "current effect.\nFavourites can also drive Auto-cycle (Settings).")
        else:
            self.empty.set_label(f"Nothing matches “{self.search.get_text()}” here.\nTry another word or the All tab.")
        self.empty.set_visible(True)

    def on_filter(self, button, key):
        if button.get_active():
            self.filter = key
            self.refilter()

    def on_columns(self, dropdown, _param):
        n = COLUMNS[dropdown.get_selected() - 1] if dropdown.get_selected() else 0
        self.apply_columns(n)
        if n != self.cfg["columns"]:
            self.commit(sync=False, columns=n)

    def apply_columns(self, n):
        CardView.natural = 96 if n else 136  # a fixed count: let cards shrink until that many fit
        for flow in (self.flow, self.user_flow, self.starter_flow):
            flow.set_max_children_per_line(n or 12)
        for card in [*self.cards.values(), *self.look_cards]:
            card.view.queue_resize()

    def scroll_to(self, card):
        ok, bounds = card.compute_bounds(self.flow)
        if ok:
            adj = self.scroller.get_vadjustment()
            top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
            if top < adj.get_value() or bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(max(0, top - 40))

    def step(self, direction):
        cards = self.shown_cards() or list(self.cards.values())
        keys = [c.key for c in cards]
        base = self.state()[0]
        i = keys.index(base) + direction if base in keys else (0 if direction > 0 else -1)
        key = keys[i % len(keys)]
        self.choose(key)
        self.scroll_to(self.cards[key])

    def surprise(self):
        base = self.state()[0]
        cards = [c for c in self.shown_cards() if c.key != base] or [c for c in self.cards.values() if c.key != base]
        card = random.choice(cards)
        self.choose(card.key)
        self.scroll_to(card)
        self.toast(f"{k.CATEGORIES[card.group][0]}  {title_of(card.key)}")

    def set_favorite(self, key, on):
        favs = [n for n in self.cfg["favorites"] if n != key] + ([key] if on else [])
        if favs != self.cfg["favorites"]:
            self.commit(favorites=favs)
            self.toast(f"★  {title_of(key)} added to favourites" if on else f"☆  {title_of(key)} removed from favourites")

    def toggle_current_fav(self):
        base = self.state()[0]
        if base in k.EFFECTS:
            self.set_favorite(base, base not in self.cfg["favorites"])

    def toast(self, text, button=None, action=None):
        t = Adw.Toast(title=text, timeout=3 if button else 2)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    # ------------------------------------------------------------ actions

    def choose(self, key):
        base, solid, layers = self.state()
        if key == "solid":
            solid = solid or self.last_solid or "violet"
        self.put(key, solid, layers, self.cfg["color"])

    def on_layer(self, button, name):
        if self.syncing:
            return
        base, solid, layers = self.state()
        if base is None:
            base, solid, _ = self.resume_state()
        layers = [n for n in layers if n != name] + ([name] if button.get_active() else [])
        self.put(base, solid, layers, self.cfg["color"])

    def on_swatch(self, _button, name):
        if name == "custom":
            dialog = Gtk.ColorDialog(title="Pick a colour", with_alpha=False)
            dialog.choose_rgba(self, None, None, self.on_custom_color)
            return
        self.pick(name)

    def on_custom_color(self, dialog, result):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return  # cancelled
        self.pick(hexcolor((rgba.red, rgba.green, rgba.blue)))

    def pick(self, value):
        base, solid, layers = self.state()
        target = self.color_target(base, layers)
        if target == "solid" and value != "rainbow":
            self.put("solid", value, layers, self.cfg["color"])
        elif target in ("layer", "base"):
            self.put(base, solid, layers, None if value == "rainbow" else value)

    def on_scale(self, scale, key):
        if self.syncing:
            return
        self.values[key].set_label(SLIDERS[key][4].format(scale.get_value()))
        if self.pending.get(key):
            GLib.source_remove(self.pending[key])
        self.pending[key] = GLib.timeout_add(90, self.flush_scale, key)

    def flush_scale(self, key):
        self.pending[key] = None
        value = self.scales[key].get_value()
        self.commit(**{key: round(value, 2) if key == "speed" else round(value)})
        return False

    def on_choice(self, button, key, value):
        if button.get_active() and not self.syncing and self.cfg[key] != value:
            self.commit(**{key: value})
            if key == "previews" and not value:
                for card in [*self.cards.values(), *self.look_cards]:
                    card.advance(1.5)  # a nicer still than the very first frame

    def on_power(self, switch, _param):
        if self.syncing:
            return
        if switch.get_active():
            r = self.cfg.get("resume") or {"spec": "galaxy", "color": None}
            self.commit(spec=r["spec"], color=r["color"])
        else:
            self.commit(spec="off", color=None, resume=self.remember())

    def on_builtin(self, button):
        if self.syncing:
            return
        if button.get_active():
            self.commit(spec="auto", color=None, resume=self.remember())
        else:
            r = self.cfg.get("resume") or {"spec": "galaxy", "color": None}
            self.commit(spec=r["spec"] if r["spec"] != "auto" else "galaxy", color=r["color"])

    def on_laptop(self, button):
        if not self.syncing:
            self.commit(all_keyboards=button.get_active())

    def on_side_toggle(self, button):
        self.side.set_reveal_child(button.get_active())
        if button.get_active() != self.cfg["sidebar"]:
            self.commit(sync=False, sidebar=button.get_active())

    def on_page(self, button, name):
        if button.get_active():
            self.pages.set_visible_child_name(name)
            if name == "settings":
                self.refresh_setup()

    # device & setup

    def refresh_setup(self):
        """Fill the device list and the setup checks."""
        found = k.lamparray.find()
        want = self.cfg["device"]
        self.filling_devices = True
        labels = ["Automatic: the first keyboard"] + [f"{c.name} ({c.ids})" + ("  · not supported" if c.quirk else "")
                                                     for c in found]
        self.device_ids = ["", *(c.ids for c in found)]
        if want and want not in self.device_ids:
            labels.append(f"{want} (not plugged in)")
            self.device_ids.append(want)
        self.device_drop.set_model(Gtk.StringList.new(labels))
        self.device_drop.set_selected(self.device_ids.index(want) if want in self.device_ids else 0)
        self.filling_devices = False
        try:
            with open(k.LAYOUT_CACHE) as f:
                lamps = len(json.load(f))
        except (OSError, ValueError):
            lamps = 0
        usable = [c for c in found if not c.quirk or want in (c.ids, c.path)]
        self.device_desc.set_label(f"{len(found)} Dynamic Lighting device{'s' * (len(found) != 1)} found"
                                   + (f"; the last one lit had {lamps} lights" if lamps and usable else "")
                                   if found else "No Dynamic Lighting device is plugged in")
        while child := self.health.get_first_child():
            self.health.remove(child)
        for ok, what, fix in k.health_checks(self.cfg):
            self.health.append(Gtk.Label(label=f"{'✓' if ok else '✗'}  {what}", xalign=0, wrap=True,
                                         css_classes=["check", "ok" if ok else "bad"]))
            if fix and not ok:
                self.health.append(Gtk.Label(label=f"fix: {fix}", xalign=0, wrap=True, selectable=True,
                                             css_classes=["check-fix"]))
        self.check_access()

    def show_setup(self):
        self.show_page("settings")
        GLib.idle_add(lambda: self.settings_scroller.get_vadjustment().set_value(0))

    def on_device(self, drop, _pspec):
        if self.filling_devices:
            return
        i = drop.get_selected()
        if 0 <= i < len(self.device_ids) and self.device_ids[i] != self.cfg["device"]:
            self.commit(device=self.device_ids[i])
            self.toast("Switching device…")
            GLib.timeout_add(2500, lambda: self.refresh_setup() and False)

    def report_device(self):
        """Copy `lumikey probe` to the clipboard and open a new-device issue."""
        out = subprocess.run([sys.executable, "-m", "lumikey", "probe"], capture_output=True, text=True).stdout
        self.get_clipboard().set(out)
        Gtk.UriLauncher.new(f"{k.HOMEPAGE}/issues/new?template=new-device.yml").launch(self, None, None)
        self.toast("Device details copied: paste them into the issue")

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Lumikey", application_icon=APP_ID, version=k.__version__,
            developer_name=k.__author__, developers=[k.__author__], copyright=f"© 2026 {k.__author__}",
            website=k.HOMEPAGE, issue_url=f"{k.HOMEPAGE}/issues", license_type=Gtk.License.GPL_3_0,
            comments="Animated, music- and typing-reactive lighting for Dynamic Lighting (HID LampArray) "
                     "keyboards on Linux.\n\nColours live only in the keyboard's memory; nothing is written "
                     "to its firmware.")
        about.add_link("Supported devices", f"{k.HOMEPAGE}#supported-devices")
        about.present(self)

    # looks sharing

    def export_looks(self):
        if not self.cfg["looks"]:
            self.toast("Save a look first, then export it")
            return
        dialog = Gtk.FileDialog(title="Export looks", initial_name="lumikey-looks.json")

        def done(d, result):
            try:
                path = d.save_finish(result).get_path()
            except GLib.Error:
                return
            with open(path, "w") as f:
                json.dump({"lumikey": "looks", "version": 1, "looks": self.cfg["looks"]}, f, indent=2)
            self.toast(f"Exported {len(self.cfg['looks'])} look{'s' * (len(self.cfg['looks']) != 1)}")
        dialog.save(self, None, done)

    def import_looks(self):
        dialog = Gtk.FileDialog(title="Import looks")

        def done(d, result):
            try:
                path = d.open_finish(result).get_path()
                with open(path) as f:
                    data = json.load(f)
                looks = data["looks"] if isinstance(data, dict) and "looks" in data else data
                good = {}
                for name, look in looks.items():
                    k.parse_spec(look["spec"], look.get("color"))  # skip anything we can't show
                    good[str(name)[:60]] = {key: look[key] for key in (*k.LOOK_FIELDS, "keys") if key in look}
            except GLib.Error:
                return
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.toast("That file doesn't contain Lumikey looks")
                return
            self.commit(looks={**self.cfg["looks"], **good})
            self.toast(f"Imported {len(good)} look{'s' * (len(good) != 1)}")
        dialog.open(self, None, done)

    def show_page(self, name):
        self.page_buttons[name].set_active(True)

    # looks

    def save_look(self):
        name = self.look_name.get_text().strip()
        if not name:
            n = 1
            while f"My look {n}" in self.cfg["looks"]:
                n += 1
            name = f"My look {n}"
        existed = name in self.cfg["looks"]
        self.commit(looks={**self.cfg["looks"], name: k.snapshot_look(self.cfg)})
        self.look_name.set_text("")
        self.toast(f"❖  {'Updated' if existed else 'Saved'} “{name}”")

    def apply_look(self, name):
        looks = k.all_looks(self.cfg)
        if name in looks:
            self.commit(**k.apply_look(self.cfg, looks[name]))
            self.toast(f"❖  {name}")

    def forget_look(self, name):
        look = self.cfg["looks"].get(name)
        if look is None:
            return
        self.commit(looks={n: v for n, v in self.cfg["looks"].items() if n != name})
        self.toast(f"Deleted “{name}”", "Undo", lambda: self.commit(looks={**self.cfg["looks"], name: look}))

    # per-key painting

    def set_brush(self, value):
        self.brush = value
        custom = value if value and value not in k.COLORS else None
        for name, b in self.brushes.items():
            on = name == (value if value in k.COLORS else "custom" if custom else "eraser")
            (b.add_css_class if on else b.remove_css_class)("active")
        self.brush_css.load_from_string(f".sw-brush {{ background: #{custom}; }}" if custom else "")

    def on_brush(self, _button, name):
        if name == "custom":
            dialog = Gtk.ColorDialog(title="Pick a brush colour", with_alpha=False)
            dialog.choose_rgba(self, None, None, self.on_custom_brush)
            return
        self.set_brush(None if name == "eraser" else name)

    def on_custom_brush(self, dialog, result):
        try:
            c = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return  # cancelled
        self.set_brush(hexcolor((c.red, c.green, c.blue)))

    def checkpoint(self):
        """Remember the pinned keys before a change, for Undo."""
        self.history.append(dict(self.cfg["keys"]))
        del self.history[:-50]

    def set_keys(self, keys, wake=False):
        """Save pinned keys; the daemon redraws within a frame without restarting the effect."""
        changes = {"keys": keys}
        if wake and self.cfg["spec"] in ("auto", "off"):  # painted keys only show over a Lumikey effect
            r = self.cfg.get("resume") or {}
            if r.get("spec") in (None, "auto", "off"):
                r = {"spec": "galaxy", "color": None}
            changes.update(spec=r["spec"], color=r["color"])
        if changes != {"keys": self.cfg["keys"]}:
            self.commit(**changes)

    def paint(self, indices, color):
        keys = dict(self.cfg["keys"])
        for i in indices:
            if color is None:
                keys.pop(str(self.lamps[i].usage), None)
            else:
                keys[str(self.lamps[i].usage)] = color
        self.set_keys(keys, wake=color is not None)

    def on_quick(self, _button, group):
        usages = set(k.KEY_GROUPS[group] or [l.usage for l in self.lamps])
        indices = [i for i, l in enumerate(self.lamps) if l.usage and l.usage in usages]
        brush = k.parse_color(self.brush) if self.brush else None
        self.checkpoint()
        if brush and all(self.pins.get(i) == brush for i in indices):
            self.paint(indices, None)  # already that colour: clear them
        else:
            self.paint(indices, self.brush)

    def on_pattern(self, _button, kind):
        brush = k.parse_color(self.brush) if self.brush else (1, 1, 1)
        keys = dict(self.cfg["keys"])
        for l in self.lamps:
            if l.usage:
                c = {"rainbow": lambda: k.hsv(l.u * 0.85), "random": lambda: k.hsv(random.random(), 0.9),
                     "gradient": lambda: k.scale(brush, 1 - 0.85 * l.u)}[kind]()
                keys[str(l.usage)] = hexcolor(c)
        self.checkpoint()
        self.set_keys(keys, wake=True)

    def clear_pins(self):
        self.checkpoint()
        self.set_keys({})

    def undo(self):
        while self.history:  # skip checkpoints of strokes that changed nothing
            keys = self.history.pop()
            if keys != self.cfg["keys"]:
                self.set_keys(keys)
                break
        self.sync_pins()

    # keyboard

    def on_key(self, _controller, keyval, _keycode, state):
        keyval = Gdk.keyval_to_lower(keyval)
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
            return True
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        pages = {Gdk.KEY_1: "effects", Gdk.KEY_2: "keys", Gdk.KEY_3: "looks", Gdk.KEY_4: "settings"}
        if keyval in pages:
            self.show_page(pages[keyval])
        elif keyval == Gdk.KEY_f:
            self.show_page("effects")
            self.search.grab_focus()
        elif keyval == Gdk.KEY_r:
            self.surprise()
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Left):
            self.step(1 if keyval == Gdk.KEY_Right else -1)
        elif keyval == Gdk.KEY_d:
            self.toggle_current_fav()
        elif keyval == Gdk.KEY_b:
            self.side_toggle.set_active(not self.side_toggle.get_active())
        elif keyval == Gdk.KEY_s:
            self.show_page("looks")
            self.look_name.grab_focus()
        elif keyval == Gdk.KEY_z:
            self.undo()
        elif keyval == Gdk.KEY_slash:
            self.show_page("settings")
            GLib.idle_add(lambda: self.settings_scroller.get_vadjustment().set_value(1e6))
        else:
            return False
        return True

    def on_close(self, *_):
        self.closed = True
        return False

    # ------------------------------------------------------------ animation

    def hero_frame(self, t):
        """effect_frame() with the pinned keys drawn in straight away, ahead of the daemon."""
        colors, live = self.effect_frame(t)
        if self.cfg["spec"] not in ("auto", "off"):
            dim = self.cfg["brightness"] / 100
            for i, c in self.pins.items():
                colors[i] = k.scale(c, dim)
        return colors, live

    def effect_frame(self, t):
        """The keyboard's real frame if the service is publishing one, else a local simulation."""
        if self.cfg["spec"] != "auto":
            try:
                if time.time() - os.stat(k.FRAME_FILE).st_mtime < 7:
                    with open(k.FRAME_FILE, "rb") as f:
                        data = f.read()
                    if len(data) == 3 * len(self.lamps):
                        return [(UNGAMMA[data[i]], UNGAMMA[data[i + 1]], UNGAMMA[data[i + 2]])
                                for i in range(0, len(data), 3)], True
            except OSError:
                pass
        dim = 0.5 if self.cfg["spec"] == "auto" else self.cfg["brightness"] / 100
        colors = k.grade(self.hero_sim.frame(t), self.cfg["saturation"] / 100, self.cfg["hue"] / 360)
        return [k.scale(c, dim) for c in colors], False

    def animate_cards(self, t):
        """Cards on screen, round-robin, within ~9 ms a pass so a big grid can't hog the CPU."""
        page = self.pages.get_visible_child_name()
        if page == "effects":
            cards, scroller = self.cards.values(), self.scroller
        elif page == "looks":
            cards, scroller = self.look_cards, self.looks_scroller
        else:
            return
        view, shown = scroller.get_height(), []
        for card in cards:
            if card.get_mapped():
                ok, bounds = card.compute_bounds(scroller)
                if ok and bounds.get_y() + bounds.get_height() > 0 and bounds.get_y() < view:
                    shown.append(card)
        start, n = time.perf_counter(), 0
        while n < len(shown):
            shown[(self.rr + n) % len(shown)].advance(t)
            n += 1
            if time.perf_counter() - start > 0.009:
                break
        self.rr = (self.rr + n) % max(1, len(shown))

    def check_access(self):
        """Show what's stopping the keyboard from lighting, with a fix button where there is one."""
        if self.setting_up:
            return
        try:
            hint = k.access_hint(self.cfg)
        except ValueError:
            hint = None
        if not hint:
            self.banner.set_revealed(False)
            return
        fixable = "lumikey setup" in hint
        self.banner.set_title(hint.split(";")[0].capitalize() if fixable else hint.capitalize())
        self.banner.set_button_label("Set up access" if fixable else None)
        self.banner.set_revealed(True)


    def on_setup(self, banner):
        self.setting_up = True
        banner.set_title("Setting up: enter your password in the dialog...")
        banner.set_button_label(None)
        proc = subprocess.Popen([sys.executable, "-m", "lumikey", "setup"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

        def done(pid, status):
            self.setting_up = False
            GLib.spawn_close_pid(pid)
            self.refresh_setup()
            if not self.banner.get_revealed():
                self.toast("All set: your keyboard is ready")
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, done)

    def tick(self):
        if self.closed:
            return False
        t = time.monotonic() - self.t0
        self.ticks += 1
        self.music.update(t)
        if self.ticks % 90 == 1:
            self.check_access()
        if k.config_mtime() != self.cfg_mtime:  # changed elsewhere (lumikey on the command line, auto-cycle)
            self.cfg, self.cfg_mtime = k.load_config(), k.config_mtime()
            self.sync_from_config()
        colors, live = self.hero_frame(t)
        if self.side.get_child_revealed():
            self.stage.set_colors(colors)
        if self.pages.get_visible_child_name() == "keys":
            self.editor.set_colors(colors)
        self.live.set_visible(live)
        if live:
            self.live_dot.set_opacity(0.6 + 0.4 * math.cos(t * 4.5))
        if self.is_active() and self.cfg["previews"] and self.ticks % 2 == 0:
            self.animate_cards(t)
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS + extra_css())
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.custom_css = Gtk.CssProvider()  # the custom-colour swatch, updated as it changes
        Gtk.StyleContext.add_provider_for_display(display, self.custom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.brush_css = Gtk.CssProvider()  # the custom brush swatch on the Per-key page
        Gtk.StyleContext.add_provider_for_display(display, self.brush_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def do_activate(self):
        if self.win and not self.win.closed:  # launched again (SUPER+K): toggle
            if self.win.is_active():
                self.win.close()
            else:
                self.win.present()
            return
        self.win = Window(self)
        self.win.present()


def main():
    App().run(None)


if __name__ == "__main__":
    main()
