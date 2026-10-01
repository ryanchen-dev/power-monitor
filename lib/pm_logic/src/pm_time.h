#ifndef PM_TIME_H
#define PM_TIME_H

#include <stdbool.h>
#include <stdint.h>

/* Same reason as pm_status.h: main.cpp (C++) calls these C functions. */
#ifdef __cplusplus
extern "C" {
#endif

/* Half of the 32-bit millis() circle: 2^31 ms, about 24.8 days.
 * pm_deadline_reached is valid while `now` and `deadline` are less than this apart. */
#define PM_TIME_HALF_RANGE_MS  UINT32_C(0x80000000)

/* Milliseconds from `earlier_ms` to `later_ms`, correct across one millis()
 * wrap. Valid for intervals up to 2^32 - 1 ms (about 49.7 days). */
uint32_t pm_elapsed_ms(uint32_t earlier_ms, uint32_t later_ms);

/* True once `now_ms` has reached `deadline_ms` (equal counts as reached),
 * correct across a millis() wrap. Valid while the two are less than
 * PM_TIME_HALF_RANGE_MS apart. */
bool pm_deadline_reached(uint32_t now_ms, uint32_t deadline_ms);

#ifdef __cplusplus
}
#endif

#endif /* PM_TIME_H */
