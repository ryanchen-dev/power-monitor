"""Example tests for power_monitor.parser (design.md, "Parsing rules (Frame_Parser)")."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from power_monitor.parser import FrameParser
from power_monitor.protocol import (
    CRC_OFFSET,
    CRC_START_OFFSET,
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


BITS_PER_BYTE = 8
# CRC_START_OFFSET is 2, the length byte: the first byte after the two sync
# bytes. So the checked bits run from the length byte (bit 16) to the last
# bit of the CRC byte (bit 199).
FIRST_CHECKED_BIT = CRC_START_OFFSET * BITS_PER_BYTE
LAST_CHECKED_BIT = FRAME_LEN_BYTES * BITS_PER_BYTE - 1


# Feature: power-monitor, Property 12: Single-bit errors are rejected and counted
# Validates: Requirements 9.2, 9.6
# Why the sync bytes are excluded: flipping a sync bit means the bytes never
# form an AA 55 candidate, so the parser drops them as garbage (rules 1-2),
# which by design is not a checksum error. The property would wrongly fail
# there. Corrupted sync is covered by resynchronization (Property 13) instead.
# Why the CRC catches every single-bit error: a one-bit error adds the
# polynomial x^k to the message. The generator x^8 + x^2 + x + 1 has more than
# one term, so it can never divide x^k exactly; the remainder is never zero,
# so the CRC no longer matches. (A flipped length bit is caught even earlier,
# by rule 3.)
@given(valid_samples(), st.integers(FIRST_CHECKED_BIT, LAST_CHECKED_BIT))
def test_single_bit_error_is_rejected_and_counted(sample: Sample, bit: int) -> None:
    frame = bytearray(encode_frame(sample))
    frame[bit // BITS_PER_BYTE] ^= 1 << (bit % BITS_PER_BYTE)

    parser = FrameParser()
    assert parser.feed(bytes(frame)) == []
    # >= 1, not == 1: after the bad candidate is rejected, its remaining bytes
    # are rescanned and may hold a false AA 55 in the data, which is counted too.
    assert parser.checksum_errors >= 1


MAX_FRAMES = 5
# A bit over one Frame (25 bytes), so a garbage run can be longer than a Frame.
MAX_GARBAGE_LEN_BYTES = 30
# Enough cut points to make many 1-byte and empty reads in a stream of up to
# 5 Frames plus 6 garbage runs (at most 305 bytes).
MAX_CUTS = 40
BYTE_VALUE_COUNT = 256  # a byte takes values 0x00-0xFF

# Why garbage never contains 0xAA: a 0xAA in garbage can start a false AA 55
# candidate, which the parser rightly rejects and counts as a checksum error.
# Req 9.7 promises zero errors only for garbage with no sync first byte, so
# garbage with 0xAA belongs to a different property. The Frames themselves may
# still hold 0xAA in their data: once the parser is lined up on a real sync,
# it removes whole Frames, so data bytes are never scanned for a sync.
# Built from the allowed bytes directly instead of filtering out 0xAA, so
# hypothesis never has to throw away a drawn example.
NON_SYNC_BYTES = [b for b in range(BYTE_VALUE_COUNT) if b != SYNC[0]]


def garbage() -> st.SearchStrategy[bytes]:
    """Byte strings (possibly empty) that contain no 0xAA."""
    return st.lists(st.sampled_from(NON_SYNC_BYTES), max_size=MAX_GARBAGE_LEN_BYTES).map(bytes)


@st.composite
def garbled_streams(draw: st.DrawFn) -> tuple[list[Sample], list[bytes]]:
    """Samples, and their Frames with garbage around them, cut into reads."""
    samples = draw(st.lists(valid_samples(), min_size=0, max_size=MAX_FRAMES))
    # One garbage run before each Frame and one after the last: len + 1 runs.
    runs = draw(st.lists(garbage(), min_size=len(samples) + 1, max_size=len(samples) + 1))
    stream = runs[0] + b"".join(encode_frame(s) + run for s, run in zip(samples, runs[1:]))

    # Why random split points: a serial read returns however many bytes have
    # arrived so far (any count, even 0 on a timeout), so reads don't break on
    # Frame boundaries. Cutting anywhere checks that the parser doesn't depend
    # on where reads happen to break. Repeated cuts give empty reads, adjacent
    # cuts give 1-byte reads, and no cuts gives the whole stream in one read.
    cuts = sorted(draw(st.lists(st.integers(0, len(stream)), max_size=MAX_CUTS)))
    bounds = [0, *cuts, len(stream)]
    chunks = [stream[a:b] for a, b in zip(bounds, bounds[1:])]
    assert b"".join(chunks) == stream  # guards against a bug in this generator
    return samples, chunks


# Feature: power-monitor, Property 13: The parser resynchronizes across garbage and split reads
# Validates: Requirements 9.3, 9.4, 9.7
@given(garbled_streams())
def test_parser_resynchronizes_across_garbage_and_split_reads(
    case: tuple[list[Sample], list[bytes]],
) -> None:
    samples, chunks = case
    parser = FrameParser()
    received: list[Sample] = []
    for chunk in chunks:
        received += parser.feed(chunk)
        assert buffered_len(parser) <= MAX_BUFFERED_BYTES
    assert received == samples
    assert parser.checksum_errors == 0
