#ifndef PM_SCALING_H
#define PM_SCALING_H

#include <stdbool.h>
#include <stdint.h>

/* Same reason as pm_status.h: main.cpp and the INA219 driver (C++) use these. */
#ifdef __cplusplus
extern "C" {
#endif

/* Scaling_Module: raw INA219 registers -> fixed-point engineering units.
 *
 * Fixed point: instead of storing 5.000 V as a float, store the integer 5000
 * with an agreed unit (mV). The math stays in integers, which is exact, fast
 * on an 8-bit CPU with no FPU, and identical on the AVR and the Mac. The design
 * work is picking units fine enough for the resolution needed and types wide
 * enough that nothing overflows.
 *
 * Why the L suffixes: int is 16 bits on the AVR (max 32,767), and C picks the
 * type of `a * b` from the operands, not from where the result is stored. With
 * plain int operands, 32767 * 100 overflows a 16-bit int even though the answer
 * fits in a long. Making every operand a long (32 bits on the AVR) keeps these
 * products in 32-bit arithmetic on both targets. The `#if` checks below would
 * be safe without them (the preprocessor computes in at least 64 bits), but
 * the same macros are also used in ordinary C expressions. */

#define SHUNT_RESISTANCE_MOHM           100L
#define SHUNT_ADC_LSB_UV                10L         /* INA219 shunt voltage register LSB (datasheet) */
#define MAX_EXPECTED_CURRENT_UA         1000000L    /* Rated_Range: +/-1 A */
#define UA_PER_MA                       1000L
#define INA219_CURRENT_REG_MAX_COUNT    32767L      /* 2^15 - 1: largest positive signed 16-bit register value */

/* uV / mOhm = mA, so x UA_PER_MA gives uA. 10 uV / 100 mOhm = 100 uA, one shunt ADC step.
 * Derived from the shunt ADC step, not rounded up from the datasheet's minimum
 * MAX_EXPECTED_CURRENT_UA / 2^15 (about 30.5 uA): the shunt register cannot
 * resolve anything finer than 10 uV, i.e. 100 uA through 0.1 Ohm, so a finer
 * LSB would only add register bits that never carry information (with a 50 uA
 * LSB every reading would be even). MAX_EXPECTED_CURRENT_UA is still used, in
 * the headroom check below, as the datasheet procedure requires. */
#define CURRENT_LSB_UA                  (SHUNT_ADC_LSB_UV * UA_PER_MA / SHUNT_RESISTANCE_MOHM)

/* Calibration register. The INA219 computes Current_reg = Shunt_reg * CAL / 4096
 * internally. Setting Current_reg * Current_LSB equal to the real current,
 * Shunt_reg * 10 uV / R_shunt, and solving gives the datasheet formula
 * CAL = 0.04096 V / (Current_LSB * R_shunt), where 0.04096 V = 4096 * 10 uV.
 * In integer units, with Current_LSB in uA (1e-6 A) and R_shunt in mOhm (1e-3 Ohm):
 * 0.04096 / (I * 1e-6 * R * 1e-3) = 40,960,000 / (I * R).
 * Here 40,960,000 / (100 * 100) = 4096, so CAL = 4096, the INA219's multiply is
 * by exactly 1, and the current register equals the shunt register. */
#define INA219_CAL_NUMERATOR_UA_MOHM    40960000L   /* 0.04096 V expressed in uA * mOhm */
#define INA219_CAL_VALUE  (INA219_CAL_NUMERATOR_UA_MOHM / (CURRENT_LSB_UA * SHUNT_RESISTANCE_MOHM))

/* Compile-time checks. Each one stops the build if someone edits the shunt or
 * current constants into a combination the integer math cannot handle exactly. */

/* Protects CURRENT_LSB_UA: integer division would silently truncate it (for
 * example a 150 mOhm shunt gives 66.67 uA -> 66 uA), and every current reading
 * would then be scaled by the wrong LSB. */
#if ((SHUNT_ADC_LSB_UV * UA_PER_MA) % SHUNT_RESISTANCE_MOHM) != 0
#error "SHUNT_ADC_LSB_UV * UA_PER_MA must be divisible by SHUNT_RESISTANCE_MOHM, or CURRENT_LSB_UA is inexact"
#endif
/* Datasheet: Minimum_LSB = Max_Expected_I / 2^15. The register tops out at INA219_CURRENT_REG_MAX_COUNT
   counts, so CURRENT_LSB_UA * INA219_CURRENT_REG_MAX_COUNT is the largest positive current it can report.
   Protects the Rated_Range: if this fails, currents near 1 A would wrap the register instead of reading. */
#if (CURRENT_LSB_UA * INA219_CURRENT_REG_MAX_COUNT) < MAX_EXPECTED_CURRENT_UA
#error "CURRENT_LSB_UA * INA219_CURRENT_REG_MAX_COUNT < MAX_EXPECTED_CURRENT_UA: current register cannot reach it; lower SHUNT_RESISTANCE_MOHM"
#endif
/* Protects INA219_CAL_VALUE: a truncated CAL makes the chip's internal
 * Shunt * CAL / 4096 no longer match CURRENT_LSB_UA, a gain error in every reading. */
#if (INA219_CAL_NUMERATOR_UA_MOHM % (CURRENT_LSB_UA * SHUNT_RESISTANCE_MOHM)) != 0
#error "CURRENT_LSB_UA * SHUNT_RESISTANCE_MOHM must divide 40960000 exactly, or current scaling is inexact"
#endif
/* Protects the register write: CAL is a 16-bit register whose bit 0 is
 * read-only zero, so an odd value would be stored as value - 1 (and fail the
 * driver's read-back), and anything above 0xFFFE cannot be stored at all. */
#if (INA219_CAL_VALUE > 0xFFFE) || ((INA219_CAL_VALUE % 2) != 0)
#error "INA219_CAL_VALUE must be even and fit in 16 bits (calibration bit 0 is read-only zero)"
#endif

/* Bus voltage register. No L suffixes needed: the largest bus value is
 * 8191 counts * 4 mV = 32,764 mV, which fits a 16-bit int. */
#define BUS_VOLTAGE_LSB_MV          4
#define BUS_REG_READING_SHIFT       3        /* bits 15..3 hold the reading */
#define BUS_REG_OVF_MASK            0x0001u  /* math overflow flag */

/* Rated_Range limits. The lower bus limit, 0 mV, needs no check because
 * bus_voltage_mv is unsigned. */
#define RATED_BUS_VOLTAGE_MAX_MV    6000
#define RATED_CURRENT_MAX_UA        MAX_EXPECTED_CURRENT_UA  /* one source of truth for 1 A */

/* The two registers read each Sample, exactly as the INA219 returned them. */
typedef struct {
    uint16_t bus_reg;      /* register 0x02: reading in bits 15..3, CNVR bit 1, OVF bit 0 */
    uint16_t current_reg;  /* register 0x04: signed two's complement, CURRENT_LSB_UA per count */
} pm_raw_reading_t;

/* (bus_reg >> 3) * 4 mV. Ignores the CNVR and OVF bits. Range 0..32,764 mV. */
uint16_t pm_scale_bus_voltage_mv(uint16_t bus_reg);

/* signed16(current_reg) * CURRENT_LSB_UA, sign preserved.
 * Range -3,276,800..+3,276,700 uA. */
int32_t pm_scale_current_ua(uint16_t current_reg);

/* True when the bus register's OVF (math overflow) bit is set. */
bool pm_bus_math_overflow(uint16_t bus_reg);

/* True when bus_voltage_mv > RATED_BUS_VOLTAGE_MAX_MV or current_ua lies
 * outside +/-RATED_CURRENT_MAX_UA. The limits themselves are in range.
 * Defined for every int32_t current, including INT32_MIN. */
bool pm_is_overrange(uint16_t bus_voltage_mv, int32_t current_ua);

#ifdef __cplusplus
}
#endif

#endif /* PM_SCALING_H */
