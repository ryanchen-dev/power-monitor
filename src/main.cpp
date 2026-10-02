/* Firmware entry point: scheduler and glue (Req 1.3, 2.3, 2.4, 6.4, 6.5, 7.6, 8.2, 8.3).
 *
 * Super-loop with a millis() schedule: there is no RTOS and no application
 * interrupt. The Arduino core calls setup() once, then loop() forever. Each
 * loop() call asks "has the next Sample's deadline arrived?" and returns at
 * once if not. When it has, it does one Sample from start to finish: one
 * sensor transaction, the logic, and one Frame handed to the UART. The only
 * interrupts are the core's own (Timer0 for millis(), USART TX, TWI).
 *
 * main.cpp only sequences calls. Decisions (fault policy, flags, seq, energy)
 * live in pm_sampler, hardware access in the two drivers. It uses neither
 * Serial nor Wire directly.
 *
 * Why no catch-up logic: the worst case for one Sample is about 11 ms (one
 * aborted I2C transaction plus the logic and the 25-byte copy into the TX
 * buffer), below the shortest period, 20 ms at 50 Hz. A Sample always ends
 * before the next deadline, so a backlog of missed deadlines can't build up,
 * and there is nothing to catch up on. */

#include <Arduino.h>      /* millis(), setup()/loop() entry points */

#include "ina219_driver.h"
#include "pm_config.h"    /* SAMPLE_PERIOD_MS */
#include "pm_frame.h"     /* pm_sample_t, PM_FRAME_LEN_BYTES, pm_frame_encode */
#include "pm_sampler.h"
#include "pm_scaling.h"   /* pm_raw_reading_t */
#include "pm_status.h"
#include "pm_time.h"      /* pm_deadline_reached */
#include "uart_driver.h"

/* Timing types: millis() returns unsigned long, which is 32 bits on the AVR,
 * the same as uint32_t. SAMPLE_PERIOD_MS is unsigned long too, because
 * MS_PER_S is 1000UL. So millis() + SAMPLE_PERIOD_MS and
 * next_sample_ms += SAMPLE_PERIOD_MS are computed in 32 bits, and no value in
 * the schedule ever passes through a 16-bit int. That matters: an unsigned
 * int here would be 16 bits on the AVR and wrap after 65,535 ms (about 65 s),
 * and a signed int would overflow even sooner, at 32,767 ms (undefined
 * behavior). Either would break the schedule within about a minute of boot.
 * The 32-bit counter wraps after about 49.7 days, which pm_deadline_reached
 * handles. */
static pm_sampler_t sampler;
static uint8_t      frame_buf[PM_FRAME_LEN_BYTES];
static uint32_t     next_sample_ms;

void setup() {
    /* uart_init has no failure path (see uart_driver.cpp), and there is no
     * other channel to report one on. */
    (void)uart_init();
    pm_status_t st = ina219_bus_begin();
    if (st == PM_OK) { st = ina219_configure(); }
    /* A failed boot configuration is not fatal: the sampler starts in FAULT,
     * so the first Sample is a re-init attempt and Frames flow regardless. */
    pm_sampler_init(&sampler, st == PM_OK);
    /* First Sample one period from now, so the INA219 finishes its first conversion pair.
     * After configuration the chip needs one bus and one shunt conversion
     * (about 1.06 ms at 12 bits) before its registers hold valid data. One
     * period (20 ms or more) covers that with margin, and the first Frame
     * then lands on the same fixed grid as every later one. */
    next_sample_ms = millis() + SAMPLE_PERIOD_MS;
}

void loop() {
    uint32_t now_ms = millis();
    /* Wrap-safe comparison: a plain now_ms >= next_sample_ms would misfire
     * when the deadline has wrapped past 2^32 and now_ms hasn't yet. */
    if (!pm_deadline_reached(now_ms, next_sample_ms)) { return; }
    /* Fixed rate: the next deadline is the previous deadline plus one period,
     * not now_ms + period. now_ms is a little late each time (loop() latency,
     * and millis() occasionally steps by 2 ms). With now_ms + period every
     * such delay would push all later Samples back and the mean interval
     * would drift long. Adding to the deadline keeps the schedule on a fixed
     * grid, so individual intervals jitter by about +/-1 ms but the error
     * doesn't accumulate and the mean interval is exact (Req 6.4). */
    next_sample_ms += SAMPLE_PERIOD_MS;      /* fixed rate: loop jitter does not accumulate */

    pm_raw_reading_t raw = {0u, 0u};
    pm_sensor_action_t action = pm_sampler_next_action(&sampler);
    pm_status_t result = (action == PM_ACTION_READ) ? ina219_read(&raw) : ina219_configure();

    /* A local, so it lives on the stack only while loop() runs and costs no
     * static RAM: Req 16.1 limits static RAM (.data + .bss), and the stack
     * reuses the same bytes for every call. It doesn't need to outlive one
     * Sample, because pm_frame_encode copies it into frame_buf. */
    pm_sample_t sample;
    pm_sampler_finish(&sampler, now_ms, action, result, &raw, &sample);
    if (pm_frame_encode(&sample, frame_buf, sizeof frame_buf) == PM_OK) {
        /* BUSY or WRITE failure: the Frame is skipped, its seq is already used, and the host
           counts it as a dropped Frame. The firmware has no other channel to report it on. */
        /* Ignored on purpose: the UART is the only output, and Req 8.3 forbids
         * text or debug output on it. The seq gap is the report. */
        (void)uart_write_frame(frame_buf, PM_FRAME_LEN_BYTES);
    }
}
