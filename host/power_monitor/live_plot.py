"""Live plot of the last 60 s of Samples (design.md, "live_plot.py"; Req 12).

Two classes, split the same way as hardware vs logic in the firmware:
- PlotBuffer is pure Python: it keeps the points inside the 60 s window and
  converts units. It is tested without matplotlib.
- LivePlot owns the matplotlib window and draws the buffer's contents.

Lazy import: matplotlib is imported inside LivePlot.__init__, never at the
top of this file. Importing pyplot loads a GUI toolkit (on macOS, the Cocoa
"macosx" backend) and is slow (around a second, more on a first run while
it builds its font cache). With the import inside __init__, `--no-plot`
runs and the tests that only use PlotBuffer never pay for it, and never
need a display. Only creating a LivePlot does.

Time axis: t_s is host time, seconds since session start (run_session takes
it from time.monotonic()), not the device's timestamp_ms. If the board
resets, its Timestamp restarts at 0, and after about 49.7 days it wraps;
either would make points jump backwards on a Timestamp axis. Host time only
moves forward, so the axis keeps scrolling smoothly through a device reset.
"""

from collections import deque
from typing import TYPE_CHECKING, Final

from power_monitor.protocol import Sample

# Only for type hints: TYPE_CHECKING is False when the program runs, so this
# import never executes and the module still loads without matplotlib. Type
# checkers (and editors) set it to True, so they still know these types.
if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

PLOT_WINDOW_S: Final = 60.0  # Req 12.1: the most recent 60 seconds
# 0.5 s gives 2 refreshes per second, double Req 12.2's minimum of one. As
# with the CSV flush, maybe_refresh() only runs once per loop iteration, so
# the real gap can be the period plus one iteration; 0.5 s keeps that < 1 s.
PLOT_REFRESH_PERIOD_S: Final = 0.5
# How long plt.pause() runs the GUI event loop per refresh. Just long enough
# to process pending window events (redraw, resize, close); a longer pause
# would stall the read loop, and serial bytes would pile up in the OS buffer.
PLOT_PAUSE_S: Final = 0.001

MV_PER_V: Final = 1000
UA_PER_MA: Final = 1000

# One subplot per entry, top to bottom. The units in the labels match the
# conversions in PlotBuffer.series().
_Y_LABELS: Final = ("Bus voltage (V)", "Current (mA)", "Power (mW)")
_X_LABEL: Final = "Time since start (s)"


class PlotBuffer:
    """The (host time, Sample) points from the last PLOT_WINDOW_S seconds."""

    def __init__(self) -> None:
        # Whole Samples are stored, not copies of the three plotted fields:
        # the Sample object already exists (the parser made it), so keeping a
        # reference copies nothing, and Samples are frozen, so a stored one
        # can't change later. series() picks out the fields it plots.
        #
        # A deque, not a list: points leave from the front. list.pop(0)
        # shifts every remaining element, so it is O(n); deque.popleft() is
        # O(1). At the 50 Hz debug rate a window is 3000 points, and after a
        # burst of buffered serial data it can be far more, all removed one
        # at a time from the front.
        self._points: deque[tuple[float, Sample]] = deque()

    def add(self, t_s: float, sample: Sample) -> None:
        """Append a point, then drop points older than PLOT_WINDOW_S before it.

        t_s must not decrease between calls (run_session uses a monotonic
        clock), so the oldest point is always at the front.
        """
        self._points.append((t_s, sample))
        oldest_kept_s = t_s - PLOT_WINDOW_S
        # Strict `<`, so a point exactly PLOT_WINDOW_S old is kept. That
        # matches Property 17 ("at least the latest time minus 60 s") and the
        # x-axis, whose left limit is now_s - PLOT_WINDOW_S: a point on that
        # edge is still on screen, so it should still be in the buffer.
        # The loop can't empty the deque: the point just added is never
        # older than itself, so it stops there at the latest.
        while self._points[0][0] < oldest_kept_s:
            self._points.popleft()

    def series(self) -> tuple[list[float], list[float], list[float], list[float]]:
        """(t_s, volts, milliamps, milliwatts), oldest point first."""
        # Floats in V and mA are for display only: they read better on an
        # axis than mV and uA. The CSV keeps the exact integers from the Frame.
        t_s = [t for t, _ in self._points]
        volts = [s.bus_voltage_mv / MV_PER_V for _, s in self._points]
        milliamps = [s.current_ua / UA_PER_MA for _, s in self._points]
        milliwatts = [float(s.power_mw) for _, s in self._points]
        return t_s, volts, milliamps, milliwatts


class LivePlot:
    """A matplotlib window with V, mA, and mW stacked over a shared time axis.

    It has add() and maybe_refresh(), so it fits cli.PlotSink without
    importing cli.

    Single-threaded: run_session calls maybe_refresh() from its own loop
    instead of running the plot in a background thread. The macOS GUI
    backend must run on the main thread (Cocoa refuses windows from other
    threads), and 1.25 kB/s of serial data leaves the loop mostly idle, so
    there is nothing for a second thread to speed up.
    """

    def __init__(self, buffer: PlotBuffer) -> None:
        # The lazy import (see the module docstring). Kept on self so the
        # other methods use the same pyplot module without importing it again.
        import matplotlib.pyplot as plt

        self._plt = plt
        self._buffer = buffer
        # sharex=True links the x-axes: setting the limits on one sets them
        # on all three, and only the bottom subplot shows x tick labels.
        fig, axes = plt.subplots(nrows=len(_Y_LABELS), ncols=1, sharex=True)
        self._fig: "Figure" = fig
        self._axes: "tuple[Axes, ...]" = tuple(axes)
        for ax, label in zip(self._axes, _Y_LABELS):
            ax.set_ylabel(label)
        self._axes[-1].set_xlabel(_X_LABEL)
        # One line per subplot, created once with no data. Each refresh then
        # swaps in new data with set_data(). Calling ax.plot() every refresh
        # would add a new line object (an "artist") each time, so the figure
        # would keep growing and every redraw would get slower; clearing and
        # rebuilding the axes instead would redo labels and ticks each time.
        self._lines: "tuple[Line2D, ...]" = tuple(ax.plot([], [])[0] for ax in self._axes)
        self._last_refresh_s: float | None = None  # None: never refreshed yet
        self._closed = False

    def add(self, t_s: float, sample: Sample) -> None:
        """Record a point; it appears on the next refresh."""
        self._buffer.add(t_s, sample)

    def maybe_refresh(self, now_s: float) -> None:
        """Redraw if PLOT_REFRESH_PERIOD_S has passed since the last redraw.

        now_s is seconds since session start, the same time base as add().
        The first call always redraws, so the window appears at once.
        Call this every loop iteration, even when no Sample arrived, so the
        window keeps scrolling and stays responsive.
        """
        if self._closed:
            return
        # The user closed the window: stop for good. Every pyplot call
        # after this is skipped, because pyplot functions that need a
        # "current figure" (plt.pause among them) create a new one when none
        # exists, which would reopen a window the user just closed. Logging
        # carries on; only the plot stops.
        if not self._plt.fignum_exists(self._fig.number):
            self._closed = True
            return
        if (
            self._last_refresh_s is not None
            and now_s - self._last_refresh_s < PLOT_REFRESH_PERIOD_S
        ):
            return

        t_s, *y_series = self._buffer.series()
        for ax, line, y in zip(self._axes, self._lines, y_series):
            line.set_data(t_s, y)
            # relim() recomputes the data limits from the lines' new data
            # (axes don't track set_data() on their own); autoscale_view()
            # then fits the y range to them. scalex=False because x is set
            # explicitly below.
            ax.relim()
            ax.autoscale_view(scalex=False)
        # A fixed-width window ending at now_s, not at the newest point, so
        # the axis keeps scrolling even if Samples stop. Early in a session
        # the left part is negative and empty; the trace grows in from the
        # right instead of the axis stretching.
        self._axes[0].set_xlim(now_s - PLOT_WINDOW_S, now_s)
        # Matplotlib only draws, and the window only reacts to clicks,
        # resizes, or the close button, while the GUI event loop runs. Our
        # loop never hands it control otherwise, so plt.pause() draws the
        # pending changes and runs the event loop for PLOT_PAUSE_S. Without
        # it the window would not update and the OS would show it as frozen.
        self._plt.pause(PLOT_PAUSE_S)
        self._last_refresh_s = now_s
