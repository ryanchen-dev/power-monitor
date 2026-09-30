/* Throwaway smoke test: proves Unity runs and that the Library Dependency
 * Finder resolves lib/pm_logic from this #include. Deleted in task 2.1. */
#include <unity.h>

#include "pm_status.h"

/* Unity calls these around every test; nothing to set up yet. */
void setUp(void) {}
void tearDown(void) {}

static void test_pm_ok_is_zero(void)
{
    TEST_ASSERT_EQUAL_INT(0, PM_OK);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_pm_ok_is_zero);
    return UNITY_END();
}
