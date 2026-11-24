# Contributing to Lumikey

Thanks for helping! Here's where help matters most.

## 1. Report your keyboard (no coding needed)

Lumikey can only be tested on keyboards someone owns. If you have one with RGB:

1. Run `lumikey probe` (or, in the app: menu → *Report my keyboard*, which copies it for you).
2. Open a [new device report](../../issues/new?template=new-device.yml) and paste the output.
3. Say what works and what doesn't: lights, layout, typing effects, music effects.

A report for a device that **works perfectly** is just as useful, because it goes into the supported list.

## 2. Add a device that doesn't follow the standard

Most devices need nothing: `lumikey/lamparray.py` reads everything from the device's HID descriptor.

- **Device lists a LampArray but misbehaves:** add it to `QUIRKS` in `lamparray.py`, or fix the parser.
  Save its descriptor to `tests/descriptors/<vendor>-<model>.bin`
  (`cp /sys/class/hidraw/hidrawN/device/report_descriptor ...`) so it's tested from then on.
- **Device uses a vendor protocol** (no LampArray): this needs a new backend with the same interface as
  `LampArray` (`lamps`, `send(leds)`, `autonomous()`, `close()`). Open an issue first so we can plan it.

## 3. Make a new effect

Effects live in `lumikey/core.py`. Each one is a class:

```python
class Aurora(Effect):
    category = "nature"           # which gallery tab it shows in

    def render(self, t):          # t = seconds (already scaled by the speed setting)
        return [color_for(lamp, t) for lamp in self.lamps]   # linear (r, g, b), 0..1
```

`lamp.x` / `lamp.y` are in millimetres, and `lamp.u` / `lamp.v` are 0..1 across the board. Use those,
not key indices, so the effect works on every layout. Set `needs_audio = True` to read `self.audio`, or
`needs_keys = True` and implement `on_key(i, t)` for typing effects. Then register it in `EFFECTS`.

Before sending a pull request:

```sh
python -m pytest tests                       # every effect on six very different layouts
python docs/make_preview.py                  # optional: see it rendered
```

## Code style

- Plain Python, standard library plus numpy. The GUI uses GTK 4 + libadwaita through PyGObject.
- Keep the daemon light. It runs all day, so measure CPU (`top -p $(pgrep -f "lumikey daemon")`).
- Comments explain *why*. Names should explain *what*.

## Code of conduct

Be kind and patient. Everyone was new once.
