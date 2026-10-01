"""Compile-time configuration checks in src/pm_config.h (Req 2.1, 6.1, 6.2, 6.3).

Each test compiles a small C snippet that includes pm_config.h, with -D
overrides, and checks whether the build passes or stops at an #error.

Why compile instead of reading the header as text: the requirement is that the
*build* fails with a message naming the constant. Only the real preprocessor
shows that the #if arithmetic, the #ifndef overrides, and the #error text all
work together.

Why `-fsyntax-only`: the compiler preprocesses and type-checks the snippet but
writes no object file, so nothing lands in the repo. The snippet comes from
stdin (`-x c -`), so there is no temporary source file either.

Why the #ifndef guards matter here: `-DSAMPLE_RATE_HZ=51` defines the macro
before pm_config.h is read, so the header's `#ifndef SAMPLE_RATE_HZ` skips its
default and the checks see 51. That is how one header can be built with values
that must fail, without editing it.
"""

import subprocess
from pathlib import Path

import pytest

# Located from this file, not the current directory, so the include paths
# below resolve the same way whether pytest is run from host/ or the repo root.
# parents[0] is host/tests, parents[1] is host, parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# C11 for _Static_assert. -Wall -Wextra so a passing case must also be
# warning-free (the passing tests require empty stderr).
# -fmessage-length=0 is defensive and only changes how diagnostics are printed.
# Clang wraps long messages (to COLUMNS) only when stderr is a terminal, and
# this test captures stderr through a pipe, so today nothing wraps. The flag
# guarantees one-line diagnostics regardless of compiler or version, so the
# "error:" line match in error_lines() can't break on a wrapped #error message.
CC_COMMAND = [
    "cc", "-std=c11", "-fsyntax-only", "-Wall", "-Wextra",
    "-x", "c", "-",
    "-I", "src", "-I", "lib/pm_logic/src",
    "-fmessage-length=0",
]
CC_TIMEOUT_S = 30  # a syntax-only compile of one header takes well under a second

INCLUDE_ONLY = '#include "pm_config.h"\n'
DEFAULT_PERIOD_SNIPPET = (
    INCLUDE_ONLY
    + '_Static_assert(SAMPLE_PERIOD_MS == 100, "default SAMPLE_PERIOD_MS must be 100 ms");\n'
)
I2C_TIMEOUT_SNIPPET = (
    INCLUDE_ONLY
    + "_Static_assert(I2C_TIMEOUT_US < US_PER_S / SAMPLE_RATE_MAX_HZ, "
    + '"I2C timeout must be shorter than the 50 Hz Sample_Period");\n'
)

UART_ERROR_TEXT = (
    "Frame traffic exceeds 50% of the UART bit rate: lower SAMPLE_RATE_HZ or raise UART_BAUD_BPS"
)


def compile_snippet(snippet: str, defines: dict[str, int] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the syntax-only compile from the repo root and return the result."""
    define_args = [f"-D{name}={value}" for name, value in (defines or {}).items()]
    return subprocess.run(
        CC_COMMAND + define_args,
        input=snippet,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=CC_TIMEOUT_S,
        check=False,
    )


def error_lines(stderr: str) -> list[str]:
    # Only the compiler's "error:" lines. clang also echoes the offending
    # source line under each diagnostic, so searching all of stderr for
    # "SAMPLE_RATE_HZ" could match an unrelated error on a line that merely
    # mentions the macro. Requiring it in an error line means the #error
    # message itself names the constant.
    return [line for line in stderr.splitlines() if "error:" in line]


def assert_passes(result: subprocess.CompletedProcess[str]) -> None:
    # Empty stderr, not just return code 0, so a new warning is noticed too.
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr


def assert_fails_with(result: subprocess.CompletedProcess[str], text: str) -> None:
    assert result.returncode != 0, "expected the build to fail"
    assert any(text in line for line in error_lines(result.stderr)), result.stderr


# --- Req 6.1: defaults ---------------------------------------------------------


def test_defaults_pass() -> None:
    assert_passes(compile_snippet(INCLUDE_ONLY))


def test_default_sample_period_is_100_ms() -> None:
    # 10 Hz default -> 1000 / 10 = 100 ms. _Static_assert makes the compiler
    # check a value the preprocessor computed, without running anything.
    assert_passes(compile_snippet(DEFAULT_PERIOD_SNIPPET))


# --- Req 6.2: sample rate limits -----------------------------------------------


@pytest.mark.parametrize("rate_hz", [0, 51])
def test_sample_rate_outside_limits_fails_naming_the_constant(rate_hz: int) -> None:
    result = compile_snippet(INCLUDE_ONLY, {"SAMPLE_RATE_HZ": rate_hz})
    assert_fails_with(result, "SAMPLE_RATE_HZ")


@pytest.mark.parametrize("rate_hz", [1, 50])
def test_sample_rate_at_limits_passes(rate_hz: int) -> None:
    # The limits themselves are allowed (1 to 50 Hz inclusive).
    assert_passes(compile_snippet(INCLUDE_ONLY, {"SAMPLE_RATE_HZ": rate_hz}))


# --- Req 6.3: UART load ----------------------------------------------------------


def test_uart_overload_fails_with_uart_message() -> None:
    # 25 bytes x 10 bits x 50 Hz = 12,500 bit/s, which is 130% of 9600 baud.
    # In the header's multiplied form: 1,250,000 > 9600 x 50 = 480,000.
    result = compile_snippet(INCLUDE_ONLY, {"UART_BAUD_BPS": 9600, "SAMPLE_RATE_HZ": 50})
    assert_fails_with(result, UART_ERROR_TEXT)


# Req 6.3 fails the build only for MORE than 50%, so exactly 50% must pass.
# At 50 Hz the Frame traffic is 25 x 10 x 50 = 12,500 bit/s:
#   25000 baud: 1,250,000 > 25,000 x 50 = 1,250,000 is false -> exactly 50%, passes
#   24999 baud: 1,250,000 > 24,999 x 50 = 1,249,950 is true  -> just over 50%, fails
# This pins the header's ">" against ">=": an off-by-one there would still fail
# at 9600 baud (130% load), so the case above can't tell them apart.
def test_uart_load_of_exactly_50_percent_passes() -> None:
    result = compile_snippet(INCLUDE_ONLY, {"UART_BAUD_BPS": 25000, "SAMPLE_RATE_HZ": 50})
    assert_passes(result)


def test_uart_load_just_over_50_percent_fails() -> None:
    result = compile_snippet(INCLUDE_ONLY, {"UART_BAUD_BPS": 24999, "SAMPLE_RATE_HZ": 50})
    assert_fails_with(result, UART_ERROR_TEXT)


# --- Req 2.1: I2C timeout ---------------------------------------------------------


def test_i2c_timeout_is_shorter_than_the_50_hz_period() -> None:
    # 5000 us < 1,000,000 / 50 = 20,000 us. Checks the constants directly, in
    # addition to the header's own #error (which this compile also runs).
    assert_passes(compile_snippet(I2C_TIMEOUT_SNIPPET))
