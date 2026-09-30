"""Example tests for power_monitor.link_monitor (design.md, "link_monitor.py")."""

from power_monitor.link_monitor import LinkMonitor, LinkStats, format_summary
from power_monitor.protocol import (
    FLAG_ENERGY_SATURATED,
    FLAG_OVERRANGE,
    FLAG_SENSOR_ERROR,
    Sample,
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
