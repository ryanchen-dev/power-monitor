"""Frame format: constants, the Sample type, CRC-8, and Frame encode/decode.

docs/packet_format.md is the authoritative definition of the Frame. This
module is the host's copy of it; the Golden_Vector tests check that the two
(and the firmware's pm_frame.c) agree byte for byte.
"""

import struct
from dataclasses import dataclass
from typing import Final

SYNC: Final = b"\xAA\x55"
FRAME_LEN_BYTES: Final = 25

# `<` means little-endian, standard sizes (H = 2, I = 4, B = 1, i = 4 bytes),
# and no padding. Without it, struct uses the host CPU's native byte order
# and C-style alignment, and would insert padding before the 4-byte fields,
# so the layout would stop matching the Frame. Fields, in Frame order:
# seq, timestamp_ms, status_flags, bus_voltage_mv, current_ua, power_mw,
# energy_uwh (21 bytes).
PAYLOAD_STRUCT: Final = struct.Struct("<HIBHiii")

CRC8_POLY: Final = 0x07
CRC8_INIT: Final = 0x00

FLAG_SENSOR_ERROR: Final = 0x01
FLAG_OVERRANGE: Final = 0x02
FLAG_ENERGY_SATURATED: Final = 0x04

# Frame byte offsets (see the layout table in docs/packet_format.md). Public
# so the streaming parser can check the length byte without re-deriving them.
LENGTH_OFFSET: Final = 2
PAYLOAD_OFFSET: Final = 3
CRC_START_OFFSET: Final = 2  # CRC covers the length byte, not the sync bytes
CRC_OFFSET: Final = FRAME_LEN_BYTES - 1

_BYTE_MASK: Final = 0xFF
_CRC8_TOP_BIT_MASK: Final = 0x80  # bit 7, the x^7 coefficient of the remainder
_BITS_PER_BYTE: Final = 8

# Wire type limits for each struct code in PAYLOAD_STRUCT.
_U8_MAX: Final = 0xFF
_U16_MAX: Final = 0xFFFF
_U32_MAX: Final = 0xFFFF_FFFF
_I32_MIN: Final = -(2**31)
_I32_MAX: Final = 2**31 - 1

# Inclusive (min, max) per Sample field, in Frame order.
_FIELD_RANGES: Final[dict[str, tuple[int, int]]] = {
    "seq": (0, _U16_MAX),
    "timestamp_ms": (0, _U32_MAX),
    "status_flags": (0, _U8_MAX),
    "bus_voltage_mv": (0, _U16_MAX),
    "current_ua": (_I32_MIN, _I32_MAX),
    "power_mw": (_I32_MIN, _I32_MAX),
    "energy_uwh": (_I32_MIN, _I32_MAX),
}

_FLAG_NAMES: Final[dict[int, str]] = {
    FLAG_SENSOR_ERROR: "SENSOR_ERROR",
    FLAG_OVERRANGE: "OVERRANGE",
    FLAG_ENERGY_SATURATED: "ENERGY_SATURATED",
}


# frozen: a decoded Sample is a record of what the device sent, so it should
# not change afterwards. Frozen dataclasses also get __eq__ and __hash__, so
# tests can compare Samples directly and put them in sets.
# slots: no per-instance __dict__, which saves memory at 50 Samples/s and
# turns a misspelled attribute into an error instead of a silent new field.
@dataclass(frozen=True, slots=True)
class Sample:
    # Field order must match PAYLOAD_STRUCT: decode_frame builds a Sample
    # positionally from the unpacked tuple.
    seq: int
    timestamp_ms: int
    status_flags: int
    bus_voltage_mv: int
    current_ua: int
    power_mw: int
    energy_uwh: int


def crc8(data: bytes) -> int:
    """CRC-8/SMBUS, bitwise, same algorithm as lib/pm_logic/src/pm_crc8.c."""
    crc = CRC8_INIT
    for byte in data:
        crc ^= byte
        for _ in range(_BITS_PER_BYTE):
            # One long-division step (see pm_crc8.c). Python ints never
            # overflow, so the mask stands in for C's uint8_t cast and drops
            # the cancelled x^8 term.
            if crc & _CRC8_TOP_BIT_MASK:
                crc = ((crc << 1) ^ CRC8_POLY) & _BYTE_MASK
            else:
                crc = (crc << 1) & _BYTE_MASK
    return crc


def _check_ranges(sample: Sample) -> None:
    # Checked here, not left to struct: struct raises struct.error (not a
    # ValueError) with a message that doesn't name the field. Masking values
    # to fit would be worse: a bug upstream would be sent as a wrong but
    # valid-looking Frame. A clear ValueError points at the actual field.
    for name, (low, high) in _FIELD_RANGES.items():
        value = getattr(sample, name)
        if not low <= value <= high:
            raise ValueError(f"{name}={value} is outside its wire range {low}..{high}")


def encode_frame(sample: Sample) -> bytes:
    """Build the 25-byte Frame for `sample`. Raises ValueError if a field doesn't fit."""
    _check_ranges(sample)
    frame = bytearray(SYNC)
    frame.append(FRAME_LEN_BYTES)
    frame += PAYLOAD_STRUCT.pack(
        sample.seq,
        sample.timestamp_ms,
        sample.status_flags,
        sample.bus_voltage_mv,
        sample.current_ua,
        sample.power_mw,
        sample.energy_uwh,
    )
    frame.append(crc8(bytes(frame[CRC_START_OFFSET:CRC_OFFSET])))
    return bytes(frame)


def decode_frame(frame: bytes) -> Sample:
    """Check and decode one complete Frame. Raises ValueError if any check fails."""
    if len(frame) != FRAME_LEN_BYTES:
        raise ValueError(f"frame is {len(frame)} bytes, expected {FRAME_LEN_BYTES}")
    if frame[:LENGTH_OFFSET] != SYNC:
        raise ValueError(f"bad sync bytes {frame[:LENGTH_OFFSET].hex(' ')}, expected {SYNC.hex(' ')}")
    if frame[LENGTH_OFFSET] != FRAME_LEN_BYTES:
        raise ValueError(f"bad length byte {frame[LENGTH_OFFSET]}, expected {FRAME_LEN_BYTES}")
    computed_crc = crc8(frame[CRC_START_OFFSET:CRC_OFFSET])
    if computed_crc != frame[CRC_OFFSET]:
        raise ValueError(
            f"CRC mismatch: computed 0x{computed_crc:02X}, frame has 0x{frame[CRC_OFFSET]:02X}"
        )
    return Sample(*PAYLOAD_STRUCT.unpack(frame[PAYLOAD_OFFSET:CRC_OFFSET]))


def flag_names(status_flags: int) -> list[str]:
    """Names of the set bits in `status_flags`, lowest bit first."""
    # Reserved bits get a name instead of being ignored: firmware never sets
    # them, so seeing one means a firmware bug or a newer Frame format, and
    # the user should be told rather than have it silently hidden.
    names: list[str] = []
    for bit in range(_BITS_PER_BYTE):
        mask = 1 << bit
        if status_flags & mask:
            names.append(_FLAG_NAMES.get(mask, f"RESERVED_BIT{bit}"))
    return names
