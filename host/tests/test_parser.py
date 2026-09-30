"""Example tests for power_monitor.parser (design.md, "Parsing rules (Frame_Parser)")."""

import pytest
from hypothesis import given

from power_monitor.parser import FrameParser
from power_monitor.protocol import (
    CRC_OFFSET,
    FRAME_LEN_BYTES,
    LENGTH_OFFSET,
    SYNC,
    Sample,
    encode_frame,
)
from strategies import valid_samples

# Frames are built with encode_frame (checked against the Golden_Vectors in
# test_protocol.py), so this file has no hand-written Frame bytes to drift.
SAMPLE_A = Sample(
    seq=0x1234,
    timestamp_ms=123_456_789,
    status_flags=0x00,
    bus_voltage_mv=5000,
    current_ua=123_500,
    power_mw=618,
    energy_uwh=7_654_321,
)
SAMPLE_B = Sample(
    seq=0x1235,
    timestamp_ms=123_456_809,
    status_flags=0x00,
    bus_voltage_mv=3300,
    current_ua=-250_000,
    power_mw=-825,
    energy_uwh=7_654_000,
)
FRAME_A = encode_frame(SAMPLE_A)
FRAME_B = encode_frame(SAMPLE_B)

MAX_BUFFERED_BYTES = FRAME_LEN_BYTES - 1


def with_byte(frame: bytes, offset: int, value: int) -> bytes:
    """Copy of `frame` with one byte replaced."""
    changed = bytearray(frame)
    changed[offset] = value
    return bytes(changed)


def buffered_len(parser: FrameParser) -> int:
    # Reads the private buffer on purpose: the 24-byte bound is a property of
    # the internal state, and adding a public accessor just for tests would
    # grow the design's interface.
    return len(parser._buffer)


def test_one_frame_in_one_read() -> None:
    parser = FrameParser()
    assert parser.feed(FRAME_A) == [SAMPLE_A]
    assert parser.checksum_errors == 0


def test_frame_split_across_three_reads() -> None:
    # Splits chosen so the parser waits at rule 2 (1 byte), then rule 4
    # (24 bytes), and only the third read supplies the last byte.
    parser = FrameParser()
    assert parser.feed(FRAME_A[:1]) == []
    assert parser.feed(FRAME_A[1:-1]) == []
    assert parser.feed(FRAME_A[-1:]) == [SAMPLE_A]
    assert parser.checksum_errors == 0


@pytest.mark.parametrize(
    "garbage",
    [
        b"\x00\x11\x55\x19\xFF",
        # Lone 0xAA bytes not followed by 0x55, the last one right before the
        # Frame's own AA 55. These fail rule 2, which is not a checksum error.
        b"\x01\xAA\x02\xAA",
    ],
    ids=["no_sync_byte", "lone_0xAA"],
)
def test_garbage_before_frame_is_dropped(garbage: bytes) -> None:
    parser = FrameParser()
    assert parser.feed(garbage + FRAME_A) == [SAMPLE_A]
    assert parser.checksum_errors == 0


@pytest.mark.parametrize(
    ("offset", "bad_value"),
    [
        (LENGTH_OFFSET, FRAME_LEN_BYTES - 1),
        (CRC_OFFSET, FRAME_A[CRC_OFFSET] ^ 0x01),  # flip bit 0 of the CRC
    ],
    ids=["wrong_length_byte", "bad_crc"],
)
def test_rejected_candidate_is_counted_once(offset: int, bad_value: int) -> None:
    bad = with_byte(FRAME_A, offset, bad_value)
    # After the candidate's first byte is dropped, its other 24 bytes are
    # rescanned. They contain no 0xAA, so no second false candidate can form
    # and exactly one error is expected.
    assert SYNC[0] not in bad[1:]

    parser = FrameParser()
    assert parser.feed(bad) == []
    assert parser.checksum_errors == 1
    # The parser recovers: the next valid Frame is decoded, with no new error.
    assert parser.feed(FRAME_A) == [SAMPLE_A]
    assert parser.checksum_errors == 1


def test_frame_starting_inside_rejected_candidate_is_found() -> None:
    # A Frame cut short after 10 bytes (as if the rest were lost), then a
    # whole Frame. The truncated candidate passes rules 2-3, and its 25 bytes
    # run into FRAME_B, so its CRC fails. Dropping only its first byte lets the
    # rescan find FRAME_B's AA 55 at offset 10, inside the rejected bytes.
    truncated = FRAME_A[:10]
    assert SYNC[0] not in truncated[1:]  # so the rejected candidate is the only one

    parser = FrameParser()
    assert parser.feed(truncated + FRAME_B) == [SAMPLE_B]
    assert parser.checksum_errors == 1


def test_two_back_to_back_frames_in_one_read() -> None:
    parser = FrameParser()
    assert parser.feed(FRAME_A + FRAME_B) == [SAMPLE_A, SAMPLE_B]
    assert parser.checksum_errors == 0


@pytest.mark.parametrize(
    ("data", "expected_len"),
    [
        (FRAME_A[:-1], MAX_BUFFERED_BYTES),  # waiting for the last byte
        (b"\x00" * 1000, 0),  # garbage is not kept
        (b"\x00" * 1000 + FRAME_A * 3 + FRAME_B[:-1], MAX_BUFFERED_BYTES),
    ],
    ids=["partial_frame", "long_garbage", "garbage_frames_then_partial"],
)
def test_buffer_holds_at_most_24_bytes_after_feed(data: bytes, expected_len: int) -> None:
    parser = FrameParser()
    parser.feed(data)
    assert buffered_len(parser) == expected_len
    assert buffered_len(parser) <= MAX_BUFFERED_BYTES


# Feature: power-monitor, Property 11: Encode then parse is the identity
# Validates: Requirements 9.1, 9.5
# The examples above pin down specific cases; this checks that every valid
# Sample survives the trip, including the edge values strategies.py favors.
@given(valid_samples())
def test_encode_then_parse_is_identity(sample: Sample) -> None:
    parser = FrameParser()
    assert parser.feed(encode_frame(sample)) == [sample]
    assert parser.checksum_errors == 0
