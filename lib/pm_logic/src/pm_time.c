/* Wrap-safe time helpers for the 32-bit millis() counter.
 *
 * These are ordinary functions in a .c file, not `static inline` in the
 * header: an inline function has no exported symbol, and the optional ctypes
 * property tests (task 30.3) load this library and call the functions by name.
 * The call overhead (microseconds at most) is irrelevant at 50 Hz. */

#include "pm_time.h"

uint32_t pm_elapsed_ms(uint32_t earlier_ms, uint32_t later_ms)
{
    /* C defines unsigned arithmetic modulo 2^N, so this subtraction can't
     * overflow; it wraps. Across a millis() wrap that is exactly the right
     * answer: 4 - 4,294,967,196 = 4 - (2^32 - 100) = 104 (mod 2^32), the
     * real 104 ms between them.
     * The cast is a no-op with 16-bit int (AVR) and 32-bit int (Mac), but
     * it keeps the result mod 2^32 even where int is wider than 32 bits and
     * uint32_t operands would be promoted to a signed int. */
    return (uint32_t)(later_ms - earlier_ms);
}

bool pm_deadline_reached(uint32_t now_ms, uint32_t deadline_ms)
{
    /* Why not `now_ms >= deadline_ms`: it breaks at the wrap. If now is
     * 4,294,967,290 and the next deadline has wrapped to 4 (10 ms ahead),
     * `now >= deadline` is already true and the Sample would fire 10 ms early.
     *
     * Instead, treat the counter as a circle of 2^32 ms. now - deadline
     * (mod 2^32) is how far now is ahead of deadline going forward around the
     * circle. A distance in the first half (0 .. 2^31 - 1) means "at or past
     * the deadline"; the second half (2^31 .. 2^32 - 1) means the deadline is
     * still ahead, by 2^32 minus that distance. In the example,
     * 4,294,967,290 - 4 = 4,294,967,286 is in the second half: not reached.
     * This only works while now and deadline are less than 2^31 ms
     * (about 24.8 days) apart; our deadlines are at most one Sample_Period
     * (1 s at 1 Hz) away, so the limit is never approached.
     *
     * It is the same test as the common `(int32_t)(now - deadline) >= 0`,
     * because the second half of the circle is exactly the set of uint32_t
     * values whose int32_t reading would be negative. But that cast converts
     * values above INT32_MAX to a signed type, which C99 leaves
     * implementation-defined. Comparing the unsigned difference against 2^31
     * stays in unsigned arithmetic, which C fully defines, so it means the same
     * on every compiler (the same reasoning as the current decode in pm_scaling.c). */
    return (uint32_t)(now_ms - deadline_ms) < PM_TIME_HALF_RANGE_MS;
}
