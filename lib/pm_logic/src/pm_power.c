/* Power_Calculator.
 *
 * Units: mV x uA = 1e-3 V x 1e-6 A = 1e-9 W, so the integer product is an
 * exact power in nanowatts. Converting to mW is one division by 1,000,000.
 *
 * One rounded division, not two. Going nW -> uW -> mW with rounding at each
 * step can be off by more than half an LSB, because the first rounding can
 * push a value onto the next half-way point: 1,499,600 nW rounds to 1,500 uW,
 * which rounds to 2 mW, but the exact 1.4996 mW rounds to 1 mW. Dividing once
 * keeps the error at most 0.5 mW, inside Req 4.2's +/-1 mW.
 *
 * Overflow analysis over the full input domain (Req 4.3), not just the
 * Rated_Range, because the function accepts any uint16_t and int32_t:
 *
 *   quantity          bound                                    needs                 type
 *   bus_voltage_mv    0 .. 65,535                              16 bits unsigned      uint16_t
 *   current_ua        -2^31 .. 2^31 - 1                        32 bits signed        int32_t
 *   p_nw              |p| <= 65,535 x 2^31 = 2^47 - 2^31       48 bits signed        int64_t (max 9.22e18)
 *                         ~ 1.41e14
 *   p_nw +/- 500,000  ~ 1.41e14                                48 bits signed        int64_t
 *   p_mw              |p| <= 140,735,341 ~ 1.41e8              29 bits signed        int32_t (max 2.15e9)
 *
 * So int64_t holds the largest product with about 65,000x headroom,
 * and the final result always fits int32_t, so the closing cast never
 * changes the value. In practice (INA219 outputs) |p| <= 107 W, and inside
 * the Rated_Range |p| <= 6000 mW. */

#include "pm_power.h"

int32_t pm_power_mw(uint16_t bus_voltage_mv, int32_t current_ua)
{
    /* Widen before multiplying: the cast makes both operands int64_t, so the
     * multiply itself is 64-bit. Multiplying first in 32 bits would overflow
     * (6000 mV x 1,000,000 uA = 6e9 already exceeds INT32_MAX). */
    int64_t p_nw = (int64_t)bus_voltage_mv * current_ua;   /* mV x uA = nW, exact */
    int64_t half_nw = NW_PER_MW / 2;                       /* 0.5 mW in nW */
    int64_t p_mw;

    /* Round half away from zero. C division truncates toward zero (C99), so
     * x / 1e6 alone would round 1.9 mW down to 1 and -1.9 mW up to -1.
     * Adding half before dividing moves the cut-off from x.0 to x.5: for a
     * positive value, (p + 0.5 mW) / 1 mW truncated is round-to-nearest with
     * x.5 going up. Negative values need the mirror image, subtracting half,
     * so -x.5 goes down (away from zero). Biasing in the sign's direction is
     * what makes the rule symmetric: the result for -p is always -(result for p). */
    if (p_nw >= 0) {
        p_mw = (p_nw + half_nw) / NW_PER_MW;
    } else {
        p_mw = (p_nw - half_nw) / NW_PER_MW;
    }
    return (int32_t)p_mw;   /* |p_mw| <= 140,735,341: always fits (table above) */
}
