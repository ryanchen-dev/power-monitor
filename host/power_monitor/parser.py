"""Streaming Frame_Parser: turns arbitrary chunks of UART bytes into Samples.

Implements the six parsing rules in design.md, "Parsing rules (Frame_Parser)".
The Frame layout itself (sync bytes, offsets, CRC) comes from protocol.py and
is not repeated here.

Concept: sync-byte resynchronization. A serial port delivers a plain stream of
bytes with no marker saying "a Frame starts here", and the parser may start
listening mid-Frame or lose bytes to noise. So every AA 55 in the stream is
treated as a *candidate* Frame start, and it is accepted only if the length
byte and the CRC also check out. AA 55 can appear inside data too, which is
why those two extra checks are needed. Once one real Frame is accepted, the
parser removes whole Frames and stays lined up with the sender.
"""

from typing import Final

from power_monitor.protocol import (
    CRC_OFFSET,
    CRC_START_OFFSET,
    FRAME_LEN_BYTES,
    LENGTH_OFFSET,
    SYNC,
    Sample,
    crc8,
    decode_frame,
)

# Bytes needed before each rule can make its check.
_SYNC_LEN_BYTES: Final = len(SYNC)  # rule 2: both sync bytes
_HEADER_LEN_BYTES: Final = LENGTH_OFFSET + 1  # rule 3: sync bytes + length byte


class FrameParser:
    """Buffers bytes across reads and returns each valid Frame as a Sample."""

    def __init__(self) -> None:
        # Rejected candidates: wrong length byte or wrong CRC (Req 9.2).
        self.checksum_errors: int = 0
        # Invariant: between feed() calls this holds at most FRAME_LEN_BYTES - 1
        # (24) bytes. The loop in feed() only stops in two places: rule 1 after
        # emptying the buffer, or a "wait" in rules 2-4, each of which needs
        # fewer than 25 bytes to be present. With 25 or more bytes starting at
        # 0xAA, rules 2-6 always either reject a candidate or emit a Frame, so
        # the loop keeps going. That keeps memory bounded whatever is received.
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Sample]:
        """Add `data` to the buffer and return the Samples it completes, in order."""
        buf = self._buffer
        buf += data
        samples: list[Sample] = []

        while True:
            # Rule 1: bytes before the first 0xAA can't start a Frame (Req 9.3).
            start = buf.find(SYNC[0])
            if start < 0:
                buf.clear()
                break
            # Deleting from the front shifts the rest down, but the buffer is
            # at most 24 bytes plus one read, so the copy is cheap.
            del buf[:start]

            # Rule 2. A lone 0xAA is ordinary garbage, not a failed Frame, so
            # it is not counted as a checksum error.
            if len(buf) < _SYNC_LEN_BYTES:
                break
            if buf[1] != SYNC[1]:
                del buf[0]
                continue

            # Rule 3. The length byte is checked as soon as it arrives, before
            # waiting for all 25 bytes: an AA 55 that happens to appear inside
            # data is then rejected after 3 bytes instead of 25, and the parser
            # doesn't sit waiting to fill a candidate that was never a Frame.
            # A wrong length counts as a checksum error because Req 9.2 treats
            # both as "this candidate failed its integrity check".
            if len(buf) < _HEADER_LEN_BYTES:
                break
            if buf[LENGTH_OFFSET] != FRAME_LEN_BYTES:
                self._reject_candidate()
                continue

            # Rule 4: wait for the rest of the Frame (Req 9.4).
            if len(buf) < FRAME_LEN_BYTES:
                break

            # Rule 5 (Req 9.2).
            if crc8(bytes(buf[CRC_START_OFFSET:CRC_OFFSET])) != buf[CRC_OFFSET]:
                self._reject_candidate()
                continue

            # Rule 6 (Req 9.1). decode_frame repeats the sync, length, and CRC
            # checks, so it cannot raise here: rules 2-5 have just passed them.
            # Reusing it keeps the unpacking in one place (protocol.py).
            samples.append(decode_frame(bytes(buf[:FRAME_LEN_BYTES])))
            del buf[:FRAME_LEN_BYTES]

        return samples

    def _reject_candidate(self) -> None:
        # Drop only the candidate's first byte, not all 25. The candidate may
        # be a false AA 55 inside data, or a real Frame cut short by lost
        # bytes; either way a real Frame can start inside the rejected bytes.
        # Rule 1 then rescans from the next byte and finds it (Req 9.2).
        self.checksum_errors += 1
        del self._buffer[0]
