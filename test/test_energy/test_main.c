/* Unity tests for the Energy_Integrator (Req 5.1-5.4, 5.6, 5.7, 15.3, 15.4).
 *
 * Expected values are hand-computed literals (arithmetic in the comments).
 * Energy unit: mV x uA x ms = pJ, and 1 uWh = 3,600,000,000 pJ.
 * Tests read acc_pj directly when a value is below 1 uWh or must be exact. */
#include <stdbool.h>
#include <stdint.h>
#include <unity.h>
#include "pm_energy.h"

#define ZERO_PJ                    INT64_C(0)
#define ZERO_UWH                   INT32_C(0)
#define ZERO_MV                    UINT16_C(0)
#define ZERO_UA                    INT32_C(0)

/* 3600 mV x 1000 uA = 3,600,000 nW = 3.6 mW. Over 1000 ms that is
 * 3,600,000 x 1000 = 3,600,000,000 pJ = exactly 1 uWh. */
#define TEST_MV                    UINT16_C(3600)
#define TEST_UA                    INT32_C(1000)
#define ONE_S_MS                   UINT32_C(1000)
#define ONE_UWH_PJ                 INT64_C(3600000000)
#define ONE_UWH                    INT32_C(1)

/* 3,600,000 nW x 1999 ms = 7,196,400,000 pJ = 1.999 uWh -> 1 (truncated).
 * Negative: -7,196,400,000 pJ = -1.999 uWh -> -1 toward zero (floor would give -2). */
#define NON_WHOLE_DT_MS            UINT32_C(1999)
#define NON_WHOLE_PJ               INT64_C(7196400000)

/* Timestamp wrap: 4 - 4,294,967,196 = 104 ms (mod 2^32).
 * 3,600,000 nW x 104 ms = 374,400,000 pJ. */
#define PRE_WRAP_MS                UINT32_C(4294967196)
#define POST_WRAP_MS               UINT32_C(4)
#define WRAP_INTERVAL_PJ           INT64_C(374400000)

/* OVERRANGE-level voltage: 6004 mV x 1000 uA = 6,004,000 nW.
 * Over 1000 ms: 6,004,000,000 pJ = 1.667 uWh -> 1. */
#define OVERRANGE_MV               UINT16_C(6004)
#define OVERRANGE_INTERVAL_PJ      INT64_C(6004000000)

/* 300 h at 6 W in 1000 ms steps.
 * 300 h = 300 x 3600 s = 1,080,000 s = 1,080,000 steps; the last timestamp is
 * 1,080,000,000 ms, below 2^32 so there is no wrap.
 * Each step: 6000 mV x 1,000,000 uA x 1000 ms = 6,000,000,000,000 pJ.
 * Total: 6e12 x 1,080,000 = 6,480,000,000,000,000,000 pJ
 *      = 6.48e18 / 3.6e9 = 1,800,000,000 uWh exactly (6 W x 300 h = 1800 Wh). */
#define RATED_MAX_MV               UINT16_C(6000)
#define RATED_MAX_UA               INT32_C(1000000)
#define LONG_RUN_STEP_COUNT        UINT32_C(1080000)
#define LONG_RUN_TOTAL_PJ          INT64_C(6480000000000000000)
#define LONG_RUN_TOTAL_UWH         INT32_C(1800000000)

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

static void test_starts_at_zero(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);
    TEST_ASSERT_FALSE(s.prev_valid);
    TEST_ASSERT_EQUAL_INT32(ZERO_UWH, pm_energy_uwh(&s));
    TEST_ASSERT_FALSE(pm_energy_is_saturated(&s));
}

static void test_first_sample_only_sets_baseline(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);
    TEST_ASSERT_TRUE(s.prev_valid);
    TEST_ASSERT_EQUAL_UINT32(ONE_S_MS, s.prev_timestamp_ms);
}

static void test_one_uwh_interval_is_exact(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, TEST_MV, TEST_UA);
    pm_energy_update(&s, ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ONE_UWH_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, pm_energy_uwh(&s));
}

static void test_positive_partial_uwh_truncates_down(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, TEST_MV, TEST_UA);
    pm_energy_update(&s, NON_WHOLE_DT_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(NON_WHOLE_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, pm_energy_uwh(&s));
}

static void test_negative_interval_decreases_and_truncates_toward_zero(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, TEST_MV, -TEST_UA);
    pm_energy_update(&s, NON_WHOLE_DT_MS, true, TEST_MV, -TEST_UA);
    TEST_ASSERT_EQUAL_INT64(-NON_WHOLE_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(-ONE_UWH, pm_energy_uwh(&s));
}

static void test_timestamp_wrap_integrates_104_ms(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, PRE_WRAP_MS, true, TEST_MV, TEST_UA);
    pm_energy_update(&s, POST_WRAP_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(WRAP_INTERVAL_PJ, s.acc_pj);
}

/* The invalid Sample carries non-zero V and I on purpose, to show they are
 * ignored. Timeline: valid at 0 s, invalid at 1 s, valid at 2 s (only a new
 * baseline), valid at 3 s (adds the 2 s -> 3 s interval, 1 uWh). */
static void test_invalid_sample_adds_nothing_and_next_only_rebaselines(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, TEST_MV, TEST_UA);
    pm_energy_update(&s, 1u * ONE_S_MS, false, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);
    TEST_ASSERT_FALSE(s.prev_valid);

    pm_energy_update(&s, 2u * ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);

    pm_energy_update(&s, 3u * ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ONE_UWH_PJ, s.acc_pj);
}

static void test_zero_dt_adds_nothing(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, ONE_S_MS, true, TEST_MV, TEST_UA);
    pm_energy_update(&s, ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);
}

/* The integrator has no notion of OVERRANGE: a reading above the Rated_Range
 * is still a real measurement and is integrated like any other. */
static void test_overrange_level_value_is_integrated(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, OVERRANGE_MV, TEST_UA);
    pm_energy_update(&s, ONE_S_MS, true, OVERRANGE_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(OVERRANGE_INTERVAL_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, pm_energy_uwh(&s));
}

/* +1 uWh then -1 uWh: exact accumulation returns to exactly 0. */
static void test_equal_positive_and_negative_intervals_cancel(void)
{
    pm_energy_state_t s;
    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, ZERO_MV, ZERO_UA);
    pm_energy_update(&s, 1u * ONE_S_MS, true, TEST_MV, TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ONE_UWH_PJ, s.acc_pj);
    pm_energy_update(&s, 2u * ONE_S_MS, true, TEST_MV, -TEST_UA);
    TEST_ASSERT_EQUAL_INT64(ZERO_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(ZERO_UWH, pm_energy_uwh(&s));
}

static void test_300_hours_at_6_w_is_exact_and_not_saturated(void)
{
    pm_energy_state_t s;
    uint32_t step;

    pm_energy_init(&s);
    pm_energy_update(&s, 0u, true, RATED_MAX_MV, RATED_MAX_UA);   /* baseline */
    for (step = 1u; step <= LONG_RUN_STEP_COUNT; step++) {
        pm_energy_update(&s, step * ONE_S_MS, true, RATED_MAX_MV, RATED_MAX_UA);
    }
    TEST_ASSERT_EQUAL_INT64(LONG_RUN_TOTAL_PJ, s.acc_pj);
    TEST_ASSERT_EQUAL_INT32(LONG_RUN_TOTAL_UWH, pm_energy_uwh(&s));
    TEST_ASSERT_FALSE(pm_energy_is_saturated(&s));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_starts_at_zero);
    RUN_TEST(test_first_sample_only_sets_baseline);
    RUN_TEST(test_one_uwh_interval_is_exact);
    RUN_TEST(test_positive_partial_uwh_truncates_down);
    RUN_TEST(test_negative_interval_decreases_and_truncates_toward_zero);
    RUN_TEST(test_timestamp_wrap_integrates_104_ms);
    RUN_TEST(test_invalid_sample_adds_nothing_and_next_only_rebaselines);
    RUN_TEST(test_zero_dt_adds_nothing);
    RUN_TEST(test_overrange_level_value_is_integrated);
    RUN_TEST(test_equal_positive_and_negative_intervals_cancel);
    RUN_TEST(test_300_hours_at_6_w_is_exact_and_not_saturated);
    return UNITY_END();
}
