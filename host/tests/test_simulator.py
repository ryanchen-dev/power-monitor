"""Example tests for power_monitor.simulator (design.md, "simulator.py")."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from power_monitor.protocol import FRAME_LEN_BYTES, decode_frame
from power_monitor.simulator import (
    SIM_SAMPLE_PERIOD_MS,
    SimulatedSource,
    Simulator,
    energy_uwh_from_pj,
    power_mw_from,
    simulated_values,
)

# Written out here rather than imported, so the expected values in these
# tests are computed independently of the module under test.
PJ_PER_UWH_REF = 3_600_000_000
SEQ_COUNT = 65_536
PERIOD_S = 0.1


class FakeClock:
    """A clock that only moves when sleep() is called (or a test moves it)."""

    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now_s

    def sleep(self, duration_s: float) -> None:
        self.sleeps.append(duration_s)
        self.now_s += duration_s


def make_source(fake: FakeClock) -> SimulatedSource:
    return SimulatedSource(Simulator(), clock=fake.clock, sleep=fake.sleep)


# --- seq ---------------------------------------------------------------------


def test_first_sample_has_seq_0_and_zero_energy() -> None:
    sample = Simulator().next_sample(0)
    assert sample.seq == 0
    # The first Sample only sets the baseline time, so nothing is integrated.
    assert sample.energy_uwh == 0
    assert sample.status_flags == 0


def test_seq_wraps_from_65535_to_0() -> None:
    # Driven through the public API (65,536 calls, well under a second) rather
    # than by setting the private counter.
    sim = Simulator()
    for k in range(SEQ_COUNT - 1):
        sim.next_sample(k * SIM_SAMPLE_PERIOD_MS)
    assert sim.next_sample((SEQ_COUNT - 1) * SIM_SAMPLE_PERIOD_MS).seq == 65535
    assert sim.next_sample(SEQ_COUNT * SIM_SAMPLE_PERIOD_MS).seq == 0


# --- simulated_values -----------------------------------------------------------


def test_values_at_known_times() -> None:
    # t = 0: both sines are 0, so the centers. t = 5 s: the V sine peaks.
    assert simulated_values(0.0) == (3700, 300_000)
    assert simulated_values(5.0)[0] == 4100


def test_values_change_over_time() -> None:
    assert simulated_values(0.0) != simulated_values(1.0)
    sim = Simulator()
    first = sim.next_sample(0)
    later = sim.next_sample(1000)
    assert (first.bus_voltage_mv, first.current_ua) != (later.bus_voltage_mv, later.current_ua)


def test_negative_current_appears() -> None:
    # I sine is at its minimum at t = 6 s (3/4 of the 8 s period): 300 - 600 mA.
    assert simulated_values(6.0)[1] == -300_000
    assert Simulator().next_sample(6000).current_ua < 0


# --- Power rounding -------------------------------------------------------------


def test_power_rounds_half_away_from_zero() -> None:
    assert power_mw_from(5000, 123_500) == 618  # 617.5 mW
    assert power_mw_from(3700, -1_045_000) == -3867  # -3866.5 mW


def test_power_exact_half_and_just_below() -> None:
    # 1 mV x 500,000 uA = 500,000 nW = exactly 0.5 mW. round(0.5) would give 0.
    assert power_mw_from(1, 500_000) == 1
    assert power_mw_from(1, -500_000) == -1
    assert power_mw_from(1, 499_999) == 0
    assert power_mw_from(1, -499_999) == 0


def test_sample_power_uses_the_rule() -> None:
    sample = Simulator().next_sample(6000)
    assert sample.power_mw == power_mw_from(sample.bus_voltage_mv, sample.current_ua)


# --- Energy -----------------------------------------------------------------------


def test_energy_truncates_toward_zero() -> None:
    # Python's // would give -1 and -2 here (floor toward minus infinity).
    assert energy_uwh_from_pj(-1) == 0
    assert energy_uwh_from_pj(-(PJ_PER_UWH_REF + 1)) == -1
    assert energy_uwh_from_pj(PJ_PER_UWH_REF - 1) == 0
    assert energy_uwh_from_pj(PJ_PER_UWH_REF) == 1


def test_energy_over_known_intervals() -> None:
    sim = Simulator()
    sim.next_sample(0)
    # Right-endpoint rule: each interval uses the later Sample's V and I.
    v1, i1 = simulated_values(0.1)
    v2, i2 = simulated_values(0.35)
    expected_pj = v1 * i1 * 100 + v2 * i2 * 250
    assert expected_pj > 0
    sim.next_sample(100)
    assert sim.next_sample(350).energy_uwh == expected_pj // PJ_PER_UWH_REF


def test_energy_interval_across_timestamp_wrap() -> None:
    sim = Simulator()
    sim.next_sample(2**32 - 100)
    v, i = simulated_values(0.004)
    # 100 ms up to the wrap plus 4 ms after it.
    assert sim.next_sample(4).energy_uwh == (v * i * 104) // PJ_PER_UWH_REF


def test_negative_energy_total_truncates_toward_zero() -> None:
    # Baseline at 5999 ms, then 1 ms at t = 6 s where current is negative.
    sim = Simulator()
    sim.next_sample(5999)
    v, i = simulated_values(6.0)
    total_pj = v * i * 1
    assert -PJ_PER_UWH_REF < total_pj < 0
    assert total_pj // PJ_PER_UWH_REF == -1  # the floor answer the code must avoid
    assert sim.next_sample(6000).energy_uwh == 0


# --- SimulatedSource ------------------------------------------------------------


def test_read_returns_the_simulators_frames() -> None:
    # A second, independent Simulator given the same Timestamps produces the
    # expected Samples, so the source's own Simulator is advanced only once.
    fake = FakeClock()
    source = make_source(fake)
    reference = Simulator()
    for k in range(5):
        frame = source.read()
        assert len(frame) == FRAME_LEN_BYTES
        assert decode_frame(frame) == reference.next_sample(k * SIM_SAMPLE_PERIOD_MS)
    source.close()


def test_read_sleeps_one_period_and_timestamps_are_100_ms_apart() -> None:
    fake = FakeClock(start_s=1000.0)
    source = make_source(fake)
    timestamps = [decode_frame(source.read()).timestamp_ms for _ in range(4)]
    assert timestamps == [0, 100, 200, 300]
    # First Frame is due at the first read, so no sleep for it.
    assert fake.sleeps == pytest.approx([PERIOD_S] * 3)


def test_late_read_never_sleeps_negative_and_stays_on_grid() -> None:
    fake = FakeClock(start_s=0.0)
    source = make_source(fake)
    source.read()  # due at 0.0
    fake.now_s = 0.25  # caller stalls past the 0.1 s and 0.2 s deadlines
    assert decode_frame(source.read()).timestamp_ms == 100
    assert decode_frame(source.read()).timestamp_ms == 200
    assert fake.sleeps == []  # behind schedule: returned at once, no negative sleep
    assert decode_frame(source.read()).timestamp_ms == 300
    # Slept only up to the 0.3 s grid point, not 0.1 s past the late read.
    assert fake.sleeps == pytest.approx([0.05])
    assert fake.now_s == pytest.approx(0.3)


# --- Property 18: Rated_Range -------------------------------------------------

# The Rated_Range and the INA219's reading steps, written out here rather than
# taken from simulator.py, so the test checks the simulator against the spec
# instead of against its own constants.
RATED_BUS_VOLTAGE_MIN_MV = 0
RATED_BUS_VOLTAGE_MAX_MV = 6000
BUS_VOLTAGE_STEP_MV = 4  # INA219 bus voltage LSB
RATED_CURRENT_MAX_UA = 1_000_000  # the Rated_Range is symmetric: +/-1 A
CURRENT_STEP_UA = 100  # current LSB set by the firmware's calibration
MAX_SIM_TIME_S = 1e7  # about 116 days, past the 49.7-day Timestamp wrap


# Feature: power-monitor, Property 18: Simulated values stay inside the Rated_Range
# Validates: Requirements 13.2
@given(st.floats(min_value=0.0, max_value=MAX_SIM_TIME_S, allow_nan=False, allow_infinity=False))
def test_simulated_values_stay_inside_rated_range(t_s: float) -> None:
    v_mv, i_ua = simulated_values(t_s)

    # `type(x) is int`, not isinstance: bool is a subclass of int, so
    # isinstance(True, int) is True; a float like 3700.0 must fail too.
    assert type(v_mv) is int
    assert type(i_ua) is int

    assert RATED_BUS_VOLTAGE_MIN_MV <= v_mv <= RATED_BUS_VOLTAGE_MAX_MV
    assert -RATED_CURRENT_MAX_UA <= i_ua <= RATED_CURRENT_MAX_UA

    # The real sensor can only report multiples of these steps. Holding the
    # simulator to the same grid makes its data look like the device's, so the
    # CSV, plot, and any later analysis are exercised with values the hardware
    # can actually produce.
    assert v_mv % BUS_VOLTAGE_STEP_MV == 0
    assert i_ua % CURRENT_STEP_UA == 0
