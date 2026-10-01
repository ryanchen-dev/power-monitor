/* Scaling_Module. See pm_scaling.h for the units, the calibration formula,
 * and the compile-time checks. */

#include "pm_scaling.h"

/* Two's-complement decode constants for the 16-bit current register. Private:
 * only this file decodes registers. Cast to explicit types so the comparison
 * and the subtraction below happen in the types we intend on both targets
 * (an unsuffixed 65536 would be a long on the AVR but an int on the Mac). */
#define INA219_CURRENT_REG_SIGN_MIN_COUNT  ((uint16_t)0x8000u)  /* smallest raw value that encodes a negative count */
#define INA219_CURRENT_REG_MODULUS_COUNT   ((int32_t)65536L)    /* 2^16: raw - 2^16 gives the negative count */

uint16_t pm_scale_bus_voltage_mv(uint16_t bus_reg)
{
    /* Shifting drops CNVR (bit 1), OVF (bit 0), and the unused bit 2, so the
     * status bits can never change the voltage. The largest result,
     * 8191 * 4 = 32,764, fits both a 16-bit int and the uint16_t return type. */
    return (uint16_t)((bus_reg >> BUS_REG_READING_SHIFT) * BUS_VOLTAGE_LSB_MV);
}

int32_t pm_scale_current_ua(uint16_t current_reg)
{
    int32_t counts;

    /* Explicit two's-complement decode instead of `(int16_t)current_reg`.
     * Converting a value that doesn't fit (here 0x8000..0xFFFF) to a signed
     * type is implementation-defined in C99: avr-gcc and clang happen to wrap,
     * but the standard doesn't promise it. In two's complement a raw value
     * with the top bit set means raw - 2^16 (0xFFFF -> -1, 0x8000 -> -32768),
     * and computing that in int32_t gives the same answer on every compiler. */
    if (current_reg >= INA219_CURRENT_REG_SIGN_MIN_COUNT) {
        counts = (int32_t)current_reg - INA219_CURRENT_REG_MODULUS_COUNT;
    } else {
        counts = (int32_t)current_reg;
    }

    /* |counts| <= 32,768 and the LSB is 100 uA, so |result| <= 3,276,800,
     * far inside int32_t. The cast keeps the multiply in int32_t on both
     * targets (CURRENT_LSB_UA is a long, which is 64 bits on the Mac). */
    return counts * (int32_t)CURRENT_LSB_UA;
}

bool pm_bus_math_overflow(uint16_t bus_reg)
{
    return (bus_reg & BUS_REG_OVF_MASK) != 0u;
}

bool pm_is_overrange(uint16_t bus_voltage_mv, int32_t current_ua)
{
    /* Strict comparisons: the boundary values belong to the Rated_Range
     * ("0 V to 6 V, -1 A to +1 A" includes its endpoints), and 6000 mV is a
     * whole number of 4 mV steps, so the hardware can report it exactly.
     * Flagging a reading of exactly 6.000 V would call a rated value overrange. */
    if (bus_voltage_mv > RATED_BUS_VOLTAGE_MAX_MV) {
        return true;
    }

    /* Two direct comparisons instead of abs()/labs(): abs() takes an int,
     * which is 16 bits on the AVR and would truncate the current, and
     * |INT32_MIN| = 2^31 has no int32_t representation, so abs(INT32_MIN) is
     * signed overflow (undefined behavior, and a trap in the native tests).
     * Negating the constant limit instead is always safe. */
    if (current_ua > RATED_CURRENT_MAX_UA || current_ua < -RATED_CURRENT_MAX_UA) {
        return true;
    }
    return false;
}
