"""Tests for power_monitor.protocol against the Packet_Spec (docs/packet_format.md)."""

import dataclasses

import pytest

from power_monitor.protocol import (
    CRC_OFFSET,
    FLAG_ENERGY_SATURATED,
    FLAG_OVERRANGE,
    FLAG_SENSOR_ERROR,
    FRAME_LEN_BYTES,
    LENGTH_OFFSET,
    PAYLOAD_STRUCT,
    Sample,
    crc8,
    decode_frame,
    encode_frame,
    flag_names,
)

# Golden_Vectors copied from docs/packet_format.md. Keep one bytes.fromhex(...)
# literal per vector, on one line, in order GV1, GV2, GV3: the drift test
# (test_golden_vectors_sync.py) reads these literals as text and compares them
# with the doc and the Unity test.
GOLDEN_VECTORS: list[tuple[Sample, bytes]] = [
    (
        Sample(
            seq=0x1234,
            timestamp_ms=123_456_789,
            status_flags=0x00,
            bus_voltage_mv=5000,
            current_ua=123_500,
            power_mw=618,
            energy_uwh=7_654_321,
        ),
        bytes.fromhex("AA 55 19 34 12 15 CD 5B 07 00 88 13 6C E2 01 00 6A 02 00 00 B1 CB 74 00 77"),
    ),
    (
        Sample(
            seq=65535,
            timestamp_ms=4_294_967_196,
            status_flags=FLAG_OVERRANGE | FLAG_ENERGY_SATURATED,
            bus_voltage_mv=3700,
            current_ua=-1_045_000,
            power_mw=-3867,
            energy_uwh=-2_147_483_647,
        ),
        bytes.fromhex("AA 55 19 FF FF 9C FF FF FF 06 74 0E F8 0D F0 FF E5 F0 FF FF 01 00 00 80 6B"),
    ),
    (
        Sample(
            seq=0,
            timestamp_ms=100,
            status_flags=FLAG_SENSOR_ERROR,
            bus_voltage_mv=0,
            current_ua=0,
            power_mw=0,
            energy_uwh=0,
        ),
        bytes.fromhex("AA 55 19 00 00 64 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 9E"),
    ),
]
GV_IDS = ["GV1", "GV2", "GV3"]

VALID_SAMPLE, VALID_FRAME = GOLDEN_VECTORS[0]

# Each field's wire range, written out here rather than imported so the test
# is an independent check of protocol.py's limits.
U8_MAX = 0xFF
U16_MAX = 0xFFFF
U32_MAX = 0xFFFF_FFFF
I32_MIN = -(2**31)
I32_MAX = 2**31 - 1
OUT_OF_RANGE_CASES: list[tuple[str, int]] = [
    ("seq", -1),
    ("seq", U16_MAX + 1),
    ("timestamp_ms", -1),
    ("timestamp_ms", U32_MAX + 1),
    ("status_flags", -1),
    ("status_flags", U8_MAX + 1),
    ("bus_voltage_mv", -1),
    ("bus_voltage_mv", U16_MAX + 1),
    ("current_ua", I32_MIN - 1),
    ("current_ua", I32_MAX + 1),
    ("power_mw", I32_MIN - 1),
    ("power_mw", I32_MAX + 1),
    ("energy_uwh", I32_MIN - 1),
    ("energy_uwh", I32_MAX + 1),
]


def with_byte(frame: bytes, offset: int, value: int) -> bytes:
    """Copy of `frame` with one byte replaced."""
    changed = bytearray(frame)
    changed[offset] = value
    return bytes(changed)


@pytest.mark.parametrize(("sample", "frame"), GOLDEN_VECTORS, ids=GV_IDS)
def test_golden_vector_encodes_to_its_bytes(sample: Sample, frame: bytes) -> None:
    assert encode_frame(sample) == frame


@pytest.mark.parametrize(("sample", "frame"), GOLDEN_VECTORS, ids=GV_IDS)
def test_golden_vector_decodes_to_its_sample(sample: Sample, frame: bytes) -> None:
    assert decode_frame(frame) == sample


def test_crc8_check_value() -> None:
    # Standard check value for CRC-8/SMBUS (reveng catalogue).
    assert crc8(b"123456789") == 0xF4


def test_sizes_match_packet_spec() -> None:
    assert PAYLOAD_STRUCT.size == 21
    assert FRAME_LEN_BYTES == 25


@pytest.mark.parametrize(("field", "value"), OUT_OF_RANGE_CASES)
def test_encode_rejects_out_of_range_field(field: str, value: int) -> None:
    bad = dataclasses.replace(VALID_SAMPLE, **{field: value})
    with pytest.raises(ValueError, match=field):
        encode_frame(bad)


# Each case uses `match` so the test proves the intended check fired. A bad
# sync byte isn't covered by the CRC, and a bad length byte also breaks the CRC,
# so without `match` a wrong check order or a missing check could still pass.
# 0xAB and 0x54 are the sync bytes 0xAA and 0x55 with bit 0 flipped.
@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (with_byte(VALID_FRAME, 0, 0xAB), "sync"),
        (with_byte(VALID_FRAME, 1, 0x54), "sync"),
        (with_byte(VALID_FRAME, LENGTH_OFFSET, FRAME_LEN_BYTES + 1), "length byte"),
        (with_byte(VALID_FRAME, CRC_OFFSET, VALID_FRAME[CRC_OFFSET] ^ 0x01), "CRC"),
        (VALID_FRAME[:-1], "frame is 24 bytes"),
        (VALID_FRAME + b"\x00", "frame is 26 bytes"),
    ],
    ids=["bad_sync0", "bad_sync1", "bad_length_byte", "bad_crc", "too_short", "too_long"],
)
def test_decode_rejects_bad_frame(frame: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_frame(frame)


@pytest.mark.parametrize(
    ("status_flags", "names"),
    [
        (0x00, []),
        (0x06, ["OVERRANGE", "ENERGY_SATURATED"]),
        (0x20, ["RESERVED_BIT5"]),
    ],
)
def test_flag_names(status_flags: int, names: list[str]) -> None:
    assert flag_names(status_flags) == names
