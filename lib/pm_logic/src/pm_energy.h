#ifndef PM_ENERGY_H
#define PM_ENERGY_H

#include <stdbool.h>
#include <stdint.h>

/* Same reason as pm_status.h: main.cpp (C++) reaches these through pm_sampler. */
#ifdef __cplusplus
extern "C" {
#endif

/* Energy_Integrator.
 *
 * Unit: mV x uA x ms = 1e-3 V x 1e-6 A x 1e-3 s = 1e-12 J = 1 pJ. So the
 * energy of one interval, V x I x dt, is an exact integer number of
 * picojoules: no rounding at all until it is reported.
 * 1 uWh = 1e-6 W x 3600 s = 3.6e-3 J = 3.6e9 pJ.
 *
 * Capacity (Req 5.4: 300 h at 6 W without reaching a limit):
 *
 *   quantity                                        value
 *   300 h x 6 W                                     1,800,000,000 uWh = 6.48e18 pJ
 *   report / accumulator limit                      2,147,483,647 uWh = 7.73e18 pJ (84% used by 300 h)
 *   INT64_MAX                                       9.22e18 (acc is kept within +/- limit, never reached)
 *   largest normal interval (6 V x 1 A x 1000 ms)   6e12 pJ ~ 1,667 uWh
 *   millis() wrap period                            2^32 ms ~ 49.7 days, longer than 300 h
 *
 * The accumulator limit is chosen so that the reported value always fits the
 * int32_t energy_uwh field of the Frame. */
#define PJ_PER_UWH              INT64_C(3600000000)
#define ENERGY_REPORT_MAX_UWH   INT32_MAX                         /* 2,147,483,647 uWh ~ 2147 Wh */
#define ENERGY_ACC_LIMIT_PJ     ((int64_t)ENERGY_REPORT_MAX_UWH * PJ_PER_UWH)  /* 7.73e18 pJ */

typedef struct {
    int64_t  acc_pj;             /* exact net energy in pJ (= mV * uA * ms), within +/- ENERGY_ACC_LIMIT_PJ */
    uint32_t prev_timestamp_ms;
    bool     prev_valid;         /* previous Sample had SENSOR_ERROR clear */
    bool     saturated;          /* latched when the limit was reached */
} pm_energy_state_t;

/* Energy = 0 and no previous Sample (Req 5.6). `s` must not be NULL. */
void pm_energy_init(pm_energy_state_t *s);

/* Call once per Sample, valid or not. Adds V x I x dt for the interval ending
 * at this Sample when this Sample and the previous one are both valid. */
void pm_energy_update(pm_energy_state_t *s, uint32_t timestamp_ms, bool sample_valid,
                      uint16_t bus_voltage_mv, int32_t current_ua);

/* Net energy in uWh: acc_pj / PJ_PER_UWH, truncated toward zero. */
int32_t pm_energy_uwh(const pm_energy_state_t *s);

/* True once the accumulator has hit its limit (latched). */
bool pm_energy_is_saturated(const pm_energy_state_t *s);

#ifdef __cplusplus
}
#endif

#endif /* PM_ENERGY_H */
