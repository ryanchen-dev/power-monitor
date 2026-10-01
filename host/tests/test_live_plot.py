"""Tests for live_plot.py: PlotBuffer windowing and units, LivePlot refresh."""

from collections.abc import Iterator

# Agg is matplotlib's non-interactive backend: it renders only to image
# files and never opens a window, so these tests need no display and can't
# pop up a GUI. matplotlib.use() must run before pyplot is first imported
# to choose the backend cleanly. If another test module imported pyplot
# first (pytest imports test modules alphabetically, and test_cli may later
# create a LivePlot), use() still switches the backend, so this stays safe.
import matplotlib

matplotlib.use("Agg")

# The remaining imports deliberately come after matplotlib.use().
import matplotlib.pyplot as plt
import pytest

from power_monitor.live_plot import PLOT_PAUSE_S, PLOT_WINDOW_S, LivePlot, PlotBuffer
from power_monitor.protocol import Sample

# 3.7 V x -250 mA = -925 mW: a battery being charged (negative current).
SAMPLE = Sample(
    seq=0,
    timestamp_ms=0,
    status_flags=0,
    bus_voltage_mv=3700,
    current_ua=-250_000,
    power_mw=-925,
    energy_uwh=0,
)


@pytest.fixture
def pause_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Replace plt.pause with a recorder; close all figures afterwards.

    Each refresh calls plt.pause exactly once, so len(calls) counts
    refreshes. LivePlot calls pause through the same matplotlib.pyplot
    module object, so patching it here reaches LivePlot too.
    """
    calls: list[float] = []

    def fake_pause(interval: float) -> None:
        calls.append(interval)

    monkeypatch.setattr(plt, "pause", fake_pause)
    yield calls
    # Figures live in pyplot's global registry until closed, so without
    # this every test's figure would leak into the next one.
    plt.close("all")


@pytest.fixture
def live_plot(pause_calls: list[float]) -> LivePlot:
    return LivePlot(PlotBuffer())


def test_buffer_drops_points_older_than_window() -> None:
    buffer = PlotBuffer()
    for t_s in (0.0, 30.0, 61.0):
        buffer.add(t_s, SAMPLE)
    assert buffer.series()[0] == [30.0, 61.0]


def test_buffer_keeps_point_exactly_window_old() -> None:
    buffer = PlotBuffer()
    buffer.add(0.0, SAMPLE)
    buffer.add(PLOT_WINDOW_S, SAMPLE)
    assert buffer.series()[0] == [0.0, PLOT_WINDOW_S]


def test_series_converts_units_including_negative_values() -> None:
    buffer = PlotBuffer()
    buffer.add(1.5, SAMPLE)
    t_s, volts, milliamps, milliwatts = buffer.series()
    assert t_s == [1.5]
    assert volts == [pytest.approx(3.7)]
    assert milliamps == [pytest.approx(-250.0)]
    assert milliwatts == [pytest.approx(-925.0)]


def test_series_of_empty_buffer_is_empty() -> None:
    assert PlotBuffer().series() == ([], [], [], [])


def test_add_forwards_to_buffer(pause_calls: list[float]) -> None:
    buffer = PlotBuffer()
    plot = LivePlot(buffer)
    plot.add(2.0, SAMPLE)
    assert buffer.series()[0] == [2.0]


def test_refresh_at_first_call_then_every_period(
    live_plot: LivePlot, pause_calls: list[float]
) -> None:
    live_plot.maybe_refresh(0.0)
    assert len(pause_calls) == 1
    live_plot.maybe_refresh(0.2)
    assert len(pause_calls) == 1
    live_plot.maybe_refresh(0.5)
    assert len(pause_calls) == 2
    # The period counts from the last refresh (0.5 s), not from the start.
    live_plot.maybe_refresh(0.7)
    assert len(pause_calls) == 2
    assert pause_calls == [PLOT_PAUSE_S, PLOT_PAUSE_S]


def test_refresh_with_empty_buffer(
    live_plot: LivePlot, pause_calls: list[float]
) -> None:
    live_plot.maybe_refresh(0.0)
    assert len(pause_calls) == 1


def test_refresh_sets_x_limits_to_last_window(
    live_plot: LivePlot, pause_calls: list[float]
) -> None:
    live_plot.add(70.0, SAMPLE)
    live_plot.maybe_refresh(75.0)
    # Shared x-axis: all three subplots get the same limits.
    for ax in live_plot._fig.axes:
        assert ax.get_xlim() == pytest.approx((75.0 - PLOT_WINDOW_S, 75.0))


def test_refresh_reuses_one_line_per_axes(
    live_plot: LivePlot, pause_calls: list[float]
) -> None:
    axes = live_plot._fig.axes
    lines_before = [ax.get_lines()[0] for ax in axes]
    live_plot.add(1.0, SAMPLE)
    live_plot.maybe_refresh(1.0)
    live_plot.add(2.0, SAMPLE)
    live_plot.maybe_refresh(2.0)
    assert len(axes) == 3
    for ax, line in zip(axes, lines_before):
        assert len(ax.get_lines()) == 1
        assert ax.get_lines()[0] is line
        assert list(line.get_xdata()) == [1.0, 2.0]
    assert list(lines_before[0].get_ydata()) == pytest.approx([3.7, 3.7])


def test_axis_labels_include_units(live_plot: LivePlot) -> None:
    axes = live_plot._fig.axes
    assert [ax.get_ylabel() for ax in axes] == [
        "Bus voltage (V)",
        "Current (mA)",
        "Power (mW)",
    ]
    assert axes[-1].get_xlabel() == "Time since start (s)"


def test_closed_figure_stops_refresh_without_reopening(
    live_plot: LivePlot, pause_calls: list[float]
) -> None:
    live_plot.maybe_refresh(0.0)
    assert len(pause_calls) == 1
    plt.close("all")  # what clicking the window's close button does
    live_plot.add(0.5, SAMPLE)
    live_plot.maybe_refresh(0.5)  # must not raise
    live_plot.maybe_refresh(1.0)
    assert len(pause_calls) == 1
    assert plt.get_fignums() == []  # no new figure was created
