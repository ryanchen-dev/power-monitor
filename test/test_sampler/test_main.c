/* Unity tests for pm_sampler (Req 2.2-2.6, 5.1, 5.5, 6.5, 7.5, 7.6, 15.3).
 *
 * Expected values are hand-computed literals (arithmetic in the comments).
 * Bus register: reading in bits 15..3, 4 mV per count. Current register:
 * two's complement, 100 uA per count. */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <unity.h>
#include "pm_sampler.h"

/* Filler for "was this field written?" checks: no expected value is 0xEE... */
#define SENTINEL_BYTE              0xEEu

#define FAIL_STATUS                PM_ERR_I2C_NACK   /* any non-OK driver result */
#define NO_FLAGS                   0x00u

/* Normal reading, same values as GV1:
 * bus 0x2712 = 10,002 -> >> 3 = 1250 counts x 4 mV = 5000 mV (CNVR bit 1 set, as after a conversion)
 * current 0x04D3 = 1235 counts x 100 uA = 123,500 uA
 * power 5000 x 123,500 = 617,500,000 nW = 617.5 mW -> 618 mW */
#define GV1_BUS_REG                UINT16_C(0x2712)
#define GV1_CURRENT_REG            UINT16_C(0x04D3)
#define GV1_MV                     UINT16_C(5000)
#define GV1_UA                     INT32_C(123500)
#define GV1_MW                     INT32_C(618)

/* Energy reading: bus 0x1C20 = 7200 -> 900 counts x 4 = 3600 mV;
 * current 0x000A = 10 counts x 100 = 1000 uA; power 3,600,000 nW = 3.6 -> 4 mW.
 * Over 1000 ms: 3,600,000 x 1000 = 3,600,000,000 pJ = exactly 1 uWh. */
#define ENERGY_BUS_REG             UINT16_C(0x1C20)
#define ENERGY_CURRENT_REG         UINT16_C(0x000A)
#define ENERGY_MV                  UINT16_C(3600)
#define ENERGY_UA                  INT32_C(1000)
#define ENERGY_MW                  INT32_C(4)

/* OVF bit (bit 0) set on an in-range reading: 0x1C21 still scales to 3600 mV. */
#define OVF_BUS_REG                UINT16_C(0x1C21)

/* Over-range current: 10,001 counts = 0x2711 -> 1,000,100 uA (one step over 1 A).
 * Negative: -10,001 counts = 65,536 - 10,001 = 55,535 = 0xD8EF -> -1,000,100 uA.
 * Power 3600 x 1,000,100 = 3,600,360,000 nW = 3600.36 mW -> 3600 mW. */
#define OVER_CURRENT_REG           UINT16_C(0x2711)
#define OVER_NEG_CURRENT_REG       UINT16_C(0xD8EF)
#define OVER_UA                    INT32_C(1000100)
#define OVER_MW                    INT32_C(3600)

/* 2,147,483,647 uWh x 3,600,000,000 pJ/uWh = 7,730,941,129,200,000,000 pJ. */
#define ENERGY_LIMIT_PJ            INT64_C(7730941129200000000)
#define MAX_REPORT_UWH             INT32_C(2147483647)

#define ZERO_MV                    UINT16_C(0)
#define ZERO_UA                    INT32_C(0)
#define ZERO_MW                    INT32_C(0)
#define ZERO_UWH                   INT32_C(0)
#define ONE_UWH                    INT32_C(1)
#define TWO_UWH                    INT32_C(2)
#define PERIOD_MS                  UINT32_C(1000)
#define FIRST_TIMESTAMP_MS         UINT32_C(1000)
#define MAX_SEQ                    UINT16_C(65535)

static const pm_raw_reading_t GV1_RAW      = { GV1_BUS_REG, GV1_CURRENT_REG };
static const pm_raw_reading_t ENERGY_RAW   = { ENERGY_BUS_REG, ENERGY_CURRENT_REG };

/* Unity calls these around every test; nothing to set up. */
void setUp(void) {}
void tearDown(void) {}

/* Runs pm_sampler_finish into a sentinel-filled Sample, so any field it
 * forgot to write would show up as 0xEE... in the assertions. */
static pm_sample_t finish(pm_sampler_t *s, uint32_t timestamp_ms, pm_sensor_action_t action,
                          pm_status_t result, const pm_raw_reading_t *raw)
{
    pm_sample_t out;
    memset(&out, SENTINEL_BYTE, sizeof out);
    pm_sampler_finish(s, timestamp_ms, action, result, raw, &out);
    return out;
}

static void assert_error_sample(const pm_sample_t *out)
{
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_SENSOR_ERROR, out->status_flags);
    TEST_ASSERT_EQUAL_UINT16(ZERO_MV, out->bus_voltage_mv);
    TEST_ASSERT_EQUAL_INT32(ZERO_UA, out->current_ua);
    TEST_ASSERT_EQUAL_INT32(ZERO_MW, out->power_mw);
}

static void test_first_sample_has_seq_zero(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);
    out = finish(&s, FIRST_TIMESTAMP_MS, PM_ACTION_READ, PM_OK, &GV1_RAW);
    TEST_ASSERT_EQUAL_UINT16(0u, out.seq);
}

static void test_boot_action_follows_configuration_result(void)
{
    pm_sampler_t s;
    pm_sampler_init(&s, false);
    TEST_ASSERT_EQUAL(PM_ACTION_REINIT, pm_sampler_next_action(&s));
    pm_sampler_init(&s, true);
    TEST_ASSERT_EQUAL(PM_ACTION_READ, pm_sampler_next_action(&s));
}

/* READ fail -> REINIT fail -> REINIT OK (still SENSOR_ERROR) -> READ OK.
 * Each step also checks the next requested action, the Timestamp, and seq,
 * so it covers "one Sample per call with the given Timestamp". Non-read
 * steps pass raw = NULL, which must never be dereferenced. */
static void test_fault_and_recovery_sequence(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);

    out = finish(&s, 1u * PERIOD_MS, PM_ACTION_READ, FAIL_STATUS, NULL);
    assert_error_sample(&out);
    TEST_ASSERT_EQUAL_UINT32(1u * PERIOD_MS, out.timestamp_ms);
    TEST_ASSERT_EQUAL_UINT16(0u, out.seq);
    TEST_ASSERT_EQUAL(PM_ACTION_REINIT, pm_sampler_next_action(&s));

    out = finish(&s, 2u * PERIOD_MS, PM_ACTION_REINIT, FAIL_STATUS, NULL);
    assert_error_sample(&out);
    TEST_ASSERT_EQUAL_UINT32(2u * PERIOD_MS, out.timestamp_ms);
    TEST_ASSERT_EQUAL_UINT16(1u, out.seq);
    TEST_ASSERT_EQUAL(PM_ACTION_REINIT, pm_sampler_next_action(&s));

    out = finish(&s, 3u * PERIOD_MS, PM_ACTION_REINIT, PM_OK, NULL);
    assert_error_sample(&out);
    TEST_ASSERT_EQUAL_UINT32(3u * PERIOD_MS, out.timestamp_ms);
    TEST_ASSERT_EQUAL_UINT16(2u, out.seq);
    TEST_ASSERT_EQUAL(PM_ACTION_READ, pm_sampler_next_action(&s));

    out = finish(&s, 4u * PERIOD_MS, PM_ACTION_READ, PM_OK, &GV1_RAW);
    TEST_ASSERT_EQUAL_HEX8(NO_FLAGS, out.status_flags);
    TEST_ASSERT_EQUAL_UINT16(GV1_MV, out.bus_voltage_mv);
    TEST_ASSERT_EQUAL_INT32(GV1_UA, out.current_ua);
    TEST_ASSERT_EQUAL_INT32(GV1_MW, out.power_mw);
    TEST_ASSERT_EQUAL_UINT32(4u * PERIOD_MS, out.timestamp_ms);
    TEST_ASSERT_EQUAL_UINT16(3u, out.seq);
    TEST_ASSERT_EQUAL(PM_ACTION_READ, pm_sampler_next_action(&s));
}

/* An in-range READ OK has flags exactly 0, and every field is written. */
static void test_in_range_read_has_no_flags_and_all_fields(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);
    out = finish(&s, FIRST_TIMESTAMP_MS, PM_ACTION_READ, PM_OK, &GV1_RAW);
    TEST_ASSERT_EQUAL_HEX8(NO_FLAGS, out.status_flags);
    TEST_ASSERT_EQUAL_UINT16(0u, out.seq);
    TEST_ASSERT_EQUAL_UINT32(FIRST_TIMESTAMP_MS, out.timestamp_ms);
    TEST_ASSERT_EQUAL_UINT16(GV1_MV, out.bus_voltage_mv);
    TEST_ASSERT_EQUAL_INT32(GV1_UA, out.current_ua);
    TEST_ASSERT_EQUAL_INT32(GV1_MW, out.power_mw);
    TEST_ASSERT_EQUAL_INT32(ZERO_UWH, out.energy_uwh);   /* first Sample: baseline only */
}

/* The two non-read combinations not covered by the sequence above, with raw = NULL. */
static void test_null_raw_is_never_read(void)
{
    pm_sampler_t s;
    pm_sample_t out;

    pm_sampler_init(&s, false);
    out = finish(&s, FIRST_TIMESTAMP_MS, PM_ACTION_REINIT, PM_OK, NULL);
    assert_error_sample(&out);

    pm_sampler_init(&s, true);
    out = finish(&s, FIRST_TIMESTAMP_MS, PM_ACTION_READ, FAIL_STATUS, NULL);
    assert_error_sample(&out);
}

static void test_overrange_from_ovf_bit(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    const pm_raw_reading_t raw = { OVF_BUS_REG, ENERGY_CURRENT_REG };
    pm_sampler_init(&s, true);
    out = finish(&s, FIRST_TIMESTAMP_MS, PM_ACTION_READ, PM_OK, &raw);
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_OVERRANGE, out.status_flags);
    TEST_ASSERT_EQUAL_UINT16(ENERGY_MV, out.bus_voltage_mv);   /* values are still reported */
    TEST_ASSERT_EQUAL_INT32(ENERGY_UA, out.current_ua);
    TEST_ASSERT_EQUAL_INT32(ENERGY_MW, out.power_mw);
}

static void test_overrange_from_current_outside_rated_range(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    const pm_raw_reading_t over_pos = { ENERGY_BUS_REG, OVER_CURRENT_REG };
    const pm_raw_reading_t over_neg = { ENERGY_BUS_REG, OVER_NEG_CURRENT_REG };
    pm_sampler_init(&s, true);

    out = finish(&s, 1u * PERIOD_MS, PM_ACTION_READ, PM_OK, &over_pos);
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_OVERRANGE, out.status_flags);
    TEST_ASSERT_EQUAL_INT32(OVER_UA, out.current_ua);
    TEST_ASSERT_EQUAL_INT32(OVER_MW, out.power_mw);

    out = finish(&s, 2u * PERIOD_MS, PM_ACTION_READ, PM_OK, &over_neg);
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_OVERRANGE, out.status_flags);
    TEST_ASSERT_EQUAL_INT32(-OVER_UA, out.current_ua);
    TEST_ASSERT_EQUAL_INT32(-OVER_MW, out.power_mw);
}

/* Integrator poked to exactly +limit with a valid baseline at 0 ms; the next
 * positive interval would pass it, so it saturates. ENERGY_SATURATED then
 * appears on its own for a READ OK, and alongside SENSOR_ERROR for a fault. */
static void test_energy_saturated_flag(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);
    s.energy.acc_pj = ENERGY_LIMIT_PJ;
    s.energy.prev_valid = true;
    s.energy.prev_timestamp_ms = 0u;

    out = finish(&s, 1u * PERIOD_MS, PM_ACTION_READ, PM_OK, &ENERGY_RAW);
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_ENERGY_SATURATED, out.status_flags);
    TEST_ASSERT_EQUAL_INT32(MAX_REPORT_UWH, out.energy_uwh);

    out = finish(&s, 2u * PERIOD_MS, PM_ACTION_READ, FAIL_STATUS, NULL);
    TEST_ASSERT_EQUAL_HEX8(PM_FLAG_SENSOR_ERROR | PM_FLAG_ENERGY_SATURATED, out.status_flags);
    TEST_ASSERT_EQUAL_INT32(MAX_REPORT_UWH, out.energy_uwh);
}

/* Timeline (1 uWh per valid 1000 ms interval at 3600 mV x 1000 uA):
 *   0 s READ OK      baseline              0 uWh
 *   1 s READ OK      +1                    1
 *   2 s READ fail    nothing; kept         1  (not dropped to 0)
 *   3 s REINIT OK    nothing; kept         1
 *   4 s READ OK      re-baseline only      1  (interval 3 s -> 4 s touches an error Sample)
 *   5 s READ OK      +1                    2 */
static void test_energy_holds_across_fault_and_rebaselines(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);

    out = finish(&s, 0u * PERIOD_MS, PM_ACTION_READ, PM_OK, &ENERGY_RAW);
    TEST_ASSERT_EQUAL_INT32(ZERO_UWH, out.energy_uwh);
    out = finish(&s, 1u * PERIOD_MS, PM_ACTION_READ, PM_OK, &ENERGY_RAW);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, out.energy_uwh);
    out = finish(&s, 2u * PERIOD_MS, PM_ACTION_READ, FAIL_STATUS, NULL);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, out.energy_uwh);
    out = finish(&s, 3u * PERIOD_MS, PM_ACTION_REINIT, PM_OK, NULL);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, out.energy_uwh);
    out = finish(&s, 4u * PERIOD_MS, PM_ACTION_READ, PM_OK, &ENERGY_RAW);
    TEST_ASSERT_EQUAL_INT32(ONE_UWH, out.energy_uwh);
    out = finish(&s, 5u * PERIOD_MS, PM_ACTION_READ, PM_OK, &ENERGY_RAW);
    TEST_ASSERT_EQUAL_INT32(TWO_UWH, out.energy_uwh);
}

static void test_seq_wraps_at_max(void)
{
    pm_sampler_t s;
    pm_sample_t out;
    pm_sampler_init(&s, true);
    s.next_seq = MAX_SEQ;

    out = finish(&s, 1u * PERIOD_MS, PM_ACTION_READ, PM_OK, &GV1_RAW);
    TEST_ASSERT_EQUAL_UINT16(MAX_SEQ, out.seq);
    out = finish(&s, 2u * PERIOD_MS, PM_ACTION_READ, PM_OK, &GV1_RAW);
    TEST_ASSERT_EQUAL_UINT16(0u, out.seq);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_first_sample_has_seq_zero);
    RUN_TEST(test_boot_action_follows_configuration_result);
    RUN_TEST(test_fault_and_recovery_sequence);
    RUN_TEST(test_in_range_read_has_no_flags_and_all_fields);
    RUN_TEST(test_null_raw_is_never_read);
    RUN_TEST(test_overrange_from_ovf_bit);
    RUN_TEST(test_overrange_from_current_outside_rated_range);
    RUN_TEST(test_energy_saturated_flag);
    RUN_TEST(test_energy_holds_across_fault_and_rebaselines);
    RUN_TEST(test_seq_wraps_at_max);
    return UNITY_END();
}
