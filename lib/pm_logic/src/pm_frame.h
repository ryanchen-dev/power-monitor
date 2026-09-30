#ifndef PM_FRAME_H
#define PM_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "pm_status.h"

/* Same reason as pm_status.h: main.cpp (C++) calls these C functions. */
#ifdef __cplusplus
extern "C" {
#endif

/* Frame layout constants from docs/packet_format.md (the Packet_Spec).
 * PM_FRAME_LEN_BYTES is a plain integer, not a cast or a sizeof expression,
 * because the preprocessor only understands integer literals: compile-time
 * checks such as `#if` on the UART bandwidth must be able to use it. */
#define PM_FRAME_LEN_BYTES        25
#define PM_FRAME_SYNC0            0xAAu
#define PM_FRAME_SYNC1            0x55u
#define PM_FRAME_CRC_START_OFFSET 2      /* CRC covers the length byte ... */
#define PM_FRAME_CRC_OFFSET       24     /* ... through byte 23; CRC itself at 24 */

/* Status_Flags bits. Bits 3..7 are reserved and always 0 from firmware. */
#define PM_FLAG_SENSOR_ERROR      0x01u
#define PM_FLAG_OVERRANGE         0x02u
#define PM_FLAG_ENERGY_SATURATED  0x04u

/* In-memory Sample. Never sent as raw bytes: see pm_frame.c. */
typedef struct {
    uint16_t seq;              /* frame counter, wraps 65535 -> 0 */
    uint32_t timestamp_ms;     /* millis() when the Sample was taken */
    uint8_t  status_flags;     /* PM_FLAG_* bits; bits 3..7 always 0 */
    uint16_t bus_voltage_mv;   /* 0 when SENSOR_ERROR */
    int32_t  current_ua;       /* + = source to load; 0 when SENSOR_ERROR */
    int32_t  power_mw;         /* 0 when SENSOR_ERROR */
    int32_t  energy_uwh;       /* net since start, truncated toward zero */
} pm_sample_t;

/* Encodes `sample` as one Frame into `out`. `sample` and `out` must not be NULL.
 * If out_size < PM_FRAME_LEN_BYTES, returns PM_ERR_BUFFER_TOO_SMALL and writes
 * nothing. Otherwise writes exactly PM_FRAME_LEN_BYTES bytes (any bytes of `out`
 * beyond that are left alone) and returns PM_OK. */
pm_status_t pm_frame_encode(const pm_sample_t *sample, uint8_t *out, size_t out_size);

/* Returns the Sequence_Number after `seq`: seq + 1, with 65535 -> 0. */
uint16_t pm_frame_next_seq(uint16_t seq);

#ifdef __cplusplus
}
#endif

#endif /* PM_FRAME_H */
