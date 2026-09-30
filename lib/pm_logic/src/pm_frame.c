/* Frame_Encoder.
 *
 * Little-endian: a multi-byte integer is sent least significant byte first,
 * so 5000 = 0x1388 goes on the wire as 88 13.
 *
 * Every field is written with explicit shifts instead of memcpy'ing the
 * struct. The compiler chooses struct padding (and the CPU chooses byte
 * order): pm_sample_t is 21 bytes on the AVR (no padding) but 24 on the Mac,
 * where 32-bit fields are aligned to 4 bytes. With shifts, this file is the
 * definition of the format and gives the same bytes on any machine. */

#include "pm_frame.h"

#include "pm_crc8.h"

/* Field offsets are private: only this file builds Frames. The host parser
 * is Python and has its own copy of the layout, checked by the Golden_Vectors.
 * The CRC offsets are public in pm_frame.h because the design names them there. */
#define PM_FRAME_SYNC0_OFFSET        0u
#define PM_FRAME_SYNC1_OFFSET        1u
#define PM_FRAME_LENGTH_OFFSET       2u
#define PM_FRAME_SEQ_OFFSET          3u
#define PM_FRAME_TIMESTAMP_OFFSET    5u
#define PM_FRAME_FLAGS_OFFSET        9u
#define PM_FRAME_BUS_VOLTAGE_OFFSET  10u
#define PM_FRAME_CURRENT_OFFSET      12u
#define PM_FRAME_POWER_OFFSET        16u
#define PM_FRAME_ENERGY_OFFSET       20u

#define PM_FRAME_U16_LEN_BYTES       2u
#define PM_FRAME_U32_LEN_BYTES       4u
#define PM_FRAME_BYTE_MASK           0xFFu
#define PM_FRAME_BITS_PER_BYTE       8u

/* Catches an edit to one offset that isn't matched in the others: the last
 * field must end exactly where the CRC starts, and the CRC is the last byte.
 * This is one of the `#if` uses that needs PM_FRAME_LEN_BYTES to be a plain integer. */
#if (PM_FRAME_ENERGY_OFFSET + PM_FRAME_U32_LEN_BYTES != PM_FRAME_CRC_OFFSET) || \
    (PM_FRAME_CRC_OFFSET != PM_FRAME_LEN_BYTES - 1)
#error "pm_frame.c: field offsets do not match PM_FRAME_CRC_OFFSET / PM_FRAME_LEN_BYTES"
#endif

/* Stores `value` at dst[0..1], least significant byte first. */
static void put_u16_le(uint8_t *dst, uint16_t value)
{
    for (size_t i = 0; i < PM_FRAME_U16_LEN_BYTES; i++) {
        dst[i] = (uint8_t)(value & PM_FRAME_BYTE_MASK);
        value = (uint16_t)(value >> PM_FRAME_BITS_PER_BYTE);
    }
}

/* Stores `value` at dst[0..3], least significant byte first. */
static void put_u32_le(uint8_t *dst, uint32_t value)
{
    for (size_t i = 0; i < PM_FRAME_U32_LEN_BYTES; i++) {
        dst[i] = (uint8_t)(value & PM_FRAME_BYTE_MASK);
        value >>= PM_FRAME_BITS_PER_BYTE;
    }
}

pm_status_t pm_frame_encode(const pm_sample_t *sample, uint8_t *out, size_t out_size)
{
    /* Checked before any write, so a too-small buffer is left untouched. */
    if (out_size < PM_FRAME_LEN_BYTES) {
        return PM_ERR_BUFFER_TOO_SMALL;
    }

    out[PM_FRAME_SYNC0_OFFSET]  = (uint8_t)PM_FRAME_SYNC0;
    out[PM_FRAME_SYNC1_OFFSET]  = (uint8_t)PM_FRAME_SYNC1;
    out[PM_FRAME_LENGTH_OFFSET] = (uint8_t)PM_FRAME_LEN_BYTES;
    put_u16_le(&out[PM_FRAME_SEQ_OFFSET], sample->seq);
    put_u32_le(&out[PM_FRAME_TIMESTAMP_OFFSET], sample->timestamp_ms);
    out[PM_FRAME_FLAGS_OFFSET] = sample->status_flags;
    put_u16_le(&out[PM_FRAME_BUS_VOLTAGE_OFFSET], sample->bus_voltage_mv);

    /* C defines signed -> unsigned conversion as "add or subtract 2^32 until
     * it fits" (modulo 2^32), so -100 becomes 2^32 - 100 = 0xFFFFFF9C: exactly
     * the two's-complement bit pattern, on any compiler. Shifting the signed
     * value directly would not be safe: right-shifting a negative int is
     * implementation-defined. */
    put_u32_le(&out[PM_FRAME_CURRENT_OFFSET], (uint32_t)sample->current_ua);
    put_u32_le(&out[PM_FRAME_POWER_OFFSET],   (uint32_t)sample->power_mw);
    put_u32_le(&out[PM_FRAME_ENERGY_OFFSET],  (uint32_t)sample->energy_uwh);

    out[PM_FRAME_CRC_OFFSET] = pm_crc8(&out[PM_FRAME_CRC_START_OFFSET],
                                       PM_FRAME_CRC_OFFSET - PM_FRAME_CRC_START_OFFSET);
    return PM_OK;
}

uint16_t pm_frame_next_seq(uint16_t seq)
{
    /* Arithmetic never happens in uint16_t. On the Mac (32-bit int), `seq` is
     * promoted to int and the 1u makes the sum unsigned int, so 65535 + 1u is
     * 65536. On the AVR (16-bit int), uint16_t promotes straight to unsigned
     * int and the sum already wraps to 0. The cast keeps the low 16 bits, so
     * the result is 65535 -> 0 on both targets. */
    return (uint16_t)(seq + 1u);
}
