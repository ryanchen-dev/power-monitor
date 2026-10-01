/* Unity tests for the Power_Calculator (Req 4.1-4.3, 15.3).
 *
 * Every expected value is a literal worked out by hand (arithmetic in the
 * comment), not recomputed with the implementation's formula. mV x uA = nW,
 * and 1 mW = 1,000,000 nW. */
#include <stdint.h>
#include <unity.h>
#include "pm_power.h"

#define ZERO_MV                    UINT16_C(0)
#define ZERO_UA                    INT32_C(0)
#define ZERO_MW                    INT32_C(0)

/* Rated_Range corner: 6000 mV x 1,000,000 uA = 6,000,000,000 nW = 6000 mW. */
#define RATED_MAX_MV               UINT16_C(6000)
#define RATED_MAX_UA               INT32_C(1000000)
#define RATED_CORNER_MW            INT32_C(6000)

/* GV1: 5000 x 123,500 = 617,500,000 nW = 617.5 mW -> 618 (half rounds up). */
#define GV1_MV                     UINT16_C(5000)
#define GV1_UA                     INT32_C(123500)
#define GV1_MW                     INT32_C(618)

/* GV2: 3700 x -1,045,000 = -3,866,500,000 nW = -3866.5 mW -> -3867 (away from zero). */
#define GV2_MV                     UINT16_C(3700)
#define GV2_UA                     (-INT32_C(1045000))
#define GV2_MW                     (-INT32_C(3867))

/* Half-way and neighbouring points, with V = 1 mV so the current in uA is
 * the power in nW. */
#define ONE_MV                     UINT16_C(1)
#define HALF_MW_UA                 INT32_C(500000)    /* 0.5 mW     -> 1  */
#define HALF_MW_MINUS_ONE_UA       INT32_C(499999)    /* 0.499999   -> 0  */
#define HALF_MW_PLUS_ONE_UA        INT32_C(500001)    /* 0.500001   -> 1  */
#define TWO_AND_HALF_MW_UA         INT32_C(2500000)   /* 2.5 mW     -> 3  */
#define ONE_MW                     INT32_C(1)
#define THREE_MW                   INT32_C(3)

/* 4 mV x 374,900 uA = 1,499,600 nW = 1.4996 mW -> 1 mW.
 * Two-step rounding would give 2: 1,499.6 uW -> 1,500 uW -> 1.5 mW -> 2 mW. */
#define TWO_STEP_MV                UINT16_C(4)
#define TWO_STEP_UA                INT32_C(374900)

/* Extremes of the input types, run under the signed-overflow trap.
 * 65,535 x 2,147,483,647 = (2^16 - 1)(2^31 - 1) = 2^47 - 2^31 - 2^16 + 1
 *   = 140,737,488,355,328 - 2,147,483,648 - 65,536 + 1
 *   = 140,735,340,806,145 nW = 140,735,340.806145 mW -> 140,735,341.
 * 65,535 x -2,147,483,648 = -(2^16 - 1) x 2^31 = -(2^47 - 2^31)
 *   = -(140,737,488,355,328 - 2,147,483,648)
 *   = -140,735,340,871,680 nW = -140,735,340.87168 mW -> -140,735,341. */
#define MAX_MV                     UINT16_C(65535)
#define EXTREME_POSITIVE_MW        INT32_C(140735341)
#define EXTREME_NEGATIVE_MW        (-INT32_C(140735341))

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

static void test_zero_power(void)
{
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, pm_power_mw(ZERO_MV, ZERO_UA));
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, pm_power_mw(ZERO_MV, RATED_MAX_UA));
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, pm_power_mw(RATED_MAX_MV, ZERO_UA));
}

static void test_rated_range_corners(void)
{
    TEST_ASSERT_EQUAL_INT32(RATED_CORNER_MW, pm_power_mw(RATED_MAX_MV, RATED_MAX_UA));
    TEST_ASSERT_EQUAL_INT32(-RATED_CORNER_MW, pm_power_mw(RATED_MAX_MV, -RATED_MAX_UA));
}

static void test_gv1_rounds_half_up(void)
{
    TEST_ASSERT_EQUAL_INT32(GV1_MW, pm_power_mw(GV1_MV, GV1_UA));
}

static void test_gv2_rounds_half_away_from_zero(void)
{
    TEST_ASSERT_EQUAL_INT32(GV2_MW, pm_power_mw(GV2_MV, GV2_UA));
}

/* 2.5 -> 3 also rules out round-half-to-even, which would give 2. */
static void test_half_way_rounds_away_from_zero_both_signs(void)
{
    TEST_ASSERT_EQUAL_INT32(ONE_MW, pm_power_mw(ONE_MV, HALF_MW_UA));
    TEST_ASSERT_EQUAL_INT32(-ONE_MW, pm_power_mw(ONE_MV, -HALF_MW_UA));
    TEST_ASSERT_EQUAL_INT32(THREE_MW, pm_power_mw(ONE_MV, TWO_AND_HALF_MW_UA));
    TEST_ASSERT_EQUAL_INT32(-THREE_MW, pm_power_mw(ONE_MV, -TWO_AND_HALF_MW_UA));
}

static void test_just_below_and_above_half_way(void)
{
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, pm_power_mw(ONE_MV, HALF_MW_MINUS_ONE_UA));
    TEST_ASSERT_EQUAL_INT32(ONE_MW, pm_power_mw(ONE_MV, HALF_MW_PLUS_ONE_UA));
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, pm_power_mw(ONE_MV, -HALF_MW_MINUS_ONE_UA));
    TEST_ASSERT_EQUAL_INT32(-ONE_MW, pm_power_mw(ONE_MV, -HALF_MW_PLUS_ONE_UA));
}

static void test_single_rounding_not_two_step(void)
{
    TEST_ASSERT_EQUAL_INT32(ONE_MW, pm_power_mw(TWO_STEP_MV, TWO_STEP_UA));
}

/* The native build traps on signed overflow, so returning at all shows no
 * intermediate overflowed; the literals show the result is exact. */
static void test_extremes_do_not_overflow(void)
{
    TEST_ASSERT_EQUAL_INT32(EXTREME_POSITIVE_MW, pm_power_mw(MAX_MV, INT32_MAX));
    TEST_ASSERT_EQUAL_INT32(EXTREME_NEGATIVE_MW, pm_power_mw(MAX_MV, INT32_MIN));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_zero_power);
    RUN_TEST(test_rated_range_corners);
    RUN_TEST(test_gv1_rounds_half_up);
    RUN_TEST(test_gv2_rounds_half_away_from_zero);
    RUN_TEST(test_half_way_rounds_away_from_zero_both_signs);
    RUN_TEST(test_just_below_and_above_half_way);
    RUN_TEST(test_single_rounding_not_two_step);
    RUN_TEST(test_extremes_do_not_overflow);
    return UNITY_END();
}
