"""Shared pytest setup for the host tests.

Import note: `python -m pytest` run from `host/` puts the current directory
on `sys.path`, so `import power_monitor` finds `host/power_monitor/` with no
packaging or install step. (Plain `pytest` would not add it.)
"""

from hypothesis import settings

# 200 examples per property: comfortably above the 100 the requirements ask
# for, while keeping the whole suite fast enough to run on every change.
# deadline=None: hypothesis fails any example slower than 200 ms by default.
# The later ctypes fixture compiles and loads the C library on its first call,
# which would trip that limit even though the code under test is fast.
settings.register_profile("power_monitor", max_examples=200, deadline=None)
settings.load_profile("power_monitor")
