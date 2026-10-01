#ifndef PM_POWER_H
#define PM_POWER_H

#include <stdint.h>

/* Same reason as pm_status.h: main.cpp (C++) calls this C function. */
#ifdef __cplusplus
extern "C" {
#endif

/* mV x uA = 1e-3 V x 1e-6 A = 1e-9 W = 1 nW, so the raw product is in nW.
 * 64-bit so the division in pm_power.c happens in int64_t on both targets. */
#define NW_PER_MW  INT64_C(1000000)

/* Power_Calculator: bus_voltage_mv x current_ua, in mW, rounded half away
 * from zero (|error| <= 0.5 mW). Sign follows current_ua. Defined, with no
 * intermediate overflow, for every uint16_t voltage and int32_t current. */
int32_t pm_power_mw(uint16_t bus_voltage_mv, int32_t current_ua);

#ifdef __cplusplus
}
#endif

#endif /* PM_POWER_H */
