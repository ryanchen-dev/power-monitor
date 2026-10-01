#ifndef INA219_DRIVER_H
#define INA219_DRIVER_H

#include "pm_scaling.h"   /* pm_raw_reading_t, INA219_CAL_VALUE */
#include "pm_status.h"

/* INA219_Driver: register-level driver on the Arduino Wire library (Req 1.5).
 *
 * Why this header is Arduino-free: it holds only constants, the prototypes,
 * and project types, so it is valid C and the native Unity build (which has no
 * Arduino headers) can include it and check INA219_CONFIG_VALUE on the Mac.
 * Only ina219_driver.cpp includes <Wire.h>. */

/* Same reason as uart_driver.h: the functions are compiled as C++ in
 * ina219_driver.cpp and get unmangled C names, like every project function. */
#ifdef __cplusplus
extern "C" {
#endif

/* 7-bit I2C address with the breakout's A0 and A1 left at GND (default). */
#define INA219_I2C_ADDR          0x40

/* Register addresses (datasheet register map). */
#define INA219_REG_CONFIG        0x00   /* written at init, read back to verify */
#define INA219_REG_BUS_VOLTAGE   0x02   /* reading in bits 15..3, CNVR bit 1, OVF bit 0 */
#define INA219_REG_CURRENT       0x04   /* signed two's complement, CURRENT_LSB_UA per count */
#define INA219_REG_CALIBRATION   0x05   /* written at init, read back to verify */

/* Configuration register fields.
 *
 * How each field lands at its bit position: the datasheet gives each field a
 * bit range (BRNG bit 13, PG bits 12..11, BADC bits 10..7, SADC bits 6..3,
 * MODE bits 2..0). Shifting the field's value left by the number of its lowest
 * bit puts it in that range, and since the ranges don't overlap, OR-ing the
 * shifted fields builds the register:
 *
 *     BRNG 0   << 13 = 0x0000
 *     PG   2   << 11 = 0x1000   (binary 10)
 *     BADC 3   <<  7 = 0x0180   (binary 0011)
 *     SADC 3   <<  3 = 0x0018   (binary 0011)
 *     MODE 7   <<  0 = 0x0007   (binary 111)
 *                      ------
 *                      0x119F
 *
 * Bits 15 (RST) and 14 (unused) stay 0. The result differs from the reset
 * value 0x399F only in BRNG (1 -> 0) and PG (11 -> 10).
 *
 * Each shifted constant is at most 0x1000, which fits in 16 bits, so plain
 * unsigned literals are safe even though unsigned int is 16 bits on the AVR. */

/* BRNG = 0: 16 V bus full scale. It is the smallest range that covers the
 * 6 V Rated_Range. It does not improve resolution: the bus LSB is 4 mV in
 * both the 16 V and 32 V ranges. */
#define INA219_CONFIG_BRNG_16V              (0u << 13)

/* PG = 10: PGA /4, +/-160 mV shunt range.
 * The PGA (programmable gain amplifier) sits in front of the shunt ADC, whose
 * native span is +/-40 mV. It divides the input by 1, 2, 4, or 8 so larger
 * shunt voltages fit. At the rated 1 A the 0.1 Ohm shunt drops 100 mV, which
 * exceeds /2 (+/-80 mV). /4 fits it with headroom to about 1.6 A, so a
 * reading above the Rated_Range is measured and flagged OVERRANGE instead of
 * clipping. /8 (+/-320 mV) would also fit but has a worse offset spec
 * (+/-200 uV vs +/-150 uV max), and offset dominates at low currents. */
#define INA219_CONFIG_PGA_DIV4_160MV        (2u << 11)

/* BADC and SADC = 0011: 12-bit, 1 sample, 532 us conversion each. */
#define INA219_CONFIG_BADC_12BIT            (3u << 7)
#define INA219_CONFIG_SADC_12BIT            (3u << 3)

/* MODE = 111: shunt and bus, continuous, so each read returns a fresh value
 * without a trigger write. */
#define INA219_CONFIG_MODE_SHUNT_BUS_CONT   (7u << 0)

#define INA219_CONFIG_VALUE  (INA219_CONFIG_BRNG_16V | INA219_CONFIG_PGA_DIV4_160MV | \
                              INA219_CONFIG_BADC_12BIT | INA219_CONFIG_SADC_12BIT | \
                              INA219_CONFIG_MODE_SHUNT_BUS_CONT)          /* 0x119F */

/* Starts the I2C bus: Wire.begin(), setClock(I2C_CLOCK_HZ),
 * setWireTimeout(I2C_TIMEOUT_US, true). */
pm_status_t ina219_bus_begin(void);

/* Writes CONFIG and CAL, then reads both back (PM_ERR_SENSOR_VERIFY on mismatch). */
pm_status_t ina219_configure(void);

/* Reads the bus register, then the current register, into *out.
 * Returns at the first failure. */
pm_status_t ina219_read(pm_raw_reading_t *out);

#ifdef __cplusplus
}
#endif

#endif /* INA219_DRIVER_H */
