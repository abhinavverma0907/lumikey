# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""The descriptor parser against real devices' descriptors (tests/descriptors/*.bin)."""
import pathlib
import struct

from lumikey import lamparray as la

HERE = pathlib.Path(__file__).parent / "descriptors"


def reports(name):
    return la.parse_descriptor((HERE / f"{name}.bin").read_bytes())


def test_k510_reports():
    r = reports("lenovo-legion-k510-mini-pro")
    assert {u: (rep.id, rep.size) for u, rep in r.items()} == {
        la.ATTRIBUTES: (1, 22), la.REQUEST: (2, 2), la.RESPONSE: (3, 28),
        la.MULTI_UPDATE: (4, 50), la.RANGE_UPDATE: (5, 9), la.CONTROL: (6, 1)}
    assert sum(f.usage == la.LAMP_ID for f in r[la.MULTI_UPDATE].fields) == 8


def test_multi_update_matches_hand_packed_bytes():
    report = reports("lenovo-legion-k510-mini-pro")[la.MULTI_UPDATE]
    leds = [(i, 255 - i, i * 3 % 256) for i in range(8)]
    values = {la.LAMP_COUNT: 8, la.UPDATE_FLAGS: 1, la.LAMP_ID: list(range(8)),
              la.RED: [c[0] for c in leds], la.GREEN: [c[1] for c in leds], la.BLUE: [c[2] for c in leds],
              la.INTENSITY: [255] * 8}
    expected = struct.pack("<BB8H32B", 8, 1, *range(8), *[v for c in leds for v in (*c, 255)])
    assert la.pack(report, values) == expected


def test_lamp_response_roundtrip():
    report = reports("lenovo-legion-k510-mini-pro")[la.RESPONSE]
    values = {la.LAMP_ID: 7, la.POS_X: 19050, la.POS_Y: 38100, la.RED_LEVELS: 255, la.INPUT_BINDING: 4}
    out = la.unpack(report, la.pack(report, values))
    assert all(out[u] == v for u, v in values.items())


def test_all_sample_descriptors_parse():
    for path in HERE.glob("*.bin"):
        assert la.MULTI_UPDATE in la.parse_descriptor(path.read_bytes()), path.name


def test_non_lamparray_descriptor_is_ignored():
    keyboard = bytes.fromhex("05010906a101050719e029e71500250175019508810295017508810305071900"
                             "29ff150026ff00750895068100c0")
    assert la.parse_descriptor(keyboard) == {}
