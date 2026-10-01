/* Energy_Integrator. Units and the capacity table are in pm_energy.h.
 *
 * Why exact integers: every interval's energy is an exact number of pJ and
 * integer addition is exact, so the running total is the exact sum. The only
 * rounding is the single division in pm_energy_uwh, so the reported error is
 * below 1 uWh however long the run. Rounding each interval to uWh instead
 * would accumulate: at 6 W and 10 Hz each 100 ms step is 166.67 uWh, and
 * truncating every step to 166 loses 0.67 uWh per step, about 7.2 Wh over
 * 300 h (10.8 million steps). */

#include "pm_energy.h"

#include "pm_time.h"

void pm_energy_init(pm_energy_state_t *s)
{
    s->acc_pj = 0;
    s->prev_timestamp_ms = 0u;
    s->prev_valid = false;   /* so the first Sample only sets the baseline time */
    s->saturated = false;
}

void pm_energy_update(pm_energy_state_t *s, uint32_t timestamp_ms, bool sample_valid,
                      uint16_t bus_voltage_mv, int32_t current_ua)
{
    /* Right-endpoint rule (Req 5.1): the interval from the previous Sample to
     * this one is credited with this Sample's V and I (a right-endpoint
     * Riemann sum), and only when both ends are valid. If this Sample is an
     * error, it has no real V or I to use. If the previous one was, we don't
     * know when the sensor came back or what the load did during the fault,
     * so the interval is skipped and the error Sample's successor only sets a
     * new baseline time. Skipping undercounts the gap rather than inventing
     * energy that was never measured.
     *
     * OVERRANGE Samples are still integrated: their V and I are real
     * measurements, only outside the rated accuracy range. This function
     * doesn't see flags at all; pm_sampler passes sample_valid = "read OK". */
    if (sample_valid && s->prev_valid && !s->saturated) {
        uint32_t dt_ms = pm_elapsed_ms(s->prev_timestamp_ms, timestamp_ms);   /* modular, wrap-safe */
        int64_t  p_nw  = (int64_t)bus_voltage_mv * current_ua;                /* |p| < 2^47 */

        /* Nothing to add, and it keeps the later division by dt_ms (18.2) safe. */
        if (p_nw != 0 && dt_ms != 0) {
            /* Work with the magnitude in uint64_t so the multiply below is
             * unsigned. -p_nw cannot overflow: |p_nw| < 2^47. */
            uint64_t mag = (p_nw > 0) ? (uint64_t)p_nw : (uint64_t)(-p_nw);

            /* TODO(task 18.2): check before multiplying. If mag > room / dt_ms,
             * clamp acc_pj to +/-ENERGY_ACC_LIMIT_PJ and latch `saturated`.
             * Until then, mag * dt_ms is only safe for powers and intervals
             * inside the capacity table in pm_energy.h (|p| <= 6 W,
             * dt <= 1000 ms, totals within the limit): in general it can need
             * 47 + 32 = 79 bits, which exceeds uint64_t. */
            uint64_t delta = mag * dt_ms;
            s->acc_pj += (p_nw > 0) ? (int64_t)delta : -(int64_t)delta;
        }
    }

    /* Every Sample, valid or not, becomes the "previous" one for the next call. */
    s->prev_valid = sample_valid;
    s->prev_timestamp_ms = timestamp_ms;
}

int32_t pm_energy_uwh(const pm_energy_state_t *s)
{
    /* Truncation toward zero: C99 `/` truncates toward zero for negative
     * operands too, so -1.999 uWh reports as -1, not -2. That makes the report
     * symmetric (a net discharge of E reads as minus the reading for a charge
     * of E), matches the Packet_Spec ("truncated toward zero"), and keeps the
     * error below 1 uWh in magnitude either way.
     *
     * Why the quotient fits int32_t: acc_pj is kept within
     * +/-ENERGY_ACC_LIMIT_PJ = +/-(INT32_MAX x PJ_PER_UWH), so the quotient is
     * within +/-INT32_MAX. (Task 18.2 enforces that bound for every input;
     * until then it holds inside the capacity table.) */
    return (int32_t)(s->acc_pj / PJ_PER_UWH);
}

bool pm_energy_is_saturated(const pm_energy_state_t *s)
{
    /* Saturation arrives in task 18.2. */
    (void)s;
    return false;
}
