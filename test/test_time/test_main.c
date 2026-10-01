/* Unity tests for the wrap-safe time helpers (Req 5.2, 6.4, 15.3). */
#include <stdint.h>
#include <unity.h>
#include "pm_time.h"

/* Elapsed across the millis() wrap: 100 ms before 2^32, then 4 ms after it. */
#define WRAP_EARLIER_MS          UINT32_C(4294967196)  /* 2^32 - 100 */
#define WRAP_LATER_MS            UINT32_C(4)
#define WRAP_ELAPSED_MS          UINT32_C(104)

/* Elapsed with no wrap. */
#define PLAIN_EARLIER_MS         UINT32_C(1000)
#define PLAIN_LATER_MS           UINT32_C(1100)
#define PLAIN_ELAPSED_MS         UINT32_C(100)

#define SAME_INSTANT_MS          UINT32_C(123456789)
#define ZERO_MS                  UINT32_C(0)

/* Deadline that has wrapped past 2^32 while now hasn't yet: 10 ms ahead.
 * This is the case where `now >= deadline` would wrongly say "reached". */
#define PRE_WRAP_NOW_MS          UINT32_C(4294967290)  /* 2^32 - 6 */
#define WRAPPED_DEADLINE_MS      UINT32_C(4)

#define DEADLINE_MS              UINT32_C(4)
#define ONE_MS_PAST_DEADLINE_MS  UINT32_C(5)

/* Far-ahead deadlines, measured from an arbitrary base time. Written as
 * literals rather than with PM_TIME_HALF_RANGE_MS so the test doesn't reuse
 * the constant under test. */
#define BASE_NOW_MS              UINT32_C(1000)
#define HALF_CIRCLE_MS           UINT32_C(2147483648)  /* 2^31 */
#define HALF_CIRCLE_MINUS_ONE_MS UINT32_C(2147483647)  /* 2^31 - 1 */

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

static void test_elapsed_across_wrap(void)
{
    TEST_ASSERT_EQUAL_UINT32(WRAP_ELAPSED_MS, pm_elapsed_ms(WRAP_EARLIER_MS, WRAP_LATER_MS));
}

static void test_elapsed_without_wrap(void)
{
    TEST_ASSERT_EQUAL_UINT32(PLAIN_ELAPSED_MS, pm_elapsed_ms(PLAIN_EARLIER_MS, PLAIN_LATER_MS));
}

static void test_elapsed_same_instant_is_zero(void)
{
    TEST_ASSERT_EQUAL_UINT32(ZERO_MS, pm_elapsed_ms(SAME_INSTANT_MS, SAME_INSTANT_MS));
}

static void test_wrapped_deadline_is_not_reached_early(void)
{
    TEST_ASSERT_FALSE(pm_deadline_reached(PRE_WRAP_NOW_MS, WRAPPED_DEADLINE_MS));
}

static void test_deadline_reached_when_equal(void)
{
    TEST_ASSERT_TRUE(pm_deadline_reached(DEADLINE_MS, DEADLINE_MS));
}

static void test_deadline_reached_when_past(void)
{
    TEST_ASSERT_TRUE(pm_deadline_reached(ONE_MS_PAST_DEADLINE_MS, DEADLINE_MS));
}

static void test_deadline_just_under_half_circle_ahead_is_not_reached(void)
{
    uint32_t deadline_ms = BASE_NOW_MS + HALF_CIRCLE_MINUS_ONE_MS;
    TEST_ASSERT_FALSE(pm_deadline_reached(BASE_NOW_MS, deadline_ms));
}

/* The half-circle point itself. now - deadline is exactly 2^31, which this
 * implementation and the signed-difference form both call "not reached".
 * It is outside the documented validity range; the test pins the behavior
 * so a future change to the comparison can't flip it silently. */
static void test_deadline_exactly_half_circle_ahead_is_not_reached(void)
{
    uint32_t deadline_ms = BASE_NOW_MS + HALF_CIRCLE_MS;
    TEST_ASSERT_FALSE(pm_deadline_reached(BASE_NOW_MS, deadline_ms));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_elapsed_across_wrap);
    RUN_TEST(test_elapsed_without_wrap);
    RUN_TEST(test_elapsed_same_instant_is_zero);
    RUN_TEST(test_wrapped_deadline_is_not_reached_early);
    RUN_TEST(test_deadline_reached_when_equal);
    RUN_TEST(test_deadline_reached_when_past);
    RUN_TEST(test_deadline_just_under_half_circle_ahead_is_not_reached);
    RUN_TEST(test_deadline_exactly_half_circle_ahead_is_not_reached);
    return UNITY_END();
}
