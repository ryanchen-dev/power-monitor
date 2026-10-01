/* pm_sampler: per-Sample fault policy, flags, and sequence numbering.
 *
 * Why this is in the logic library and not in main.cpp: these are decisions
 * (when to re-init, what a fault Sample contains, which flags to set, how seq
 * advances), not hardware access. Here they compile on the Mac and are unit
 * tested with no sensor attached; in main.cpp they could only be checked on
 * the bench. main.cpp is left with "call the driver, pass the result here,
 * send the Frame".
 *
 * Rule table (design.md):
 *
 *   action   driver result   Sample fields                          sensor after
 *   READ     PM_OK           V, I scaled; P computed; OVERRANGE?     OK
 *   READ     not OK          SENSOR_ERROR; V = I = P = 0 (Req 2.2)   FAULT
 *   REINIT   PM_OK           SENSOR_ERROR; V = I = P = 0             OK: next Sample reads (Req 2.5)
 *   REINIT   not OK          SENSOR_ERROR; V = I = P = 0             FAULT: retry next period (Req 2.4)
 *
 * So the sensor state after every call is simply "driver_result == PM_OK". */

#include "pm_sampler.h"

#include "pm_power.h"

void pm_sampler_init(pm_sampler_t *s, bool sensor_configured)
{
    s->sensor_ok = sensor_configured;
    s->next_seq = 0u;
    pm_energy_init(&s->energy);
}

pm_sensor_action_t pm_sampler_next_action(const pm_sampler_t *s)
{
    return s->sensor_ok ? PM_ACTION_READ : PM_ACTION_REINIT;
}

void pm_sampler_finish(pm_sampler_t *s, uint32_t timestamp_ms, pm_sensor_action_t action,
                       pm_status_t driver_result, const pm_raw_reading_t *raw, pm_sample_t *out)
{
    /* Only a successful read has real measurements. A successful REINIT
     * still reports SENSOR_ERROR: after configuration the INA219 needs one
     * conversion pair (about 1.06 ms) before its registers hold valid data,
     * and this period has no reading anyway. The next Sample, one period
     * later, reads normally. */
    bool     valid = (action == PM_ACTION_READ) && (driver_result == PM_OK);
    uint8_t  flags = 0u;              /* built from zero, so reserved bits 3..7 stay 0 */
    uint16_t bus_voltage_mv = 0u;
    int32_t  current_ua = 0;
    int32_t  power_mw = 0;

    if (valid) {
        bus_voltage_mv = pm_scale_bus_voltage_mv(raw->bus_reg);
        current_ua = pm_scale_current_ua(raw->current_reg);
        power_mw = pm_power_mw(bus_voltage_mv, current_ua);
        if (pm_bus_math_overflow(raw->bus_reg) || pm_is_overrange(bus_voltage_mv, current_ua)) {
            flags = (uint8_t)(flags | PM_FLAG_OVERRANGE);
        }
    } else {
        /* SENSOR_ERROR and OVERRANGE are never set together: an error Sample
         * carries V = I = P = 0 (Req 2.2), which is inside the Rated_Range,
         * and there is no register to have an OVF bit. Keeping OVERRANGE to
         * READ + PM_OK also keeps each flag's meaning unambiguous on the host. */
        flags = (uint8_t)(flags | PM_FLAG_SENSOR_ERROR);
    }

    s->sensor_ok = (driver_result == PM_OK);

    /* Called for every Sample so an error Sample breaks the interval chain
     * (pm_energy re-baselines on the next valid Sample). V and I are 0 when
     * invalid; pm_energy ignores them then anyway. */
    pm_energy_update(&s->energy, timestamp_ms, valid, bus_voltage_mv, current_ua);
    if (pm_energy_is_saturated(&s->energy)) {
        /* Can accompany either SENSOR_ERROR or OVERRANGE: it describes the
         * accumulator, not this Sample's reading. */
        flags = (uint8_t)(flags | PM_FLAG_ENERGY_SATURATED);
    }

    /* A complete Sample is produced on every call, whatever happened, so
     * main.cpp sends one Frame per period even during a fault (Req 2.3) and the
     * host keeps seeing seq and Timestamps advance. */
    out->seq = s->next_seq;
    out->timestamp_ms = timestamp_ms;          /* Req 6.5 */
    out->status_flags = flags;
    out->bus_voltage_mv = bus_voltage_mv;
    out->current_ua = current_ua;
    out->power_mw = power_mw;
    out->energy_uwh = pm_energy_uwh(&s->energy);

    s->next_seq = pm_frame_next_seq(s->next_seq);   /* Req 7.5 */
}
