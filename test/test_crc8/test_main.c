/* Unity tests for the CRC_Module (Req 7.3, 7.8, 14.2, 15.3). */
#include <unity.h>
#include "pm_crc8.h"

/* Standard check value for CRC-8/SMBUS from the reveng CRC catalogue: the CRC
 * of the ASCII string "123456789". Matching it shows the polynomial, initial
 * value, reflection, and XOR-out are all as the Packet_Spec names them. */
#define CRC8_SMBUS_CHECK_VALUE  0xF4u

/* GV1's crc8 byte (offset 24) from docs/packet_format.md. */
#define GV1_CRC8                0x77u

/* The CRC covers Frame bytes 2..23: length byte through energy_uwh. */
#define CRC_START_OFFSET        2u
#define CRC_COVERED_LEN         22u

/* GV1 Frame bytes, copied from docs/packet_format.md. */
static const uint8_t GV1_FRAME[] = {
    0xAA, 0x55, 0x19, 0x34, 0x12, 0x15, 0xCD, 0x5B, 0x07, 0x00, 0x88, 0x13, 0x6C,
    0xE2, 0x01, 0x00, 0x6A, 0x02, 0x00, 0x00, 0xB1, 0xCB, 0x74, 0x00, 0x77
};

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

static void test_check_string_gives_catalogue_check_value(void)
{
    /* sizeof - 1 drops the string's terminating '\0', which is not data. */
    static const char check_string[] = "123456789";
    TEST_ASSERT_EQUAL_HEX8(CRC8_SMBUS_CHECK_VALUE,
                           pm_crc8((const uint8_t *)check_string, sizeof check_string - 1));
}

static void test_empty_input_gives_init_value(void)
{
    /* NULL with length 0 must be safe: no byte is read. */
    TEST_ASSERT_EQUAL_HEX8(PM_CRC8_INIT, pm_crc8(NULL, 0));
}

static void test_gv1_covered_bytes_give_gv1_crc(void)
{
    TEST_ASSERT_EQUAL_HEX8(GV1_CRC8,
                           pm_crc8(&GV1_FRAME[CRC_START_OFFSET], CRC_COVERED_LEN));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_check_string_gives_catalogue_check_value);
    RUN_TEST(test_empty_input_gives_init_value);
    RUN_TEST(test_gv1_covered_bytes_give_gv1_crc);
    return UNITY_END();
}
