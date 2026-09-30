"""CSV logging of decoded Samples (design.md, "csv_logger.py" and "CSV schema").

Implements Req 11.1-11.4: a header with units, one row per Sample, a flush
to disk at least once per second, and a flush and close on exit.
"""

import csv
import os
import time
from pathlib import Path
from typing import Callable, Final, TextIO

from power_monitor.protocol import Sample

# Every column after the first is also the name of a Sample field, so write()
# can look the values up by name and the row order always follows the header.
CSV_HEADER: Final = (
    "host_time_unix_s",
    "timestamp_ms",
    "seq",
    "status_flags",
    "bus_voltage_mv",
    "current_ua",
    "power_mw",
    "energy_uwh",
)
# 0.5 s, not 1.0 s: maybe_flush() only checks once per loop iteration, so the
# worst-case gap between writing a row and it reaching disk is the period plus
# one loop iteration (about 0.6 s). That keeps Req 11.3's 1 s bound with margin.
CSV_FLUSH_PERIOD_S: Final = 0.5

# Millisecond resolution, the same as the firmware's timestamp_ms.
HOST_TIME_DECIMALS: Final = 3

_SAMPLE_COLUMNS: Final = CSV_HEADER[1:]


class CsvLogger:
    def __init__(self, stream: TextIO, clock: Callable[[], float] = time.monotonic) -> None:
        # The header is not written here, only in open(). That way wrapping a
        # stream (an existing file, or a test's stream) never adds a second
        # header, and a file created by open() gets it exactly once.
        self._stream = stream
        self._writer = csv.writer(stream)
        self._clock = clock
        # Counting from construction means the first flush comes one period
        # after the log starts, not immediately on the first maybe_flush().
        self._last_flush_s = clock()

    @classmethod
    def open(cls, path: Path) -> "CsvLogger":
        """Create a new log file at `path` and write the header (Req 11.1).

        Raises FileExistsError (an OSError) if `path` already exists.
        """
        # Mode "x" (exclusive create) fails if the file exists. "w" would
        # silently wipe an earlier log, and "a" would append a second header
        # to it. OSError is not caught: the CLI reports it to the user.
        # newline="": the csv module writes its own line endings ("\r\n").
        # Without this, Python's text layer would also translate newlines,
        # which on Windows turns each "\r\n" into "\r\r\n" (a blank row).
        stream = open(path, "x", newline="", encoding="utf-8")
        logger = cls(stream)
        logger._writer.writerow(CSV_HEADER)
        return logger

    def write(self, host_time_unix_s: float, sample: Sample) -> None:
        """Append one row for `sample` (Req 11.2)."""
        # Integers go out exactly as they arrived (mV, uA, mW, uWh), with no
        # unit conversion. Converting to V or A would mean floats and
        # rounding, so precision could be lost; a spreadsheet can convert.
        row = [f"{host_time_unix_s:.{HOST_TIME_DECIMALS}f}"]
        row += [str(getattr(sample, name)) for name in _SAMPLE_COLUMNS]
        self._writer.writerow(row)

    def maybe_flush(self) -> None:
        """Flush rows to disk if CSV_FLUSH_PERIOD_S has passed (Req 11.3).

        Call this every loop iteration, even when no row was written. If
        Frames stop arriving, write() stops being called, but the last rows
        still reach disk within one period instead of waiting in a buffer.
        """
        now_s = self._clock()
        if now_s - self._last_flush_s >= CSV_FLUSH_PERIOD_S:
            self._sync_to_disk()
            self._last_flush_s = now_s

    def close(self) -> None:
        """Flush, sync to disk, and close (Req 11.4). Safe to call twice."""
        # The CLI calls close() from a finally block (task 11), and may also
        # close on a normal exit path, so a second call must do nothing.
        if self._stream.closed:
            return
        try:
            self._sync_to_disk()
        finally:
            # Close the file even if the sync fails, so it isn't left open.
            self._stream.close()

    def _sync_to_disk(self) -> None:
        # flush() only moves rows from Python's buffer into the operating
        # system's buffer. The OS may hold them in memory for a while, so a
        # crash or power loss could still lose them. fsync() asks the OS to
        # write them to the disk itself before returning.
        self._stream.flush()
        os.fsync(self._stream.fileno())
