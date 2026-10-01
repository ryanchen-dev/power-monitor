#ifndef PM_SAMPLER_H
#define PM_SAMPLER_H

#include <stdbool.h>
#include <stdint.h>

#include "pm_energy.h"
#include "pm_frame.h"
#include "pm_scaling.h"
#include "pm_status.h"

/* Same reason as pm_status.h: main.cpp (C++) calls these C functions. */
#ifdef __cplusplus
extern "C" {
#endif

/* What main.cpp should do with the sensor this Sample_Period. */
typedef enum {
    PM_ACTION_READ,     /* sensor OK: read the bus and current registers */
    PM_ACTION_REINIT    /* sensor in fault: try to configure it again (Req 2.4) */
} pm_sensor_action_t;

/* Per-Sample state. Tests may write these fields directly. */
typedef struct {
    bool              sensor_ok;  /* false: SENSOR_ERROR state, re-init each period */
    uint16_t          next_seq;   /* Sequence_Number for the next Sample */
    pm_energy_state_t energy;
} pm_sampler_t;

/* seq = 0 (Req 7.6), Energy = 0 (Req 5.6). `sensor_configured` is the result
 * of the boot-time configuration: false makes the first action REINIT. */
void pm_sampler_init(pm_sampler_t *s, bool sensor_configured);

/* READ if the sensor is OK, otherwise REINIT. */
pm_sensor_action_t pm_sampler_next_action(const pm_sampler_t *s);

/* Builds this period's Sample from the action main.cpp took and the driver's
 * result, then advances the energy and sequence state.
 * `s` and `out` must not be NULL. Every field of *out is written on every call.
 * `raw` is read only when action == PM_ACTION_READ and driver_result == PM_OK;
 * for every other combination it is ignored and may be NULL. */
void pm_sampler_finish(pm_sampler_t *s, uint32_t timestamp_ms, pm_sensor_action_t action,
                       pm_status_t driver_result, const pm_raw_reading_t *raw, pm_sample_t *out);

#ifdef __cplusplus
}
#endif

#endif /* PM_SAMPLER_H */
