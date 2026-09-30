"""Throwaway check that pytest, the package import, and hypothesis all work.

Delete once real host tests exist.
"""

import hypothesis

import power_monitor


def test_package_imports() -> None:
    assert power_monitor.__doc__ is not None


def test_hypothesis_profile_loaded() -> None:
    # Confirms conftest.py ran; otherwise hypothesis would use its defaults.
    assert hypothesis.settings().max_examples == 200
    assert hypothesis.settings().deadline is None
