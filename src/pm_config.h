#ifndef PM_CONFIG_H
#define PM_CONFIG_H

/* Firmware compile-time configuration: sample rate, UART and I2C settings,
 * and the checks that stop the build when they are combined badly.
 *
 * Why #if / #error for the checks: a bad configuration is a build mistake, so
 * it should fail on the Mac, never on the board. The preprocessor runs before
 * the C or C++ compiler sees the code, so the same lines work unchanged in
 * main.cpp (C++) and in the host test's C compile. The language-level static
 * assertion is spelled differently in C11 (_Static_assert) and C++11
 * (static_assert) and doesn't exist in C99. The #error text is printed as-is,
 * so it can name the constant to change (Req 6.2, 6.3).
 *
 * This header holds only macros, so it needs no extern "C" guards: those
 * affect function and variable names at link time, and macros are replaced by
 * text before compilation. */

#include "pm_frame.h"                     /* PM_FRAME_LEN_BYTES */

/* Why #ifndef: it lets a build choose another value without editing this
 * file. -DSAMPLE_RATE_HZ=50 on the compiler command line (for example
 * PLATFORMIO_BUILD_FLAGS="-DSAMPLE_RATE_HZ=50") defines the macro before this
 * header is read, so the default #define below is skipped. Without the guard
 * the two definitions would collide: a "macro redefined" warning, and this
 * file's value would silently win. The host test uses the same mechanism to
 * try values that must fail. */
#ifndef SAMPLE_RATE_HZ                    /* overridable with -DSAMPLE_RATE_HZ=... */
#define SAMPLE_RATE_HZ          10
#endif
#define SAMPLE_RATE_MIN_HZ      1
#define SAMPLE_RATE_MAX_HZ      50
#define MS_PER_S                1000UL
/* Integer division truncates for rates that don't divide 1000: 3 Hz gives
 * 333 ms (a real rate of 3.003 Hz), 7 Hz gives 142 ms. Req 6.4 is measured
 * against this SAMPLE_PERIOD_MS, so the schedule is still exact by that
 * definition. The default 10 Hz and the 50 Hz maximum divide 1000 exactly. */
#define SAMPLE_PERIOD_MS        (MS_PER_S / SAMPLE_RATE_HZ)   /* truncates for rates that don't divide 1000 */

#ifndef UART_BAUD_BPS                     /* overridable the same way as SAMPLE_RATE_HZ */
#define UART_BAUD_BPS           115200UL
#endif
#define UART_BITS_PER_BYTE      10        /* start + 8 data + stop */
#define UART_MAX_LOAD_PERCENT   50
#define PERCENT_PER_WHOLE       100       /* converts a ratio to a percent: 0.5 x 100 = 50% */

#define I2C_CLOCK_HZ            100000UL
#define I2C_TIMEOUT_US          5000UL
#define US_PER_S                1000000UL

/* Req 6.2. */
#if (SAMPLE_RATE_HZ < SAMPLE_RATE_MIN_HZ) || (SAMPLE_RATE_HZ > SAMPLE_RATE_MAX_HZ)
#error "SAMPLE_RATE_HZ must be between 1 and 50"
#endif

/* Req 6.3: Frame traffic may use at most UART_MAX_LOAD_PERCENT of the UART
 * bit rate. As a ratio:
 *
 *     PM_FRAME_LEN_BYTES x UART_BITS_PER_BYTE x SAMPLE_RATE_HZ / UART_BAUD_BPS  <=  50 / 100
 *
 * Why multiply instead of divide: the preprocessor only does integer math,
 * and integer division truncates. Computing the load as a percent first
 * (traffic x 100 / baud) would round it down, so a 50.9% load would come out
 * as 50 and pass. Multiplying both sides by PERCENT_PER_WHOLE x UART_BAUD_BPS
 * gives the same inequality with no division at all, so it is exact:
 *
 *     traffic_bps x 100  >  baud x 50   ->  error
 *
 * The preprocessor evaluates #if in at least 64-bit arithmetic, so these
 * products can't overflow. With today's 25-byte Frame at 115200 baud the
 * check can't fire anywhere in 1..50 Hz (50 Hz: 1,250,000 vs 5,760,000, a
 * 10.9% load); it guards future Frame-length or baud changes. */
#if (PM_FRAME_LEN_BYTES * UART_BITS_PER_BYTE * SAMPLE_RATE_HZ * PERCENT_PER_WHOLE) > (UART_BAUD_BPS * UART_MAX_LOAD_PERCENT)
#error "Frame traffic exceeds 50% of the UART bit rate: lower SAMPLE_RATE_HZ or raise UART_BAUD_BPS"
#endif

/* Req 2.1: a hung I2C transaction must be abandoned before the next Sample is
 * due, so a bus fault costs one Sample's data but never shifts the schedule.
 * Checked against the 20 ms period at SAMPLE_RATE_MAX_HZ, not the current
 * rate, so it holds for every rate the first check allows. 5 ms is well
 * below 20 ms for a reason: the Wire timeout restarts in each of the driver's
 * wait loops, so a pathological transaction can take about 10 ms before it
 * aborts, and the logic and UART copy (about 0.6 ms) still have to fit after
 * it. That worst case, about 11 ms, stays under 20 ms. */
#if I2C_TIMEOUT_US >= (US_PER_S / SAMPLE_RATE_MAX_HZ)
#error "I2C_TIMEOUT_US must be shorter than the 20 ms Sample_Period at 50 Hz"
#endif

#endif /* PM_CONFIG_H */
