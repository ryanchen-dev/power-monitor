"""Software stand-in for the device: Samples, Frames, and their timing.

Used by `--simulate` so the whole host pipeline (parser, monitor, CSV, plot)
runs without hardware. The Frames it produces go through the same
FrameParser as serial bytes (Req 13.3).

Floats appear only in `simulated_values`, to evaluate the sine waves and the
sleep times. That is fine here: the no-float rule is for the firmware (no FPU
on the ATmega328P, and results must match the Mac bit for bit). Power and
Energy still use the firmware's integer rules, so simulated Frames look like
real ones.
"""

import math
import time
from collections.abc import Callable
from typing import Final

from power_monitor.protocol import Sample, encode_frame

MS_PER_S: Final = 1000
SIM_SAMPLE_RATE_HZ: Final = 10
SIM_SAMPLE_PERIOD_MS: Final = MS_PER_S // SIM_SAMPLE_RATE_HZ  # 100 ms

# Same names and values as the firmware's pm_power.h / pm_energy.h.
NW_PER_MW: Final = 1_000_000  # mV x uA = nW
PJ_PER_UWH: Final = 3_600_000_000  # 1 uWh = 1e-6 W x 3600 s = 3.6e9 pJ

# Wrap points of the two counters, matching the firmware's uint16_t seq and
# uint32_t millis().
_SEQ_MODULUS: Final = 2**16
_TIMESTAMP_MODULUS_MS: Final = 2**32

# Waveforms (design.md, "simulator.py"): V spans 3.3-4.1 V, I spans
# -300...+900 mA, both inside the Rated_Range, and I goes negative.
_V_CENTER_MV: Final = 3700
_V_AMPLITUDE_MV: Final = 400
_V_PERIOD_S: Final = 20.0
_V_STEP_MV: Final = 4  # INA219 bus voltage LSB
_I_CENTER_UA: Final = 300_000
_I_AMPLITUDE_UA: Final = 600_000
_I_PERIOD_S: Final = 8.0
_I_STEP_UA: Final = 100  # CURRENT_LSB_UA in the firmware


def _quantize(value: float, step: int) -> int:
    # Nearest multiple of `step`: scale to units of `step`, round, scale back.
    # round() is Python's round-half-to-even, which would be wrong for the
    # Power rule, but here it only snaps a sine value to the sensor's grid.
    # A tie needs the float to land exactly half a step between grid points,
    # and either neighbour would be an equally valid reading.
    return round(value / step) * step


def simulated_values(t_s: float) -> tuple[int, int]:
    """(bus_voltage_mv, current_ua) at time `t_s`, on the INA219's 4 mV / 100 uA grid."""
    v_mv = _V_CENTER_MV + _V_AMPLITUDE_MV * math.sin(2 * math.pi * t_s / _V_PERIOD_S)
    i_ua = _I_CENTER_UA + _I_AMPLITUDE_UA * math.sin(2 * math.pi * t_s / _I_PERIOD_S)
    return _quantize(v_mv, _V_STEP_MV), _quantize(i_ua, _I_STEP_UA)


def power_mw_from(bus_voltage_mv: int, current_ua: int) -> int:
    """Power in mW, rounded half away from zero; mirrors the firmware's pm_power_mw."""
    # Integers, not round(p_nw / 1e6): round() rounds half to even
    # (round(0.5) == 0, round(2.5) == 2), while the firmware rounds half away
    # from zero, so round() would disagree with the device on exact ties.
    # Working on the magnitude makes the rule symmetric for negative power;
    # // on a non-negative number is plain truncation, so no floor surprise.
    p_nw = bus_voltage_mv * current_ua
    magnitude_mw = (abs(p_nw) + NW_PER_MW // 2) // NW_PER_MW
    return -magnitude_mw if p_nw < 0 else magnitude_mw


def energy_uwh_from_pj(energy_pj: int) -> int:
    """Energy in uWh, truncated toward zero; mirrors the firmware's pm_energy_uwh."""
    # Not energy_pj // PJ_PER_UWH: Python's // floors toward minus infinity
    # (-1 // 3 == -1), but C's / truncates toward zero (-1 / 3 == 0). Dividing
    # the magnitude and then restoring the sign gives C's result.
    magnitude_uwh = abs(energy_pj) // PJ_PER_UWH
    return -magnitude_uwh if energy_pj < 0 else magnitude_uwh


class Simulator:
    """Builds Samples the way the firmware would, from the simulated waveforms.

    Long-run limit (no saturation logic, by design scope): Python ints don't
    overflow, but energy_uwh must fit the Frame's int32 (about 2147 Wh).
    Mean simulated power is 3.7 V x 0.3 A = 1.11 W; the product of the two
    sines averages to zero because their periods differ (20 s vs 8 s, both
    whole numbers of cycles in 40 s). So the limit is reached after about
    1935 h (about 80.6 days), and encode_frame would then raise ValueError.
    seq and timestamp_ms wrap as on the device (timestamp_ms after about
    49.7 days, which restarts the waveforms), so they never leave their ranges.
    """

    def __init__(self) -> None:
        self._seq = 0
        # Exact integer pJ, as in the firmware: mV x uA x ms is exactly 1 pJ,
        # so each interval adds an exact integer and nothing is rounded until
        # the single division in energy_uwh_from_pj. Error stays below 1 uWh
        # however long the run is.
        self._energy_pj = 0
        # None until the first Sample: that Sample only sets the baseline
        # time, since there is no earlier Sample to measure an interval from.
        self._prev_timestamp_ms: int | None = None

    def next_sample(self, timestamp_ms: int) -> Sample:
        """The next Sample at `timestamp_ms`; advances seq and energy."""
        # t comes from timestamp_ms, not from a wall clock, so the values are
        # a pure function of the Timestamp and tests can predict them.
        bus_voltage_mv, current_ua = simulated_values(timestamp_ms / MS_PER_S)

        if self._prev_timestamp_ms is not None:
            # Modular difference, like the firmware's uint32_t subtraction,
            # so an interval that crosses the 2^32 ms wrap stays correct.
            dt_ms = (timestamp_ms - self._prev_timestamp_ms) % _TIMESTAMP_MODULUS_MS
            # Right-endpoint rule: the later Sample's V and I over the interval.
            self._energy_pj += bus_voltage_mv * current_ua * dt_ms
        self._prev_timestamp_ms = timestamp_ms

        sample = Sample(
            seq=self._seq,
            timestamp_ms=timestamp_ms,
            status_flags=0,
            bus_voltage_mv=bus_voltage_mv,
            current_ua=current_ua,
            power_mw=power_mw_from(bus_voltage_mv, current_ua),
            energy_uwh=energy_uwh_from_pj(self._energy_pj),
        )
        self._seq = (self._seq + 1) % _SEQ_MODULUS
        return sample


class SimulatedSource:
    """A byte source that hands out one encoded Frame per sample period."""

    def __init__(
        self,
        simulator: Simulator,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # clock and sleep are injected so tests can pass a fake clock whose
        # sleep just advances a number: timing tests run instantly and give
        # exact, repeatable results instead of depending on the OS scheduler.
        self._simulator = simulator
        self._clock = clock
        self._sleep = sleep
        self._frame_index = 0
        self._start_s: float | None = None

    def read(self) -> bytes:
        """Sleep until the next Frame is due, then return its 25 bytes."""
        now_s = self._clock()
        if self._start_s is None:
            # The first Frame is due at the first read(), with Timestamp 0.
            # Anchoring here rather than at construction means a delay between
            # building the source and starting the loop can't cause a burst.
            self._start_s = now_s

        # Fixed-rate schedule: Frame k is due at start + k periods. Computing
        # it from k (instead of "now + period" after each read) means the time
        # spent in the caller doesn't push later Frames back, so there is no
        # drift. It also avoids summing 0.1 repeatedly, which isn't exact in
        # binary floating point.
        due_s = self._start_s + self._frame_index * SIM_SAMPLE_PERIOD_MS / MS_PER_S
        delay_s = due_s - now_s
        # If the caller is late, return at once rather than sleep a negative
        # amount. Each read still returns one Frame, so later reads catch up on
        # the grid, like Frames that queued in the serial buffer on real hardware.
        if delay_s > 0:
            self._sleep(delay_s)

        # Timestamp from the Frame index, wrapped like millis(), so consecutive
        # Frames are exactly one period apart regardless of sleep jitter.
        timestamp_ms = (self._frame_index * SIM_SAMPLE_PERIOD_MS) % _TIMESTAMP_MODULUS_MS
        self._frame_index += 1
        return encode_frame(self._simulator.next_sample(timestamp_ms))

    def close(self) -> None:
        """Nothing to release; present so it fits the same interface as SerialSource."""
