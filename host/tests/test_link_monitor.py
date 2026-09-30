"""Example tests for power_monitor.link_monitor (design.md, "link_monitor.py")."""

from typing import Final, NamedTuple

from hypothesis import given
from hypothesis import strategies as st

from power_monitor.link_monitor import SEQ_MODULUS
from power_monitor.link_monitor import LinkMonitor, LinkStats, format_summary
from power_monitor.protocol import (
    FLAG_ENERGY_SATURATED,
    FLAG_OVERRANGE,
    FLAG_SENSOR_ERROR,
    Sample,
    flag_names,
)

FRAME_PERIOD_MS = 100  # the default 10 Hz firmware rate
RESERVED_BIT5 = 1 << 5


def make_sample(seq: int, timestamp_ms: int, status_flags: int = 0) -> Sample:
    # Only seq, Timestamp and flags matter to LinkMonitor; the rest is fixed.
    return Sample(
        seq=seq,
        timestamp_ms=timestamp_ms,
        status_flags=status_flags,
        bus_voltage_mv=5000,
        current_ua=100_000,
        power_mw=500,
        energy_uwh=0,
    )


def feed_seqs(monitor: LinkMonitor, seqs: list[int], start_ms: int = 1_000_000) -> None:
    # Timestamps always increase here, so no gap can look like a reset.
    for i, seq in enumerate(seqs):
        monitor.update(make_sample(seq, start_ms + i * FRAME_PERIOD_MS))


def test_consecutive_stream_has_no_drops() -> None:
    monitor = LinkMonitor()
    feed_seqs(monitor, [10, 11, 12, 13, 14])
    assert monitor.stats == LinkStats(frames_received=5, dropped_frames=0, device_resets=0)


def test_gap_of_three_counts_three() -> None:
    monitor = LinkMonitor()
    feed_seqs(monitor, [10, 14])  # 11, 12, 13 missing
    assert monitor.stats == LinkStats(frames_received=2, dropped_frames=3, device_resets=0)


def test_normal_wrap_is_not_a_drop() -> None:
    monitor = LinkMonitor()
    feed_seqs(monitor, [65534, 65535, 0, 1])
    assert monitor.stats == LinkStats(frames_received=4, dropped_frames=0, device_resets=0)


def test_gap_across_wrap_counts_missing_frames() -> None:
    monitor = LinkMonitor()
    feed_seqs(monitor, [65534, 1])  # 65535 and 0 missing
    assert monitor.stats == LinkStats(frames_received=2, dropped_frames=2, device_resets=0)


def test_single_drop_across_wrap() -> None:
    monitor = LinkMonitor()
    feed_seqs(monitor, [65535, 1])  # only 0 missing
    assert monitor.stats.dropped_frames == 1


def test_reset_counts_one_reset_and_no_drops() -> None:
    monitor = LinkMonitor()
    monitor.update(make_sample(500, 50_000))
    monitor.update(make_sample(0, 3))  # seq 0 and the Timestamp went backwards
    assert monitor.stats == LinkStats(frames_received=2, dropped_frames=0, device_resets=1)

    # The reset Frame is the new baseline: 0 -> 1 is normal, 1 -> 3 drops one.
    monitor.update(make_sample(1, 103))
    monitor.update(make_sample(3, 303))
    assert monitor.stats == LinkStats(frames_received=4, dropped_frames=1, device_resets=1)


def test_seq_zero_with_later_timestamp_is_drops_not_reset() -> None:
    monitor = LinkMonitor()
    monitor.update(make_sample(65530, 50_000))
    monitor.update(make_sample(0, 50_600))  # 65531..65535 missing, no restart
    assert monitor.stats == LinkStats(frames_received=2, dropped_frames=5, device_resets=0)


def test_known_limitation_reset_right_after_65535_is_not_seen() -> None:
    # design.md, Known limitations, "Reset at seq 65535": the new seq 0 equals
    # the expected next seq, so the reset check never runs. No reset is
    # counted, and (just as important) no false drops either.
    monitor = LinkMonitor()
    monitor.update(make_sample(65535, 50_000))
    monitor.update(make_sample(0, 3))
    assert monitor.stats == LinkStats(frames_received=2, dropped_frames=0, device_resets=0)


def test_flag_set_then_cleared() -> None:
    monitor = LinkMonitor()
    assert monitor.update(make_sample(0, 0)) == []
    assert monitor.update(make_sample(1, 100, FLAG_SENSOR_ERROR)) == ["warning: SENSOR_ERROR set"]
    # Still set: no repeated warning.
    assert monitor.update(make_sample(2, 200, FLAG_SENSOR_ERROR)) == []
    assert monitor.update(make_sample(3, 300)) == ["info: SENSOR_ERROR cleared"]


def test_reserved_bit_is_reported_by_position() -> None:
    monitor = LinkMonitor()
    assert monitor.update(make_sample(0, 0, RESERVED_BIT5)) == ["warning: RESERVED_BIT5 set"]
    assert monitor.update(make_sample(1, 100)) == ["info: RESERVED_BIT5 cleared"]


def test_two_bits_change_in_one_sample_ascending_bit_order() -> None:
    monitor = LinkMonitor()
    monitor.update(make_sample(0, 0, FLAG_OVERRANGE))
    # Bit 0 sets and bit 1 clears in the same Sample.
    assert monitor.update(make_sample(1, 100, FLAG_SENSOR_ERROR)) == [
        "warning: SENSOR_ERROR set",
        "info: OVERRANGE cleared",
    ]
    # Bits 1 and 2 set together; bit 0 stays set.
    assert monitor.update(
        make_sample(2, 200, FLAG_SENSOR_ERROR | FLAG_OVERRANGE | FLAG_ENERGY_SATURATED)
    ) == ["warning: OVERRANGE set", "warning: ENERGY_SATURATED set"]


def test_first_sample_with_flags_reports_set_and_counts_nothing_else() -> None:
    monitor = LinkMonitor()
    messages = monitor.update(make_sample(1234, 99_999, FLAG_SENSOR_ERROR | FLAG_ENERGY_SATURATED))
    assert messages == ["warning: SENSOR_ERROR set", "warning: ENERGY_SATURATED set"]
    assert monitor.stats == LinkStats(frames_received=1, dropped_frames=0, device_resets=0)


def test_summary_contains_all_four_labelled_counts() -> None:
    stats = LinkStats(frames_received=6000, dropped_frames=7, device_resets=2)
    summary = format_summary(stats, checksum_errors=3)
    assert "frames received=6000" in summary
    assert "dropped frames=7" in summary
    assert "checksum errors=3" in summary
    assert "device resets=2" in summary


# --- Property 14: drop and reset counting ---------------------------------

MAX_RUNS: Final = 4  # one run plus up to three device resets
MAX_RUN_LEN: Final = 30  # far below 65536, so a run never repeats a seq
MAX_STEP_MS: Final = 1000  # Timestamp step between Frames in a run: 1..1000 ms
U32_MAX: Final = 2**32 - 1  # timestamp_ms is a u32 on the wire

# A run spans at most MAX_RUN_LEN steps (the first run may get one extra
# Frame, see below) and every run starts below the previous run's end, so no
# Timestamp can go more than this far past the first run's start.
TIMESTAMP_HEADROOM_MS: Final = MAX_RUNS * MAX_RUN_LEN * MAX_STEP_MS


class LinkStream(NamedTuple):
    samples: list[Sample]  # the Frames that were not removed, in order
    removed_count: int
    run_count: int


@st.composite
def link_streams(draw: st.DrawFn) -> LinkStream:
    """Runs of consecutive seq values separated by resets, some Frames removed."""
    run_count = draw(st.integers(min_value=1, max_value=MAX_RUNS))
    samples: list[Sample] = []
    removed_count = 0
    prev_last_ts = 0  # last Timestamp of the previous run (unused for run 0)

    for run_index in range(run_count):
        # Each later reset needs a Timestamp strictly below the run before it.
        # Keeping every run's start >= the number of resets still to come
        # leaves 1 ms of room per reset, so the draw below is never empty
        # (a run stuck at Timestamp 0 would make the next reset impossible).
        resets_after = run_count - 1 - run_index
        length = draw(st.integers(min_value=1, max_value=MAX_RUN_LEN))

        if run_index == 0:
            # Half the draws start within one run length of 65535, so runs
            # regularly cross the 65535 -> 0 wrap.
            start_seq = draw(
                st.one_of(
                    st.integers(min_value=SEQ_MODULUS - MAX_RUN_LEN, max_value=SEQ_MODULUS - 1),
                    st.integers(min_value=0, max_value=SEQ_MODULUS - 1),
                )
            )
            start_ts = draw(
                st.integers(min_value=resets_after, max_value=U32_MAX - TIMESTAMP_HEADROOM_MS)
            )
            # A reset right after seq 65535 can't be seen: the new seq 0 is
            # exactly the expected next seq, so the monitor sees a normal wrap
            # and counts no reset (design.md, Known limitations, 1 in 65,536).
            # Only this run can end at 65535 (later runs start at 0 and are
            # short). Lengthening by one Frame (it then ends at seq 0) avoids
            # that without rejecting the example, and keeps the wrap crossing.
            last_seq = (start_seq + length - 1) % SEQ_MODULUS
            if resets_after > 0 and last_seq == SEQ_MODULUS - 1:
                length += 1
        else:
            # A device reset: seq restarts at 0 and millis() restarts low.
            start_seq = 0
            start_ts = draw(st.integers(min_value=resets_after, max_value=prev_last_ts - 1))

        timestamp_ms = start_ts
        for i in range(length):
            if i > 0:
                # Strictly increasing within a run, so a seq 0 reached after
                # drops across the wrap is (correctly) not taken as a reset.
                timestamp_ms += draw(st.integers(min_value=1, max_value=MAX_STEP_MS))
            # Never remove a run's first or last Frame: a loss at a run's edge
            # looks the same as the reset itself. Losing the last Frame before
            # a reset: the reset adds zero drops, so that loss can't be
            # counted. Losing a reset run's first Frame (seq 0): the next
            # Frame is seq 1, not 0, so no reset is seen and the jump is
            # counted as drops. The stream's very first and last Frames are
            # also run edges: the monitor never sees a Frame before or after
            # them, so losing them would be invisible.
            is_interior = 0 < i < length - 1
            if is_interior and draw(st.booleans()):
                removed_count += 1
            else:
                samples.append(make_sample((start_seq + i) % SEQ_MODULUS, timestamp_ms))
        prev_last_ts = timestamp_ms

    return LinkStream(samples, removed_count, run_count)


# Feature: power-monitor, Property 14: Dropped Frames and resets are counted exactly
# Validates: Requirements 10.1, 10.2
@given(link_streams())
def test_drops_and_resets_are_counted_exactly(stream: LinkStream) -> None:
    monitor = LinkMonitor()
    for sample in stream.samples:
        monitor.update(sample)
    assert monitor.stats == LinkStats(
        frames_received=len(stream.samples),
        dropped_frames=stream.removed_count,
        device_resets=stream.run_count - 1,
    )


# --- Property 15: one message per flag state change -----------------------

MAX_FLAG_SAMPLES: Final = 30  # far below 65536, so seq never wraps here
FLAG_BITS: Final = 8  # status_flags is one byte on the wire
FLAGS_MAX: Final = 0xFF
FLAG_TEST_START_MS: Final = 1_000_000


@st.composite
def flag_sequences(draw: st.DrawFn) -> list[int]:
    """A list of status_flags values where each one is built from the one before."""
    # Fully random bytes would change about 4 bits on almost every Sample, so
    # "no change" and "exactly one bit changes" (the common cases on real
    # hardware) would rarely be tested. Drawing a transition kind first makes
    # each kind show up about a quarter of the time.
    length = draw(st.integers(min_value=0, max_value=MAX_FLAG_SAMPLES))
    values: list[int] = []
    # The first value is also built by a transition from 0, the same starting
    # point LinkMonitor uses, so "first Sample has no flags" and "first Sample
    # sets one flag" both come up often.
    prev = 0
    for _ in range(length):
        kind = draw(st.sampled_from(["same", "one bit", "several bits", "any value"]))
        if kind == "same":
            value = prev
        elif kind == "one bit":
            value = prev ^ (1 << draw(st.integers(min_value=0, max_value=FLAG_BITS - 1)))
        elif kind == "several bits":
            # A mask of 0 is allowed: it just gives another "same" step.
            value = prev ^ draw(st.integers(min_value=0, max_value=FLAGS_MAX))
        else:
            value = draw(st.integers(min_value=0, max_value=FLAGS_MAX))
        values.append(value)
        prev = value
    return values


# Feature: power-monitor, Property 15: One message per flag state change
# Validates: Requirements 10.4
@given(flag_sequences())
def test_one_message_per_flag_state_change(values: list[int]) -> None:
    monitor = LinkMonitor()
    messages: list[str] = []
    for i, flags in enumerate(values):
        # Consecutive seq and increasing Timestamps: no drops or resets, so
        # only the flag logic is under test.
        sample = make_sample(i, FLAG_TEST_START_MS + i * FRAME_PERIOD_MS, flags)
        messages += monitor.update(sample)

    # The model counts each bit on its own. Flags are independent: in one
    # Sample SENSOR_ERROR can clear while OVERRANGE sets, so "one message per
    # state change" is a rule about each bit, not about the byte as a whole.
    # Per-bit counts catch a message naming the wrong flag, a missing message,
    # or a duplicate; a single total count could miss a wrong-flag message
    # (one too few for one bit, one too many for another still adds up).
    # Bits are compared one at a time with shifts, not with the masks that
    # link_monitor.py uses, so the model doesn't just repeat the code it tests.
    set_count = [0] * FLAG_BITS
    cleared_count = [0] * FLAG_BITS
    history = [0, *values]  # LinkMonitor treats flags before the first Sample as 0
    for prev, cur in zip(history, history[1:]):
        for bit in range(FLAG_BITS):
            prev_bit = (prev >> bit) & 1
            cur_bit = (cur >> bit) & 1
            if prev_bit == 0 and cur_bit == 1:
                set_count[bit] += 1
            elif prev_bit == 1 and cur_bit == 0:
                cleared_count[bit] += 1

    for bit in range(FLAG_BITS):
        name = flag_names(1 << bit)[0]
        # Whole-string counts: "OVERRANGE set" must not also match inside some
        # other message the way a substring check could.
        assert messages.count(f"warning: {name} set") == set_count[bit], name
        assert messages.count(f"info: {name} cleared") == cleared_count[bit], name
    # Every message must be one of the ones counted above: nothing extra.
    assert len(messages) == sum(set_count) + sum(cleared_count)

    assert monitor.stats == LinkStats(
        frames_received=len(values), dropped_frames=0, device_resets=0
    )
