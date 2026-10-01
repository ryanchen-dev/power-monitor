/* Unity tests for the Scaling_Module (Req 1.2, 2.6, 3.1, 3.2, 3.5, 15.3, 16.5). */
#include <stdint.h>
#include <unity.h>
#include "pm_scaling.h"

/* Expected derived constants, written as literals on purpose: comparing a
 * macro against itself would prove nothing. */
#define EXPECTED_CURRENT_LSB_UA     100L
#define EXPECTED_CAL_VALUE          4096L

/* Bus voltage register inputs. The reading sits in bits 15..3. */
#define BUS_REG_ZERO                UINT16_C(0x0000)
#define BUS_REG_5000_MV             UINT16_C(0x2710)  /* 1250 counts << 3; 1250 * 4 mV = 5000 mV */
#define BUS_REG_FULL_SCALE          UINT16_C(0xFFF8)  /* all 13 reading bits set, status bits clear */
#define BUS_REG_CNVR_MASK           UINT16_C(0x0002)  /* conversion ready (bit 1); not used by pm_scaling */
#define BUS_FULL_SCALE_MV           UINT16_C(32764)   /* 8191 counts * 4 mV */
#define BUS_5000_MV                 UINT16_C(5000)

/* Current register inputs and expected outputs (100 uA per count). */
#define CURRENT_REG_ZERO            UINT16_C(0x0000)
#define CURRENT_REG_MAX_POSITIVE    UINT16_C(0x7FFF)  /* +32767 counts */
#define CURRENT_REG_MOST_NEGATIVE   UINT16_C(0x8000)  /* -32768 counts */
#define CURRENT_REG_MINUS_ONE       UINT16_C(0xFFFF)  /* -1 count */
#define CURRENT_MAX_POSITIVE_UA     INT32_C(3276700)
#define CURRENT_MOST_NEGATIVE_UA    (-INT32_C(3276800))
#define CURRENT_MINUS_ONE_LSB_UA    (-INT32_C(100))

/* Rated_Range boundaries and the first hardware step past each one
 * (bus voltage moves in 4 mV steps, current in 100 uA steps). */
#define BUS_AT_LIMIT_MV             UINT16_C(6000)
#define BUS_ONE_STEP_OVER_MV        UINT16_C(6004)
#define CURRENT_AT_LIMIT_UA         INT32_C(1000000)
#define CURRENT_ONE_STEP_OVER_UA    INT32_C(1000100)
#define ZERO_MV                     UINT16_C(0)
#define ZERO_UA                     INT32_C(0)

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

static void test_current_lsb_is_100_ua(void)
{
    TEST_ASSERT_EQUAL_INT32(EXPECTED_CURRENT_LSB_UA, CURRENT_LSB_UA);
}

static void test_cal_value_is_4096(void)
{
    TEST_ASSERT_EQUAL_INT32(EXPECTED_CAL_VALUE, INA219_CAL_VALUE);
}

static void test_bus_zero_register_is_zero_mv(void)
{
    TEST_ASSERT_EQUAL_UINT16(ZERO_MV, pm_scale_bus_voltage_mv(BUS_REG_ZERO));
}

static void test_bus_0x2710_is_5000_mv(void)
{
    TEST_ASSERT_EQUAL_UINT16(BUS_5000_MV, pm_scale_bus_voltage_mv(BUS_REG_5000_MV));
}

static void test_bus_full_scale_is_32764_mv(void)
{
    TEST_ASSERT_EQUAL_UINT16(BUS_FULL_SCALE_MV, pm_scale_bus_voltage_mv(BUS_REG_FULL_SCALE));
}

static void test_bus_status_bits_do_not_change_voltage(void)
{
    TEST_ASSERT_EQUAL_UINT16(BUS_5000_MV,
                             pm_scale_bus_voltage_mv(BUS_REG_5000_MV | BUS_REG_CNVR_MASK));
    TEST_ASSERT_EQUAL_UINT16(BUS_5000_MV,
                             pm_scale_bus_voltage_mv(BUS_REG_5000_MV | BUS_REG_OVF_MASK));
    /* Both set at once: the normal state after a conversion that overflowed. */
    TEST_ASSERT_EQUAL_UINT16(BUS_5000_MV,
                             pm_scale_bus_voltage_mv(BUS_REG_5000_MV | BUS_REG_CNVR_MASK |
                                                     BUS_REG_OVF_MASK));
}

static void test_bus_ovf_bit_is_detected(void)
{
    TEST_ASSERT_FALSE(pm_bus_math_overflow(BUS_REG_5000_MV));
    TEST_ASSERT_TRUE(pm_bus_math_overflow(BUS_REG_5000_MV | BUS_REG_OVF_MASK));
    /* CNVR alone must not look like an overflow. */
    TEST_ASSERT_FALSE(pm_bus_math_overflow(BUS_REG_5000_MV | BUS_REG_CNVR_MASK));
    TEST_ASSERT_TRUE(pm_bus_math_overflow(BUS_REG_5000_MV | BUS_REG_CNVR_MASK | BUS_REG_OVF_MASK));
}

static void test_current_zero_register_is_zero_ua(void)
{
    TEST_ASSERT_EQUAL_INT32(ZERO_UA, pm_scale_current_ua(CURRENT_REG_ZERO));
}

static void test_current_max_positive_register(void)
{
    TEST_ASSERT_EQUAL_INT32(CURRENT_MAX_POSITIVE_UA, pm_scale_current_ua(CURRENT_REG_MAX_POSITIVE));
}

static void test_current_most_negative_register(void)
{
    TEST_ASSERT_EQUAL_INT32(CURRENT_MOST_NEGATIVE_UA, pm_scale_current_ua(CURRENT_REG_MOST_NEGATIVE));
}

static void test_current_minus_one_count(void)
{
    TEST_ASSERT_EQUAL_INT32(CURRENT_MINUS_ONE_LSB_UA, pm_scale_current_ua(CURRENT_REG_MINUS_ONE));
}

static void test_overrange_bus_voltage_boundary(void)
{
    TEST_ASSERT_FALSE(pm_is_overrange(BUS_AT_LIMIT_MV, ZERO_UA));
    TEST_ASSERT_TRUE(pm_is_overrange(BUS_ONE_STEP_OVER_MV, ZERO_UA));
}

static void test_overrange_current_boundaries(void)
{
    TEST_ASSERT_FALSE(pm_is_overrange(ZERO_MV, CURRENT_AT_LIMIT_UA));
    TEST_ASSERT_FALSE(pm_is_overrange(ZERO_MV, -CURRENT_AT_LIMIT_UA));
    TEST_ASSERT_TRUE(pm_is_overrange(ZERO_MV, CURRENT_ONE_STEP_OVER_UA));
    TEST_ASSERT_TRUE(pm_is_overrange(ZERO_MV, -CURRENT_ONE_STEP_OVER_UA));
}

/* The native build traps on signed overflow, so reaching the assertions at
 * all shows pm_is_overrange never computes |INT32_MIN|. */
static void test_overrange_int32_extremes_do_not_overflow(void)
{
    TEST_ASSERT_TRUE(pm_is_overrange(ZERO_MV, INT32_MIN));
    TEST_ASSERT_TRUE(pm_is_overrange(ZERO_MV, INT32_MAX));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_current_lsb_is_100_ua);
    RUN_TEST(test_cal_value_is_4096);
    RUN_TEST(test_bus_zero_register_is_zero_mv);
    RUN_TEST(test_bus_0x2710_is_5000_mv);
    RUN_TEST(test_bus_full_scale_is_32764_mv);
    RUN_TEST(test_bus_status_bits_do_not_change_voltage);
    RUN_TEST(test_bus_ovf_bit_is_detected);
    RUN_TEST(test_current_zero_register_is_zero_ua);
    RUN_TEST(test_current_max_positive_register);
    RUN_TEST(test_current_most_negative_register);
    RUN_TEST(test_current_minus_one_count);
    RUN_TEST(test_overrange_bus_voltage_boundary);
    RUN_TEST(test_overrange_current_boundaries);
    RUN_TEST(test_overrange_int32_extremes_do_not_overflow);
    return UNITY_END();
}
