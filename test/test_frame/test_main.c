/* Unity tests for the Frame_Encoder (Req 7.1-7.4, 7.7, 14.4, 15.3). */
#include <string.h>
#include <unity.h>
#include "pm_frame.h"

/* Filler for "was this byte written?" checks. Not 0xAA or 0x00, so a stray
 * sync0 or zero write would show up; 0xEE appears in none of the vectors. */
#define SENTINEL_BYTE          0xEEu
#define SHORT_BUFFER_LEN_BYTES (PM_FRAME_LEN_BYTES - 1)
#define LONG_BUFFER_LEN_BYTES  (PM_FRAME_LEN_BYTES + 1)

/* Golden_Vector Frame bytes, copied from docs/packet_format.md. Keep this exact
 * declaration form: the drift test (host/tests/test_golden_vectors_sync.py)
 * parses it and fails if these bytes differ from the doc or the Python fixture. */
static const uint8_t GV1_BYTES[PM_FRAME_LEN_BYTES] = {
    0xAA, 0x55, 0x19, 0x34, 0x12, 0x15, 0xCD, 0x5B, 0x07, 0x00, 0x88, 0x13, 0x6C,
    0xE2, 0x01, 0x00, 0x6A, 0x02, 0x00, 0x00, 0xB1, 0xCB, 0x74, 0x00, 0x77
};
static const uint8_t GV2_BYTES[PM_FRAME_LEN_BYTES] = {
    0xAA, 0x55, 0x19, 0xFF, 0xFF, 0x9C, 0xFF, 0xFF, 0xFF, 0x06, 0x74, 0x0E, 0xF8,
    0x0D, 0xF0, 0xFF, 0xE5, 0xF0, 0xFF, 0xFF, 0x01, 0x00, 0x00, 0x80, 0x6B
};
static const uint8_t GV3_BYTES[PM_FRAME_LEN_BYTES] = {
    0xAA, 0x55, 0x19, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x9E
};

/* Golden_Vector field values from the table in docs/packet_format.md.
 * INT32_C/UINT32_C give each literal a type at least 32 bits wide on both
 * targets (int is only 16 bits on the AVR). Negative values are written as
 * -INT32_C(x) because the macro's argument must be an unsuffixed constant,
 * and in C "-x" is the operator minus applied to x, not a constant. */
static const pm_sample_t GV1_SAMPLE = {
    .seq            = UINT16_C(0x1234),
    .timestamp_ms   = UINT32_C(123456789),
    .status_flags   = 0x00u,
    .bus_voltage_mv = UINT16_C(5000),
    .current_ua     = INT32_C(123500),
    .power_mw       = INT32_C(618),
    .energy_uwh     = INT32_C(7654321),
};
static const pm_sample_t GV2_SAMPLE = {
    .seq            = UINT16_C(65535),
    .timestamp_ms   = UINT32_C(4294967196),
    .status_flags   = PM_FLAG_OVERRANGE | PM_FLAG_ENERGY_SATURATED,
    .bus_voltage_mv = UINT16_C(3700),
    .current_ua     = -INT32_C(1045000),
    .power_mw       = -INT32_C(3867),
    .energy_uwh     = -INT32_C(2147483647),
};
static const pm_sample_t GV3_SAMPLE = {
    .seq            = UINT16_C(0),
    .timestamp_ms   = UINT32_C(100),
    .status_flags   = PM_FLAG_SENSOR_ERROR,
    .bus_voltage_mv = UINT16_C(0),
    .current_ua     = INT32_C(0),
    .power_mw       = INT32_C(0),
    .energy_uwh     = INT32_C(0),
};

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

/* Encodes into a sentinel-filled buffer of exactly one Frame and compares. */
static void assert_encodes_to(const pm_sample_t *sample, const uint8_t *expected)
{
    uint8_t out[PM_FRAME_LEN_BYTES];
    memset(out, SENTINEL_BYTE, sizeof out);
    TEST_ASSERT_EQUAL(PM_OK, pm_frame_encode(sample, out, sizeof out));
    TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, PM_FRAME_LEN_BYTES);
}

static void test_gv1_encodes_byte_for_byte(void)
{
    assert_encodes_to(&GV1_SAMPLE, GV1_BYTES);
}

static void test_gv2_encodes_byte_for_byte(void)
{
    assert_encodes_to(&GV2_SAMPLE, GV2_BYTES);
}

static void test_gv3_encodes_byte_for_byte(void)
{
    assert_encodes_to(&GV3_SAMPLE, GV3_BYTES);
}

static void test_short_buffer_is_rejected_and_untouched(void)
{
    uint8_t out[SHORT_BUFFER_LEN_BYTES];
    memset(out, SENTINEL_BYTE, sizeof out);
    TEST_ASSERT_EQUAL(PM_ERR_BUFFER_TOO_SMALL, pm_frame_encode(&GV1_SAMPLE, out, sizeof out));
    TEST_ASSERT_EACH_EQUAL_HEX8(SENTINEL_BYTE, out, sizeof out);
}

static void test_long_buffer_gets_exactly_one_frame(void)
{
    uint8_t out[LONG_BUFFER_LEN_BYTES];
    memset(out, SENTINEL_BYTE, sizeof out);
    TEST_ASSERT_EQUAL(PM_OK, pm_frame_encode(&GV1_SAMPLE, out, sizeof out));
    TEST_ASSERT_EQUAL_HEX8_ARRAY(GV1_BYTES, out, PM_FRAME_LEN_BYTES);
    TEST_ASSERT_EQUAL_HEX8(SENTINEL_BYTE, out[PM_FRAME_LEN_BYTES]);
}

static void test_next_seq_increments(void)
{
    TEST_ASSERT_EQUAL_UINT16(1u, pm_frame_next_seq(0u));
}

static void test_next_seq_wraps_at_max(void)
{
    TEST_ASSERT_EQUAL_UINT16(0u, pm_frame_next_seq(UINT16_MAX));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_gv1_encodes_byte_for_byte);
    RUN_TEST(test_gv2_encodes_byte_for_byte);
    RUN_TEST(test_gv3_encodes_byte_for_byte);
    RUN_TEST(test_short_buffer_is_rejected_and_untouched);
    RUN_TEST(test_long_buffer_gets_exactly_one_frame);
    RUN_TEST(test_next_seq_increments);
    RUN_TEST(test_next_seq_wraps_at_max);
    return UNITY_END();
}
