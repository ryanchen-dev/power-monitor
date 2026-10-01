/* INA219_Driver: register-level access to the INA219 over the Arduino Wire
 * library (Req 1.4, 1.5, 2.1). The only file that includes <Wire.h>.
 *
 * Why each function stops at the first failure and never retries: it bounds
 * the worst-case Sample time. After one failed transaction the rest are
 * skipped, so one Sample waits on at most one aborted transaction (about 5 ms,
 * about 10 ms pathologically) and stays inside the 20 ms period at 50 Hz. A
 * retry loop here would multiply that bound. Recovery is still needed, but it
 * happens once per Sample_Period, not within one: the re-init policy lives in
 * pm_sampler, where it is unit tested without hardware. */

#include "ina219_driver.h"

#include <Wire.h>

#include "pm_config.h"   /* I2C_CLOCK_HZ, I2C_TIMEOUT_US */

/* Return codes of Wire.endTransmission(). It returns twi_writeTo()'s result
 * unchanged, and their meanings are documented in the header comment of
 * twi_writeTo() in the core's libraries/Wire/src/utility/twi.c (AVR core 1.8.8). */
#define WIRE_END_TX_OK              0u   /* success */
#define WIRE_END_TX_TOO_LONG        1u   /* data too long for the 32-byte buffer */
#define WIRE_END_TX_ADDR_NACK       2u   /* address sent, NACK received */
#define WIRE_END_TX_DATA_NACK       3u   /* data sent, NACK received */
#define WIRE_END_TX_OTHER_ERROR     4u   /* lost arbitration, bus error, ... */
#define WIRE_END_TX_TIMEOUT         5u   /* a wait loop exceeded I2C_TIMEOUT_US */

#define INA219_REG_SIZE_BYTES       2u      /* every INA219 register is 16 bits */
#define BITS_PER_BYTE               8u
#define LOW_BYTE_MASK               0xFFu

/* Status mapping for endTransmission() (design table):
 *   0     -> PM_OK
 *   2, 3  -> PM_ERR_I2C_NACK: nobody answered the address, or the device
 *            refused a byte. The usual cause is a missing, unpowered, or
 *            mis-addressed sensor, so these share one code.
 *   5     -> PM_ERR_I2C_TIMEOUT: the bus stopped making progress (for example
 *            SDA or SCL held low) and the Wire timeout aborted the transaction.
 *   1, 4  -> PM_ERR_I2C_BUS: an oversized write (impossible here: at most
 *            3 bytes are queued) or a bus-level fault such as lost arbitration.
 * Any other value can't come from this core; it is treated as a bus error so
 * an unexpected code is never mistaken for success. */
static pm_status_t status_from_end_transmission(uint8_t result)
{
    switch (result) {
    case WIRE_END_TX_OK:
        return PM_OK;
    case WIRE_END_TX_ADDR_NACK:
    case WIRE_END_TX_DATA_NACK:
        return PM_ERR_I2C_NACK;
    case WIRE_END_TX_TIMEOUT:
        return PM_ERR_I2C_TIMEOUT;
    case WIRE_END_TX_TOO_LONG:
    case WIRE_END_TX_OTHER_ERROR:
    default:
        return PM_ERR_I2C_BUS;
    }
}

/* Byte order: the INA219 sends and receives every 16-bit register most
 * significant byte first. That is the chip's I2C protocol, fixed by the
 * datasheet. The Frame's little-endian order is our own wire format to the
 * host, fixed by docs/packet_format.md. The two never meet: this driver turns
 * the chip's bytes into a uint16_t value, and pm_frame later writes values
 * out byte by byte in its own order. Neither depends on the CPU's order. */

/* Why the timeout flag is cleared first: getWireTimeoutFlag() is sticky. It
 * stays set after a timeout until something clears it, so without clearing
 * it at the start of each transaction, a timeout in some earlier Sample would
 * make a later, unrelated short read look like a timeout. Clearing it first
 * means the flag describes only this transaction. */

/* Writes `value` to register `reg`: pointer byte, then MSB, then LSB, then STOP. */
static pm_status_t write_reg16(uint8_t reg, uint16_t value)
{
    Wire.clearWireTimeoutFlag();

    /* Wire.write() only queues bytes in the 32-byte TX buffer; it can fail
     * only when that buffer is full, which 3 bytes can't reach. Bus errors
     * are reported by endTransmission(), which sends them. */
    Wire.beginTransmission((uint8_t)INA219_I2C_ADDR);
    Wire.write(reg);
    Wire.write((uint8_t)(value >> BITS_PER_BYTE));   /* MSB first (INA219 order) */
    Wire.write((uint8_t)(value & LOW_BYTE_MASK));
    return status_from_end_transmission(Wire.endTransmission());
}

/* Reads register `reg` into *out: set the register pointer, then read 2 bytes.
 * *out is written only on PM_OK. */
static pm_status_t read_reg16(uint8_t reg, uint16_t *out)
{
    /* One clear covers both steps. If the pointer write times out,
     * endTransmission() returns 5 and this function returns before the read,
     * so the flag can only be set again by requestFrom() itself. */
    Wire.clearWireTimeoutFlag();

    /* STOP (endTransmission()) rather than a repeated start
     * (endTransmission(false)) after the pointer write. The INA219 keeps its
     * register pointer across a STOP, so both work, and a STOP makes each half
     * a complete, independent transaction that leaves the bus idle whatever
     * happens next. A repeated start only matters with several masters
     * competing for the bus, and this bus has one. It would also leave the bus
     * held between the two calls (Wire.cpp warns this can hang it) and take
     * twi.c's repeated-start path, which adds another timed wait loop. The
     * cost of the STOP is one extra STOP + START, about 20 us at 100 kHz. */
    Wire.beginTransmission((uint8_t)INA219_I2C_ADDR);
    Wire.write(reg);
    pm_status_t status = status_from_end_transmission(Wire.endTransmission());
    if (status != PM_OK) {
        return status;
    }

    /* Both arguments uint8_t so the call matches requestFrom(uint8_t, uint8_t)
     * exactly. Mixed types (an int address with a uint8_t count) match two
     * overloads equally well and avr-g++ warns that the call is ambiguous. */
    uint8_t received = Wire.requestFrom((uint8_t)INA219_I2C_ADDR, (uint8_t)INA219_REG_SIZE_BYTES);

    /* Status mapping for requestFrom(), which returns only a byte count:
     *   2 bytes             -> continue
     *   fewer, flag set     -> PM_ERR_I2C_TIMEOUT: a wait loop timed out
     *   fewer, flag clear   -> PM_ERR_I2C_SHORT_READ: the transfer ended early.
     *                          An address NACK on the read also lands here,
     *                          because twi_readFrom() reports it as 0 bytes. */
    if (received != INA219_REG_SIZE_BYTES) {
        return Wire.getWireTimeoutFlag() ? PM_ERR_I2C_TIMEOUT : PM_ERR_I2C_SHORT_READ;
    }

    /* Wire.read() returns int (-1 when empty); both bytes are known to be
     * there, so each value is 0..255 and the uint8_t cast is exact. */
    uint8_t msb = (uint8_t)Wire.read();   /* MSB first (INA219 order) */
    uint8_t lsb = (uint8_t)Wire.read();

    /* Why cast msb to uint16_t before shifting: a uint8_t operand is promoted
     * to int, which is 16-bit signed on the AVR, and 255 << 8 = 65,280 doesn't
     * fit in it, which is undefined behavior in C++11. On the AVR uint16_t is
     * unsigned int, which isn't promoted, so the shift is done unsigned. (On a
     * 32-bit host uint16_t is promoted to a 32-bit int, where 65,280 fits.)
     * The outer cast brings the result back to uint16_t on every target. */
    *out = (uint16_t)(((uint16_t)msb << BITS_PER_BYTE) | (uint16_t)lsb);
    return PM_OK;
}

pm_status_t ina219_bus_begin(void)
{
    Wire.begin();
    Wire.setClock(I2C_CLOCK_HZ);

    /* Timeouts are off by default in this core, so a stuck bus would hang
     * loop() forever. I2C_TIMEOUT_US (5 ms) aborts a stalled transaction well
     * inside the 20 ms period at 50 Hz (Req 2.1; checked in pm_config.h).
     *
     * The timeout is per wait loop, not per transaction: in twi.c each
     * busy-wait loop restarts its own timer. A wedged transaction is aborted
     * about 5 ms after progress stops, but a pathological one that stalls
     * just under the limit in one loop and then times out in the next can
     * take about 10 ms before it aborts. That is still below 20 ms.
     *
     * reset_with_timeout = true: after a timeout, twi.c disables and
     * re-initializes the TWI hardware (keeping the clock setting). Without
     * the reset, the TWI state machine stays stuck in the aborted transfer,
     * so every later transaction would time out waiting for it to become
     * ready, even after the bus fault is gone, and re-init could never
     * succeed. With it, the next period's transaction starts clean. */
    Wire.setWireTimeout(I2C_TIMEOUT_US, true);

    /* None of these calls can fail (they only set registers), but every
     * hardware function returns a status, the same rule as uart_init(). */
    return PM_OK;
}

/* Writes CONFIG and CAL, then reads both back. Returns at the first failure.
 *
 * Why the read-back: a write that is ACKed is not proof that the settings
 * stuck. A glitch during the write, or a part that ACKs but didn't latch the
 * value, would leave the chip running on defaults (CAL = 0 after a reset, so
 * Current reads 0 A) with no I2C error at all. Reading both registers back
 * turns that silent failure into PM_ERR_SENSOR_VERIFY, so the Sample is
 * flagged SENSOR_ERROR and pm_sampler retries next period. */
pm_status_t ina219_configure(void)
{
    const uint16_t config_value = (uint16_t)INA219_CONFIG_VALUE;   /* 0x119F */

    /* Exact conversion: the fourth #error check in pm_scaling.h requires
     * INA219_CAL_VALUE to be even and between 2 and 0xFFFE, so it fits
     * uint16_t unchanged. */
    const uint16_t cal_value = (uint16_t)INA219_CAL_VALUE;          /* 4096 */

    pm_status_t status = write_reg16((uint8_t)INA219_REG_CONFIG, config_value);
    if (status != PM_OK) {
        return status;
    }
    status = write_reg16((uint8_t)INA219_REG_CALIBRATION, cal_value);
    if (status != PM_OK) {
        return status;
    }

    uint16_t config_readback = 0u;
    status = read_reg16((uint8_t)INA219_REG_CONFIG, &config_readback);
    if (status != PM_OK) {
        return status;
    }
    if ((uint16_t)config_readback != (uint16_t)config_value) {
        return PM_ERR_SENSOR_VERIFY;
    }

    /* The datasheet makes calibration bit 0 read-only zero: the chip stores
     * an odd value as value - 1. This comparison can only match because CAL
     * is even; an odd CAL would fail verification on every attempt, and the
     * sensor would never leave SENSOR_ERROR. The same pm_scaling.h check
     * rules that out at build time. */
    uint16_t cal_readback = 0u;
    status = read_reg16((uint8_t)INA219_REG_CALIBRATION, &cal_readback);
    if (status != PM_OK) {
        return status;
    }
    if ((uint16_t)cal_readback != (uint16_t)cal_value) {
        return PM_ERR_SENSOR_VERIFY;
    }
    return PM_OK;
}

/* Reads the bus voltage register, then the current register (Req 1.3).
 * Returns at the first failure. Both registers are read into locals and *out
 * is written only when both reads succeed, the same contract as read_reg16,
 * so a caller can never see a half-updated reading with a bus value from
 * this Sample and a current value from an earlier one. */
pm_status_t ina219_read(pm_raw_reading_t *out)
{
    uint16_t bus_reg = 0u;
    pm_status_t status = read_reg16((uint8_t)INA219_REG_BUS_VOLTAGE, &bus_reg);
    if (status != PM_OK) {
        return status;
    }

    uint16_t current_reg = 0u;
    status = read_reg16((uint8_t)INA219_REG_CURRENT, &current_reg);
    if (status != PM_OK) {
        return status;
    }

    out->bus_reg = bus_reg;
    out->current_reg = current_reg;
    return PM_OK;
}
