# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""Every effect and layer must render on any layout a LampArray device might report."""
import math
import random

from lumikey import core as k

PITCH = k.PITCH


class FakeMusic:
    def __init__(self):
        self.bands = [0.5] * k.Audio.NBANDS
        self.level = self.left = self.right = self.beat = self.bass = self.mid = self.treble = 0.5
        self.beats, self.strength, self.centroid, self.sensitivity = 0, 0.8, 0.4, 1.0

    def update(self, t):
        beat, ph = divmod(t, 0.5)
        self.beats, self.beat = int(beat), math.exp(-ph * 9)
        self.bands = [abs(math.sin(t * 2 + i)) for i in range(k.Audio.NBANDS)]

    def close(self):
        pass


def grid(cols, rows, usage=True):
    raw = [(c * PITCH, r * PITCH, (4 + r * cols + c) % 200 if usage else 0) for r in range(rows) for c in range(cols)]
    w = max(x for x, _, _ in raw) or 1
    h = max(y for _, y, _ in raw) or 1
    return [k.Lamp(x, y, x / w, y / h, u) for x, y, u in raw]


LAYOUTS = {
    "single lamp": grid(1, 1),
    "one-row strip": grid(24, 1, usage=False),
    "one column": grid(1, 8, usage=False),
    "75% keyboard": grid(16, 6),
    "full size": grid(22, 6),
    "random scatter": [k.Lamp(x, y, x / 400, y / 150, 0) for x, y in
                       ((random.uniform(0, 400), random.uniform(0, 150)) for _ in range(60))],
}


def run(spec, lamps):
    base_cls, color, layers = k.parse_spec(spec, None)
    music = FakeMusic()
    effects = [base_cls(lamps, color), *(cls(lamps, c) for cls, c in layers)]
    for e in effects:
        e.audio = music
    for n in range(60):
        t = n / 30
        music.update(t)
        if n % 7 == 0:
            for e in effects:
                e.on_key(random.randrange(len(lamps)), t)
        colors = list(effects[0].render(t))
        for layer in effects[1:]:
            layer.apply(colors, t)
        assert len(colors) == len(lamps)
        for c in colors:
            assert all(math.isfinite(v) for v in c), (spec, c)
            k.to_led(c, 1.0)


def test_all_effects_on_all_layouts():
    failures = []
    for name, lamps in LAYOUTS.items():
        for spec in [*k.EFFECTS, *(f"galaxy+{layer}" for layer in k.LAYERS)]:
            try:
                run(spec, lamps)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{spec} on {name}: {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures)


if __name__ == "__main__":
    test_all_effects_on_all_layouts()
    print("all effects OK on", len(LAYOUTS), "layouts")
