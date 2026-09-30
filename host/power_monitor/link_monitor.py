"""Link health: dropped Frames, device resets, and Status_Flags messages.

Implements the `update` steps in design.md, "link_monitor.py" (Req 10.1-10.4).
The parser counts checksum errors; this module counts everything that can
only be seen by comparing one good Frame with the one before it.
"""

from dataclasses import dataclass
from typing import Final

from power_monitor.protocol import Sample, flag_names

# seq is a u16 on the wire, so it counts modulo 2**16 (65535 wraps to 0).
SEQ_MODULUS: Final = 2**16

# status_flags is one byte on the wire.
_STATUS_FLAGS_MASK: Final = 0xFF
_STATUS_FLAGS_BITS: Final = 8


# Not frozen: unlike Sample, this is a running tally that update() changes.
@dataclass
class LinkStats:
    frames_received: int = 0
    dropped_frames: int = 0
    device_resets: int = 0


class LinkMonitor:
    def __init__(self) -> None:
        self.stats: LinkStats = LinkStats()
        self._prev: Sample | None = None

    def update(self, sample: Sample) -> list[str]:
        """Count this Sample and return its flag set/cleared messages."""
        self.stats.frames_received += 1

        prev = self._prev
        # The first Sample has nothing to compare with, so it sets the baseline
        # and counts no drops or resets.
        if prev is not None:
            expected = (prev.seq + 1) % SEQ_MODULUS
            if sample.seq != expected:
                # seq 0 alone is not enough: it also appears at the normal
                # 65535 -> 0 wrap. A Timestamp that went backwards proves the
                # firmware restarted (millis() starts again from 0). The Uno
                # also resets itself when the serial port opens (DTR), so one
                # reset at startup is normal.
                #
                # Known blind spot (1 in 65,536): if the board resets right
                # after sending seq 65535, the new seq 0 equals `expected`, so
                # this branch never runs. No reset is counted, but no false
                # drops are counted either.
                if sample.seq == 0 and sample.timestamp_ms < prev.timestamp_ms:
                    self.stats.device_resets += 1
                else:
                    # Python's % always returns a value in 0..65535, even when
                    # seq has wrapped past 0 and seq - expected is negative.
                    # E.g. 65530 -> 2: expected 65531, (2 - 65531) % 65536 = 7
                    # missing Frames (65531..65535 and 0..1).
                    self.stats.dropped_frames += (sample.seq - expected) % SEQ_MODULUS

        # Flags start from 0, so flags already set on the first Sample are
        # reported as "set".
        prev_flags = prev.status_flags if prev is not None else 0
        # Python ints have no fixed width, so ~0x01 is -2 (infinitely many 1
        # bits), not 0xFE. Masking to 8 bits keeps these as plain byte values.
        set_bits = sample.status_flags & ~prev_flags & _STATUS_FLAGS_MASK
        cleared_bits = prev_flags & ~sample.status_flags & _STATUS_FLAGS_MASK

        # One pass in ascending bit order, so when one flag sets and another
        # clears in the same Sample, the messages are ordered by bit number.
        # "cleared" is not required by Req 10.4, but without it a sensor
        # reconnect (SENSOR_ERROR clearing, Req 17.8) would be invisible.
        messages: list[str] = []
        for bit in range(_STATUS_FLAGS_BITS):
            mask = 1 << bit
            if set_bits & mask:
                messages.append(f"warning: {flag_names(mask)[0]} set")
            elif cleared_bits & mask:
                messages.append(f"info: {flag_names(mask)[0]} cleared")

        # Always move the baseline, including on a reset: the Frames after a
        # reset continue from the new seq 0, not from the old run.
        self._prev = sample
        return messages


def format_summary(stats: LinkStats, checksum_errors: int) -> str:
    """One-line summary printed when the Host_Tool exits (Req 10.3)."""
    return (
        f"link summary: frames received={stats.frames_received}, "
        f"dropped frames={stats.dropped_frames}, "
        f"checksum errors={checksum_errors}, "
        f"device resets={stats.device_resets}"
    )
