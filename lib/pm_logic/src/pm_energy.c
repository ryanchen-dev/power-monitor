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

/* Distance from acc up to +ENERGY_ACC_LIMIT_PJ, and down to -ENERGY_ACC_LIMIT_PJ.
 * Requires |acc| <= ENERGY_ACC_LIMIT_PJ, which pm_energy_update maintains.
 *
 * Why uint64_t: from acc = -limit the distance up is 2 x limit = 1.55e19,
 * which exceeds INT64_MAX (9.22e18) but fits uint64_t (max 1.84e19). The two
 * cases by sign keep every step in range: limit - acc with acc >= 0 can't go
 * negative, and -acc with acc < 0 can't overflow because acc >= -limit > INT64_MIN. */
static uint64_t headroom_up(int64_t acc)
{
    if (acc >= 0) {
        return (uint64_t)ENERGY_ACC_LIMIT_PJ - (uint64_t)acc;
    }
    return (uint64_t)ENERGY_ACC_LIMIT_PJ + (uint64_t)(-acc);
}

static uint64_t headroom_down(int64_t acc)
{
    if (acc <= 0) {
        return (uint64_t)ENERGY_ACC_LIMIT_PJ - (uint64_t)(-acc);
    }
    return (uint64_t)ENERGY_ACC_LIMIT_PJ + (uint64_t)acc;
}

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

        /* Nothing to add, and dt_ms != 0 makes the division below safe. */
        if (p_nw != 0 && dt_ms != 0) {
            /* Work with the magnitude in uint64_t so the multiply below is
             * unsigned. -p_nw cannot overflow: |p_nw| < 2^47. */
            uint64_t mag  = (p_nw > 0) ? (uint64_t)p_nw : (uint64_t)(-p_nw);
            uint64_t room = (p_nw > 0) ? headroom_up(s->acc_pj) : headroom_down(s->acc_pj);

            /* Check before multiplying: mag < 2^47 and dt_ms < 2^32, so
             * mag * dt_ms can need 79 bits and overflow even uint64_t (and
             * wrap silently to a wrong, small value). The test must not compute
             * that product.
             *
             * For non-negative integers with dt > 0, mag * dt <= room exactly
             * when mag <= floor(room / dt): if mag * dt <= room then
             * mag <= room / dt, and mag is an integer, so it is <= the floor;
             * conversely mag <= floor(room / dt) <= room / dt gives
             * mag * dt <= room. C's unsigned `/` is that floor, so
             * `mag > room / dt_ms` is exactly "the product would pass the limit". */
            if (mag > room / dt_ms) {
                s->acc_pj = (p_nw > 0) ? ENERGY_ACC_LIMIT_PJ : -ENERGY_ACC_LIMIT_PJ;
                /* Latched: the !s->saturated condition above stops all later
                 * integration (Req 5.5). Once the total has been clipped it is
                 * no longer the true energy, so letting it move again would
                 * produce values that look valid but aren't. Only a restart
                 * (pm_energy_init) clears it. */
                s->saturated = true;
            } else {
                uint64_t delta = mag * dt_ms;   /* <= room, so it cannot wrap */

                /* Added in two halves because delta <= room <= 2 x limit can
                 * exceed INT64_MAX (e.g. from acc = -limit), and converting
                 * such a value to int64_t is implementation-defined. Each half
                 * is <= limit < INT64_MAX, so both conversions are exact. The
                 * value after the first half lies between the old acc_pj and
                 * the final result, both within +/- limit, so neither
                 * addition overflows. */
                int64_t half1 = (int64_t)(delta / 2u);
                int64_t half2 = (int64_t)(delta - delta / 2u);
                if (p_nw > 0) {
                    s->acc_pj += half1;
                    s->acc_pj += half2;
                } else {
                    s->acc_pj -= half1;
                    s->acc_pj -= half2;
                }
            }
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
     * within +/-INT32_MAX for every input (pm_energy_update clamps to the
     * limit instead of passing it). */
    return (int32_t)(s->acc_pj / PJ_PER_UWH);
}

bool pm_energy_is_saturated(const pm_energy_state_t *s)
{
    return s->saturated;
}
