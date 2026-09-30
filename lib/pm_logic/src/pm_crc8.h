#ifndef PM_CRC8_H
#define PM_CRC8_H

#include <stddef.h>
#include <stdint.h>

/* Same reason as pm_status.h: main.cpp (C++) calls this C function. */
#ifdef __cplusplus
extern "C" {
#endif

/* CRC-8/SMBUS, as named in docs/packet_format.md.
 * Generator polynomial x^8 + x^2 + x + 1. Its bits are 1 0000 0111 (9 bits),
 * but the x^8 bit is always 1 and is always cancelled by the XOR in each
 * division step, so it is never stored: only the low 8 bits (0x07) are kept. */
#define PM_CRC8_POLY  0x07u
#define PM_CRC8_INIT  0x00u

/* Returns the CRC-8/SMBUS of `len` bytes starting at `data`.
 * `data` may be NULL when `len` is 0; the result is then PM_CRC8_INIT. */
uint8_t pm_crc8(const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif

#endif /* PM_CRC8_H */
