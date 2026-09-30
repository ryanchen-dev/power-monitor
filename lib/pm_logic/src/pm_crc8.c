/* CRC_Module.
 *
 * A CRC treats the message as one long binary polynomial and divides it by a
 * fixed generator polynomial, using XOR instead of subtraction (arithmetic
 * with no carries). The 8-bit remainder is the checksum. The receiver
 * recomputes it; any difference means the bytes changed in transit.
 *
 * Bitwise, not table-driven: a 256-byte lookup table would be faster, but on
 * the AVR a `const` array is copied into SRAM at startup (unless it is marked
 * PROGMEM, which is AVR-only code). That is 256 of the Uno's 1024 bytes of
 * RAM, 25% of the budget. The bitwise loop costs about 0.1 ms per Frame. */

#include "pm_crc8.h"

/* Private to this file: the rest of the firmware has no use for them. */
#define PM_CRC8_TOP_BIT_MASK   0x80u /* bit 7, the x^7 coefficient of the remainder */
#define PM_CRC8_BITS_PER_BYTE  8u

uint8_t pm_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = PM_CRC8_INIT;

    /* When len is 0 the loop body never runs, so a NULL `data` is never read. */
    for (size_t i = 0; i < len; i++) {
        /* Bring the next 8 message bits into the running remainder. */
        crc ^= data[i];
        for (uint8_t bit = 0; bit < PM_CRC8_BITS_PER_BYTE; bit++) {
            /* One long-division step. If the top bit is 1, shifting it out
             * makes an x^8 term, so the (implicit x^8) polynomial "goes into"
             * the remainder once: subtract it, which in XOR arithmetic is XOR.
             * The casts matter because `crc << 1` is computed as an `int`
             * (integer promotion), so bit 8 survives; casting to uint8_t
             * discards it, which is exactly the cancelled x^8 term. */
            if (crc & PM_CRC8_TOP_BIT_MASK) {
                crc = (uint8_t)((crc << 1) ^ PM_CRC8_POLY);
            } else {
                crc = (uint8_t)(crc << 1);
            }
        }
    }
    return crc;
}
