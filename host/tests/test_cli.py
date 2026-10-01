"""Example tests for power_monitor.cli: build_arg_parser and run_session.

run_session gets fakes for the byte source, plot, and clocks, so no hardware
or GUI is needed. Tests that check the CSV is closed use a real CsvLogger on a
file in tmp_path. The others use FakeLogger, which is not a CsvLogger: Python
doesn't enforce type hints at runtime, and run_session only calls write(),
maybe_flush(), and close(), so any object with those methods works.
"""

import csv
import errno
import re
from pathlib import Path
from typing import Final, TextIO

import pytest

from power_monitor.cli import (
    DEFAULT_BAUD_BPS,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    EXIT_USER_STOPPED,
    build_arg_parser,
    run_session,
)
from power_monitor.csv_logger import CsvLogger
from power_monitor.link_monitor import LinkMonitor
from power_monitor.parser import FrameParser
from power_monitor.protocol import FLAG_SENSOR_ERROR, Sample, encode_frame

FRAME_PERIOD_MS: Final = 100  # the default 10 Hz firmware rate
FIXED_WALL_TIME_S: Final = 1_700_000_000.0
# Sync bytes followed by a wrong length byte: the parser rejects it as one
# checksum error (rule 3) as soon as the third byte arrives.
BAD_LENGTH_CANDIDATE: Final = b"\xAA\x55\x00"
CSV_NAME_PATTERN: Final = re.compile(r"power_log_\d{8}_\d{6}\.csv")


def make_sample(seq: int, timestamp_ms: int, status_flags: int = 0) -> Sample:
    return Sample(
        seq=seq,
        timestamp_ms=timestamp_ms,
        status_flags=status_flags,
        bus_voltage_mv=5000,
        current_ua=100_000,
        power_mw=500,
        energy_uwh=0,
    )


def make_frame(seq: int, timestamp_ms: int, status_flags: int = 0) -> bytes:
    return encode_frame(make_sample(seq, timestamp_ms, status_flags))


class ScriptedSource:
    """Returns each scripted chunk from read() in turn, or raises it if it's an exception."""

    def __init__(
        self, script: list[bytes | BaseException], close_error: Exception | None = None
    ) -> None:
        self._script = list(script)
        self._close_error = close_error
        self.closed = False

    def read(self) -> bytes:
        # Every script ends in an exception; running past the end would mean
        # run_session didn't stop, so fail loudly instead of looping forever.
        assert self._script, "ScriptedSource ran out of script"
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class ScriptedClock:
    """Returns the scripted times in order, one per call."""

    def __init__(self, times_s: list[float]) -> None:
        self._times_s = list(times_s)

    def __call__(self) -> float:
        assert self._times_s, "clock called more times than scripted"
        return self._times_s.pop(0)

    @property
    def remaining(self) -> int:
        return len(self._times_s)


class FakeLogger:
    """Records run_session's calls instead of writing a file."""

    def __init__(self, close_error: Exception | None = None) -> None:
        self.rows: list[tuple[float, Sample]] = []
        self.flush_checks = 0
        self.closed = False
        self._close_error = close_error

    def write(self, host_time_unix_s: float, sample: Sample) -> None:
        self.rows.append((host_time_unix_s, sample))

    def maybe_flush(self) -> None:
        self.flush_checks += 1

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class RecordingPlot:
    def __init__(self) -> None:
        self.added: list[tuple[float, Sample]] = []
        self.refreshed_at_s: list[float] = []

    def add(self, t_s: float, sample: Sample) -> None:
        self.added.append((t_s, sample))

    def maybe_refresh(self, now_s: float) -> None:
        self.refreshed_at_s.append(now_s)


def open_log_stream(tmp_path: Path) -> TextIO:
    # Opened here rather than with CsvLogger.open() so the test holds the
    # stream and can check stream.closed afterwards.
    return open(tmp_path / "log.csv", "x", newline="", encoding="utf-8")


def read_rows(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


# ---- build_arg_parser ----


def test_simulate_alone_uses_defaults() -> None:
    args = build_arg_parser().parse_args(["--simulate"])

    assert args.simulate is True
    assert args.port is None
    assert args.baud == DEFAULT_BAUD_BPS == 115200
    assert args.no_plot is False
    assert isinstance(args.csv, Path)
    # Matched by shape, not exact time: the name depends on when the parser
    # was built.
    assert CSV_NAME_PATTERN.fullmatch(args.csv.name), args.csv.name
    assert not args.csv.is_absolute()
    assert args.csv.parent == Path(".")


def test_all_options_parse() -> None:
    args = build_arg_parser().parse_args(
        ["--port", "/dev/cu.usbmodem14101", "--baud", "9600", "--csv", "out/run.csv", "--no-plot"]
    )

    assert args.port == "/dev/cu.usbmodem14101"
    assert args.simulate is False
    assert args.baud == 9600
    assert args.csv == Path("out/run.csv")
    assert args.no_plot is True


@pytest.mark.parametrize(
    "argv",
    [
        [],  # neither --port nor --simulate (Req 13.5)
        ["--port", "/dev/cu.usbmodem14101", "--simulate"],  # both
    ],
)
def test_bad_source_options_exit_with_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_arg_parser().parse_args(argv)

    assert exc_info.value.code == EXIT_USAGE_ERROR == 2
    assert "usage:" in capsys.readouterr().err


# ---- run_session: exit paths ----


def test_ctrl_c_returns_0_closes_csv_and_prints_all_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # seq 0 then seq 3: 2 dropped. Then seq 0 with a smaller Timestamp: a
    # device reset. Plus one bad candidate: a checksum error.
    source = ScriptedSource(
        [
            make_frame(0, 1_000),
            make_frame(3, 1_000 + 3 * FRAME_PERIOD_MS) + BAD_LENGTH_CANDIDATE,
            make_frame(0, 5),
            KeyboardInterrupt(),
        ]
    )
    stream = open_log_stream(tmp_path)
    parser = FrameParser()

    status = run_session(
        source,
        parser,
        LinkMonitor(),
        CsvLogger(stream),
        None,
        wall_clock=lambda: FIXED_WALL_TIME_S,
    )

    assert status == EXIT_USER_STOPPED == 0
    assert stream.closed
    assert source.closed
    # No header: CsvLogger(stream) only wraps the stream. One row per Frame.
    rows = read_rows(tmp_path / "log.csv")
    assert [row[2] for row in rows] == ["0", "3", "0"]
    assert rows[0][0] == "1700000000.000"

    out = capsys.readouterr().out
    assert "frames received=3" in out
    assert "dropped frames=2" in out
    assert "checksum errors=1" in out
    assert "device resets=1" in out


def test_os_error_returns_1_closes_csv_and_reports_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The kind of error a USB serial disconnect produces on macOS.
    disconnect = OSError(errno.ENXIO, "Device not configured")
    source = ScriptedSource([make_frame(0, 1_000), disconnect])
    stream = open_log_stream(tmp_path)

    status = run_session(source, FrameParser(), LinkMonitor(), CsvLogger(stream), None)

    assert status == EXIT_RUNTIME_ERROR == 1
    assert stream.closed
    assert source.closed
    # The row read before the disconnect reached the file.
    assert len(read_rows(tmp_path / "log.csv")) == 1

    captured = capsys.readouterr()
    assert captured.err == "error: I/O error, session stopped: [Errno 6] Device not configured\n"
    assert "link summary: frames received=1" in captured.out


def test_source_close_failure_still_closes_csv_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = ScriptedSource([KeyboardInterrupt()], close_error=OSError("close failed"))
    stream = open_log_stream(tmp_path)

    # The close error is not swallowed: it propagates after the cleanup.
    with pytest.raises(OSError, match="close failed"):
        run_session(source, FrameParser(), LinkMonitor(), CsvLogger(stream), None)

    assert stream.closed
    assert "link summary:" in capsys.readouterr().out


def test_csv_close_failure_still_closes_source_and_prints_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = ScriptedSource([KeyboardInterrupt()])
    logger = FakeLogger(close_error=OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        run_session(source, FrameParser(), LinkMonitor(), logger, None)

    assert logger.closed
    assert source.closed
    assert "link summary:" in capsys.readouterr().out


# ---- run_session: per-Sample and per-loop work ----


def test_flag_warning_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    source = ScriptedSource([make_frame(0, 1_000, FLAG_SENSOR_ERROR), KeyboardInterrupt()])

    run_session(source, FrameParser(), LinkMonitor(), FakeLogger(), None)

    assert "warning: SENSOR_ERROR set" in capsys.readouterr().out


def test_maybe_flush_runs_even_when_no_bytes_arrive() -> None:
    # Three loop iterations, none with any data; Ctrl+C on the fourth read.
    source = ScriptedSource([b"", b"", b"", KeyboardInterrupt()])
    logger = FakeLogger()

    run_session(source, FrameParser(), LinkMonitor(), logger, None)

    assert logger.rows == []
    assert logger.flush_checks == 3


def test_plot_add_and_refresh_get_time_since_session_start() -> None:
    first = make_sample(0, 1_000)
    second = make_sample(1, 1_000 + FRAME_PERIOD_MS)
    source = ScriptedSource([encode_frame(first) + encode_frame(second), b"", KeyboardInterrupt()])
    plot = RecordingPlot()
    # mono_clock calls, in order (values chosen to be exact in binary):
    #   100.0   session start
    #   100.5   plot.add for `first`  -> t_s 0.5
    #   100.75  plot.add for `second` -> t_s 0.75
    #   101.0   maybe_refresh after iteration 1 -> now_s 1.0
    #   101.25  maybe_refresh after iteration 2 (read returned b"") -> now_s 1.25
    # The third read raises, so there are no more calls.
    mono = ScriptedClock([100.0, 100.5, 100.75, 101.0, 101.25])

    run_session(
        source, FrameParser(), LinkMonitor(), FakeLogger(), plot, mono_clock=mono
    )

    assert plot.added == [(0.5, first), (0.75, second)]
    assert plot.refreshed_at_s == [1.0, 1.25]
    assert mono.remaining == 0


def test_no_plot_runs_and_only_reads_mono_clock_at_start() -> None:
    sample = make_sample(0, 1_000)
    source = ScriptedSource([encode_frame(sample), b"", KeyboardInterrupt()])
    logger = FakeLogger()
    # Only the session start: with no plot, nothing else needs mono_clock.
    mono = ScriptedClock([100.0])

    status = run_session(
        source,
        FrameParser(),
        LinkMonitor(),
        logger,
        None,
        wall_clock=lambda: FIXED_WALL_TIME_S,
        mono_clock=mono,
    )

    assert status == EXIT_USER_STOPPED
    assert logger.rows == [(FIXED_WALL_TIME_S, sample)]
    assert mono.remaining == 0
