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
from itertools import accumulate

import matplotlib.pyplot as plt
import pytest
from hypothesis import given
from hypothesis import strategies as st

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


# Enough points to cross the window several times (each increment can be up
# to 3 windows), while keeping each example quick.
MAX_PLOT_POINTS = 50
MAX_GAP_S = 3 * PLOT_WINDOW_S
MAX_SMALL_STEP_S = 1.0

# Gaps between consecutive host times. All are >= 0, so accumulating them
# always gives a non-decreasing sequence: no filter or assume() is needed.
# - 0.0: repeated times (two Samples read in the same loop iteration).
# - small steps: many points inside one window, like real 10-50 Hz data.
# - up to 3 windows: long pauses that should empty all but the newest point.
# - exactly PLOT_WINDOW_S, or two halves that add up to it: puts a point
#   exactly on the drop boundary. Random floats almost never land there, so
#   without these a `<=` instead of `<` in PlotBuffer.add would go unnoticed.
#   (30.0 + 30.0 == 60.0 exactly in binary floating point, so the halves
#   really do hit the boundary.)
_increments_s = st.one_of(
    st.sampled_from([0.0, PLOT_WINDOW_S / 2, PLOT_WINDOW_S]),
    st.floats(0.0, MAX_SMALL_STEP_S, allow_nan=False, allow_infinity=False),
    st.floats(0.0, MAX_GAP_S, allow_nan=False, allow_infinity=False),
)


# Feature: power-monitor, Property 17: The plot buffer holds exactly the last 60 seconds
# Validates: Requirements 12.1
@given(
    start_s=st.one_of(
        st.just(0.0),
        st.floats(0.0, MAX_GAP_S, allow_nan=False, allow_infinity=False),
    ),
    increments_s=st.lists(_increments_s, min_size=1, max_size=MAX_PLOT_POINTS),
)
def test_plot_buffer_keeps_exactly_last_window(
    start_s: float, increments_s: list[float]
) -> None:
    times_s = list(accumulate(increments_s, initial=start_s))
    buffer = PlotBuffer()
    for t_s in times_s:
        buffer.add(t_s, SAMPLE)

    # The oracle filters all added times once, against the final threshold.
    # PlotBuffer instead drops points a little at a time on every add(). The
    # two agree because times never decrease: every earlier threshold
    # (t - PLOT_WINDOW_S for an earlier t) is <= the final one, so any point
    # dropped earlier is also outside the final window, and nothing dropped
    # can come back. Same expression as live_plot.py, so the float rounding
    # of the threshold is identical.
    latest_s = times_s[-1]
    oldest_kept_s = latest_s - PLOT_WINDOW_S
    # Inclusive `>=`: a point exactly 60 s old is still "within the most
    # recent 60 seconds" (Req 12.1), and it sits on the x-axis's left edge
    # (now - PLOT_WINDOW_S), so it is still on screen.
    expected_s = [t for t in times_s if t >= oldest_kept_s]
    assert buffer.series()[0] == expected_s
