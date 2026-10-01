"""Command-line interface and the main read/parse/log loop (design.md, "cli.py").

This file holds the argparse setup and run_session(), the loop that wires
ByteSource -> FrameParser -> LinkMonitor / CsvLogger / plot. main() and
SerialSource come in task 11.2.

Output streams: flag messages and the link summary are the tool's normal
report, so they go to stdout, where a user can redirect or capture them
(`> run.txt`). Error messages go to stderr, so they still reach the terminal
when stdout is redirected, and they never mix into a captured report.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Final, Protocol

from power_monitor.csv_logger import CsvLogger
from power_monitor.link_monitor import LinkMonitor, format_summary
from power_monitor.parser import FrameParser
from power_monitor.protocol import Sample

# Exit codes (the process exit status, read by the shell as `$?`).
# 0: the user stopped the session with Ctrl+C. That is the normal way to end
#    a run, so it counts as success.
EXIT_USER_STOPPED: Final = 0
# 1: a runtime error ended the session (e.g. the serial port disconnected).
EXIT_RUNTIME_ERROR: Final = 1
# 2: bad command-line arguments. argparse itself exits with 2 on a usage
#    error; it is named here only so the meaning of every code is in one place.
EXIT_USAGE_ERROR: Final = 2

PROG_NAME: Final = "python -m power_monitor"
DEFAULT_BAUD_BPS: Final = 115200  # must match the firmware UART (Req 8.1)
# strftime codes: %Y year, %m month, %d day, %H hour, %M minute, %S second.
CSV_NAME_FORMAT: Final = "power_log_%Y%m%d_%H%M%S.csv"
# Relative, so the log lands in whatever directory the tool is run from.
DEFAULT_CSV_DIR: Final = Path(".")


# Concept: a typing.Protocol describes a set of methods, not a parent class.
# Any object that has those methods fits, without inheriting from anything
# ("structural typing", also called duck typing with type hints). So
# SimulatedSource, the future SerialSource, and a test's fake are all
# ByteSources just by having read() and close().
class ByteSource(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


# A Protocol here, not an import of LivePlot, for two reasons: run_session
# then does not depend on live_plot.py (which arrives in task 12.1, and which
# pulls in matplotlib), and tests can pass a small fake that records calls.
class PlotSink(Protocol):
    def add(self, t_s: float, sample: Sample) -> None: ...

    def maybe_refresh(self, now_s: float) -> None: ...


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser for the usage line in design.md (Req 13.1, 13.5)."""
    parser = argparse.ArgumentParser(prog=PROG_NAME)

    # required=True: giving neither option is a usage error, so argparse
    # prints the usage line and exits with EXIT_USAGE_ERROR (Req 13.5).
    # Mutually exclusive: giving both is also a usage error.
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--port", metavar="PORT", help="serial device, e.g. /dev/cu.usbmodem14101"
    )
    source.add_argument(
        "--simulate",
        action="store_true",
        help="generate Frames in software; no serial port is opened",
    )

    parser.add_argument(
        "--baud",
        metavar="BAUD",
        type=int,
        default=DEFAULT_BAUD_BPS,
        help="default %(default)s",
    )
    # The default name is computed now, from local time, because the parser
    # is built once at startup: each run gets its own timestamped file.
    parser.add_argument(
        "--csv",
        metavar="PATH",
        type=Path,
        default=DEFAULT_CSV_DIR / datetime.now().strftime(CSV_NAME_FORMAT),
        help="default ./power_log_YYYYmmdd_HHMMSS.csv (local time)",
    )
    parser.add_argument(
        "--no-plot", action="store_true", help="do not open a plot window"
    )
    return parser


def run_session(
    source: ByteSource,
    parser: FrameParser,
    monitor: LinkMonitor,
    logger: CsvLogger,
    plot: PlotSink | None,
    wall_clock: Callable[[], float] = time.time,
    mono_clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the read/parse/log loop until Ctrl+C or an I/O error.

    Returns EXIT_USER_STOPPED on Ctrl+C and EXIT_RUNTIME_ERROR on an OSError.
    Any other exception propagates (after cleanup), since it means a bug.
    """
    # Two clocks on purpose. wall_clock (Unix time) is what a person wants in
    # the CSV, but it can jump when the system clock is adjusted (e.g. a
    # network time sync). mono_clock never goes backwards or jumps, so the
    # plot's x-axis and refresh timing use it, both as seconds since session
    # start. Both clocks are parameters so tests can script them.
    try:
        session_start_s = mono_clock()
        while True:
            data = source.read()
            for sample in parser.feed(data):
                for message in monitor.update(sample):
                    print(message)
                logger.write(wall_clock(), sample)
                if plot is not None:
                    plot.add(mono_clock() - session_start_s, sample)
            # Every iteration, even when read() returned no bytes: if Frames
            # stop, the last rows must still reach disk (Req 11.3), and the
            # plot window must keep handling GUI events.
            logger.maybe_flush()
            if plot is not None:
                # Same time base as plot.add (seconds since session start),
                # so LivePlot can set its x-axis to the last 60 s up to now_s
                # and keep scrolling even when no Frames arrive.
                plot.maybe_refresh(mono_clock() - session_start_s)
    except KeyboardInterrupt:
        return EXIT_USER_STOPPED
    except OSError as exc:
        # pyserial's SerialException is a subclass of OSError, so this also
        # catches a serial disconnect (Req 13.7) without importing serial
        # here. It also catches a failing CSV write or fsync, which is why
        # the wording names an I/O error rather than the serial port.
        print(f"error: I/O error, session stopped: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    finally:
        # finally runs on every way out of the try: the returns above, an
        # unexpected exception, and Ctrl+C. So the CSV is always closed and
        # the summary always printed (Req 10.3, 11.4, 13.7).
        #
        # Nested try/finally so one failing step can't skip the others: the
        # CSV is closed first (the data matters most), then the source, and
        # the summary prints even if both closes fail. A close exception
        # still propagates after that, so a failure is never hidden.
        #
        # There is deliberately no `return` in this block. A return inside
        # finally discards any exception in flight, so a close failure (or
        # a bug) would silently turn into a normal exit code.
        try:
            try:
                logger.close()
            finally:
                source.close()
        finally:
            print(format_summary(monitor.stats, parser.checksum_errors))
