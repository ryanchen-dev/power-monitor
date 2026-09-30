"""Example tests for power_monitor.csv_logger (design.md, "csv_logger.py").

Every test writes real files, and only inside pytest's tmp_path.
"""

import csv
import io
from pathlib import Path

import pytest

from power_monitor import csv_logger
from power_monitor.csv_logger import CSV_FLUSH_PERIOD_S, CSV_HEADER, CsvLogger
from power_monitor.protocol import Sample

# Expected unit suffix per column, from the "CSV schema" table in design.md.
UNIT_SUFFIXES = {
    "host_time_unix_s": "_s",
    "timestamp_ms": "_ms",
    "bus_voltage_mv": "_mv",
    "current_ua": "_ua",
    "power_mw": "_mw",
    "energy_uwh": "_uwh",
}
# A count and a bit field are dimensionless, so they have no unit to name.
DIMENSIONLESS_COLUMNS = {"seq", "status_flags"}

SAMPLE_A = Sample(
    seq=7,
    timestamp_ms=123_456,
    status_flags=0x02,
    bus_voltage_mv=3700,
    current_ua=250_000,
    power_mw=925,
    energy_uwh=1_234,
)
# Negative current and power: the battery is being charged.
SAMPLE_B = Sample(
    seq=8,
    timestamp_ms=123_556,
    status_flags=0x00,
    bus_voltage_mv=4096,
    current_ua=-300_000,
    power_mw=-1_229,
    energy_uwh=-5,
)


class FakeClock:
    """A clock that only moves when a test sets it."""

    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def clock(self) -> float:
        return self.now_s


class FsyncCounter:
    """Stands in for os.fsync and records the file descriptors it was given."""

    def __init__(self) -> None:
        self.fds: list[int] = []

    def __call__(self, fd: int) -> None:
        self.fds.append(fd)


@pytest.fixture
def fsync_counter(monkeypatch: pytest.MonkeyPatch) -> FsyncCounter:
    counter = FsyncCounter()
    # Patched through the module's own `os` reference; monkeypatch restores
    # the real os.fsync when the test ends.
    monkeypatch.setattr(csv_logger.os, "fsync", counter)
    return counter


def read_rows(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_header_matches_schema_and_names_units(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    CsvLogger.open(path).close()

    assert read_rows(path) == [list(CSV_HEADER)]
    assert set(CSV_HEADER) == set(UNIT_SUFFIXES) | DIMENSIONLESS_COLUMNS
    for column, suffix in UNIT_SUFFIXES.items():
        assert column.endswith(suffix), column


def test_wrapping_a_stream_writes_no_header() -> None:
    # Only open() writes the header, so wrapping a stream can't duplicate it.
    stream = io.StringIO()
    CsvLogger(stream)
    assert stream.getvalue() == ""


def test_one_row_per_write_with_raw_integers(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = CsvLogger.open(path)
    logger.write(1_700_000_000.0, SAMPLE_A)
    logger.write(1_700_000_000.1, SAMPLE_B)
    logger.close()

    rows = read_rows(path)
    assert rows[1:] == [
        ["1700000000.000", "123456", "7", "2", "3700", "250000", "925", "1234"],
        ["1700000000.100", "123556", "8", "0", "4096", "-300000", "-1229", "-5"],
    ]


@pytest.mark.parametrize(
    ("host_time_unix_s", "expected"),
    [
        (1_700_000_000.1, "1700000000.100"),  # padded to 3 decimals
        (1_700_000_000.12349, "1700000000.123"),  # rounded down
        (1_700_000_000.9876, "1700000000.988"),  # rounded up
    ],
)
def test_host_time_has_exactly_three_decimals(
    tmp_path: Path, host_time_unix_s: float, expected: str
) -> None:
    path = tmp_path / "log.csv"
    logger = CsvLogger.open(path)
    logger.write(host_time_unix_s, SAMPLE_A)
    logger.close()

    assert read_rows(path)[1][0] == expected


def test_flush_happens_once_per_period(tmp_path: Path, fsync_counter: FsyncCounter) -> None:
    path = tmp_path / "log.csv"
    period = CSV_FLUSH_PERIOD_S
    # Just short of a full period. 1.5 * period and 2 * period are exact in
    # binary for period = 0.5, so the >= comparison isn't at the mercy of
    # float rounding.
    just_before_s = 0.001
    fake = FakeClock()
    with open(path, "x", newline="", encoding="utf-8") as stream:
        logger = CsvLogger(stream, clock=fake.clock)
        logger.write(1_700_000_000.0, SAMPLE_A)

        fake.now_s = period - just_before_s
        logger.maybe_flush()
        assert len(fsync_counter.fds) == 0

        fake.now_s = period
        logger.maybe_flush()
        assert fsync_counter.fds == [stream.fileno()]
        # flush() pushed the row to the OS, so a separate reader sees it.
        assert len(read_rows(path)) == 1

        fake.now_s = 1.5 * period
        logger.maybe_flush()
        assert len(fsync_counter.fds) == 1

        fake.now_s = 2 * period
        logger.maybe_flush()
        assert len(fsync_counter.fds) == 2


def test_close_fsyncs(tmp_path: Path, fsync_counter: FsyncCounter) -> None:
    logger = CsvLogger.open(tmp_path / "log.csv")
    logger.write(1_700_000_000.0, SAMPLE_A)
    logger.close()
    assert len(fsync_counter.fds) == 1


def test_close_twice_keeps_every_row(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    logger = CsvLogger.open(path)
    logger.write(1_700_000_000.0, SAMPLE_A)
    logger.write(1_700_000_000.1, SAMPLE_B)
    logger.close()
    logger.close()

    rows = read_rows(path)
    assert rows[0] == list(CSV_HEADER)
    assert len(rows) == 3
    # csv.writer ends every row with "\r\n", so a complete last row does too.
    assert path.read_bytes().endswith(b"\r\n")


def test_open_refuses_to_overwrite_an_existing_log(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("earlier run\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        CsvLogger.open(path)
    assert path.read_text(encoding="utf-8") == "earlier run\n"
