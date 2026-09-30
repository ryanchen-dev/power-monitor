"""Shared hypothesis strategies for the host property tests.

Created for Property 11 (task 7.2). Later property tests (Properties 12 and
13, and the ctypes tests for the firmware logic) reuse valid_samples() so
every test draws Samples the same way.

Concept: property-based testing. An example test checks one input chosen by
hand. A property test states a rule that must hold for *every* valid input
("encode then parse gives back the same Sample"), and hypothesis generates
many inputs (200 here, set in conftest.py) looking for one that breaks it.
When it finds one, it *shrinks* it: it keeps trying simpler versions (smaller
numbers, earlier choices) that still fail, and reports the simplest. So a
failure reads like "seq=1, everything else 0", not a wall of random numbers.
"""

from collections.abc import Sequence
from typing import Final

from hypothesis import strategies as st

from power_monitor.protocol import (
    FLAG_ENERGY_SATURATED,
    FLAG_OVERRANGE,
    FLAG_SENSOR_ERROR,
    Sample,
)

# Wire type limits, written out rather than imported from protocol.py so the
# generators are an independent statement of the Packet_Spec's ranges.
U8_MAX: Final = 0xFF
U16_MAX: Final = 0xFFFF
U32_MAX: Final = 0xFFFF_FFFF
I32_MIN: Final = -(2**31)
I32_MAX: Final = 2**31 - 1

# The int16 sign boundary inside the u16 fields. If any code path treated a
# u16 as signed, 0x7FFF would survive and 0x8000 would come back negative.
I16_MAX: Final = 0x7FFF
I16_MIN_AS_U16: Final = 0x8000

# Rated_Range boundaries from the requirements.
RATED_BUS_VOLTAGE_MAX_MV: Final = 6000
RATED_CURRENT_MAX_UA: Final = 1_000_000

# Timestamps in the last 10 s before the u32 millisecond counter wraps
# (about 49.7 days of uptime), and the last few seq values before 65535 -> 0.
TIMESTAMP_WRAP_WINDOW_MS: Final = 10_000
SEQ_NEAR_MAX_COUNT: Final = 3  # 65533, 65534, 65535


# Why lean toward edges: bugs cluster at boundaries (sign bits, wrap-around,
# byte order of the top byte), but uniform random draws almost never land on
# them. The chance that 200 draws from the 2^32 u32 values include exactly
# U32_MAX is about 200 / 2^32, roughly 5 in 100 million. hypothesis does favor
# some boundaries on its own, but not domain values like 6000 mV, so each
# field is "edge values OR the full range" and both halves get drawn often.
# 0 is listed first because hypothesis shrinks sampled_from toward the first
# element, so shrunk failures show 0 where the field doesn't matter.
def _edge_biased(edges: Sequence[int], low: int, high: int) -> st.SearchStrategy[int]:
    return st.one_of(st.sampled_from(edges), st.integers(min_value=low, max_value=high))


def _i32_field(extra_edges: Sequence[int] = ()) -> st.SearchStrategy[int]:
    # -1 and 1 either side of 0: -1 is all 0xFF bytes in two's complement,
    # the opposite extreme from 0 at the byte level.
    edges = [0, -1, 1, I32_MIN, I32_MAX, *extra_edges]
    return _edge_biased(edges, I32_MIN, I32_MAX)


def valid_samples() -> st.SearchStrategy[Sample]:
    """Samples whose every field fits its wire type (status_flags: all 8 bits)."""
    seq_near_max = range(U16_MAX - SEQ_NEAR_MAX_COUNT + 1, U16_MAX + 1)
    timestamps = st.one_of(
        st.sampled_from([0, U32_MAX]),
        st.integers(min_value=U32_MAX - TIMESTAMP_WRAP_WINDOW_MS + 1, max_value=U32_MAX),
        st.integers(min_value=0, max_value=U32_MAX),
    )
    flags_edges = [0, U8_MAX, FLAG_SENSOR_ERROR, FLAG_OVERRANGE, FLAG_ENERGY_SATURATED]
    return st.builds(
        Sample,
        seq=_edge_biased([0, I16_MAX, I16_MIN_AS_U16, *seq_near_max], 0, U16_MAX),
        timestamp_ms=timestamps,
        status_flags=_edge_biased(flags_edges, 0, U8_MAX),
        bus_voltage_mv=_edge_biased(
            [0, I16_MAX, I16_MIN_AS_U16, RATED_BUS_VOLTAGE_MAX_MV, U16_MAX], 0, U16_MAX
        ),
        current_ua=_i32_field([RATED_CURRENT_MAX_UA, -RATED_CURRENT_MAX_UA]),
        power_mw=_i32_field(),
        energy_uwh=_i32_field(),
    )
