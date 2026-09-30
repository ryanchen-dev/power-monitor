# Implementation Plan: Power Monitor

Run one sub-task at a time, then stop for review. Each sub-task leaves `pio test -e native` and `cd host && python -m pytest` passing. The one planned exception is `pio run -e uno`, which links only once `main.cpp` exists (task 24.1).

## Overview

The plan builds the project in five vertical slices. Each slice ends with something that runs.

1. **Packet format end to end.** Scaffolding, then the CRC-8 and the Frame encoder in C and in Python, then a drift test that keeps the Golden_Vectors in `docs/packet_format.md`, the Unity test, and the Python test identical. At the end, both sides produce the same bytes.
2. **`python -m power_monitor --simulate` runs.** FrameParser, Simulator, CsvLogger, and Live_Plot. LinkMonitor and the CLI are also in this slice because the pipeline can't run without them: the CLI is the entry point, and LinkMonitor produces the flag warnings and the exit summary. All host properties (11–18) are required.
3. **Firmware logic modules.** Scaling, time helpers, Power, Energy, and `pm_sampler` in C99, each created together with its Unity tests. The exhaustive Properties 1, 2, and 9 are required Unity loops.
4. **Firmware on the Uno.** Compile-time config checks, UART and INA219 drivers, `main.cpp`, static source-rule checks, and the first measured RAM/flash sizes. At the end, the Uno streams SENSOR_ERROR Frames to the host with no sensor attached.
5. **Fault-handling properties and verification report (last).** Optional `ctypes`/hypothesis properties for the firmware logic (fault policy first), then bench verification with the INA219 and the finished `docs/verification_report.md`.

Conventions for every sub-task: snake_case functions, UPPER_SNAKE_CASE macros, named constants with units in the name, no `float`/`double` and no dynamic allocation in firmware, a `pm_status_t` return from every function that touches hardware, and type hints on every Python function. Only the four approved host packages are used, pinned as in the design. Every sub-task that writes code has a "Comment the why" bullet listing the decisions its comments must explain.

## Tasks

### Slice 1: Packet format end to end

- [ ] 1. Scaffold the firmware and host projects
  - _Requirements: 15.1, 15.2, 15.5_

  - [-] 1.1 Create the firmware scaffold and prove `pio test -e native` runs
    - Create `platformio.ini` exactly as in the design (both environments, pinned platforms, `test_ignore = *` for `uno`, the `-Wall -Wextra` and signed-overflow trap flags for `native`), including its comments.
    - Create `src/`, `lib/pm_logic/src/`, and `test/`. Empty directories get a `.gitkeep`, since git does not track empty directories.
    - Create `lib/pm_logic/src/pm_status.h` with the `pm_status_t` enum from the design, an include guard, and `extern "C"` guards.
    - Create `.gitignore` with `.pio/`, `.venv/`, `__pycache__/`, `power_log_*.csv`, and `.hypothesis/` (hypothesis's local example database).
    - Create a throwaway `test/test_smoke/test_main.c` that includes `pm_status.h` and asserts `PM_OK == 0`. That shows Unity runs and that PlatformIO's Library Dependency Finder picks up `lib/pm_logic`.
    - Run `pio test -e native -v` and note the Unity version it prints, for the README in 1.3.
    - Comment the why: why the `uno` environment ignores tests; what `-fsanitize-trap=signed-integer-overflow` catches and why signed overflow is undefined behavior in C; why `pm_status.h` has `extern "C"` guards (the C++ drivers include C headers without name mangling); why `PM_OK` is 0.
    - _Requirements: 1.4, 8.4, 15.1, 15.2_

  - [x] 1.2 Create the host scaffold and prove pytest runs
    - Check `python3 --version` is at least 3.10 (needed for `@dataclass(slots=True)` and `X | None` annotations). If it is older, stop and tell the user.
    - Create `host/requirements.txt` with the exact pins from the design: `pyserial==3.5`, `matplotlib==3.11.2`, `pytest==9.1.1`, `hypothesis==6.168.3`. Create `.venv` and install them.
    - Create `host/power_monitor/__init__.py` (a docstring only).
    - Create `host/tests/conftest.py` that registers and loads a hypothesis profile with `max_examples=200` and `deadline=None`.
    - Create a throwaway `host/tests/test_smoke.py` that imports `power_monitor` and `hypothesis`, then run `cd host && python -m pytest`.
    - Comment the why: why 200 examples (above the 100 minimum) and why `deadline=None` (the later `ctypes` fixture's first call includes compiling and loading a library); why `python -m pytest` run from `host/` can import `power_monitor` with no packaging step (the current directory is added to `sys.path`).
    - _Requirements: 15.5_

  - [ ] 1.3 Write the README
    - Create `README.md` with a one-paragraph project description, the host setup line `python3 -m venv .venv && source .venv/bin/activate && pip install -r host/requirements.txt`, and the three commands `pio test -e native`, `cd host && python -m pytest`, and `pio run -e uno`, each with a one-line purpose. Record the Unity version noted in 1.1.
    - Comment the why: state in one line why the Unity version is recorded (PlatformIO Core supplies Unity, so the version isn't pinned in `platformio.ini`).
    - _Requirements: 15.2, 15.5, 16.1, 16.2_

- [ ] 2. Implement the C CRC_Module
  - _Requirements: 7.3, 7.8, 15.3_

  - [ ] 2.1 Implement `pm_crc8` with its Unity tests
    - Create `lib/pm_logic/src/pm_crc8.h/.c` with `PM_CRC8_POLY`, `PM_CRC8_INIT`, and the bitwise `pm_crc8(const uint8_t *data, size_t len)` from the design.
    - Create `test/test_crc8/test_main.c`: `"123456789"` gives `0xF4`; empty input gives `PM_CRC8_INIT`; GV1 bytes 2–23 give `0x77`.
    - Delete `test/test_smoke/`.
    - Comment the why: briefly explain what a CRC is (polynomial division with XOR, where the remainder is the checksum); why the loop is bitwise instead of table-driven (on AVR a `const` 256-byte table is copied into SRAM, which is 25% of the RAM budget); why each shift result is cast to `uint8_t` (integer promotion to `int`); what the implicit x⁸ term in `0x07` means.
    - _Requirements: 7.3, 7.8, 14.2, 15.3_

- [ ] 3. Implement the C Frame_Encoder
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.7, 14.4, 15.3_

  - [ ] 3.1 Implement `pm_frame` with its Golden_Vector Unity tests
    - Create `lib/pm_logic/src/pm_frame.h/.c` with the Frame constants (`PM_FRAME_LEN_BYTES`, sync bytes, CRC offsets), the `PM_FLAG_*` bits, `pm_sample_t`, private `put_u16_le()`/`put_u32_le()`, `pm_frame_encode()`, and `pm_frame_next_seq()`.
    - Create `test/test_frame/test_main.c` with `static const uint8_t GV1_BYTES[PM_FRAME_LEN_BYTES] = {...};` (the same form for GV2 and GV3, so the drift test in 5.1 can parse them) and the matching `pm_sample_t` values from `docs/packet_format.md`.
    - Tests: GV1–GV3 encode byte for byte; a 24-byte buffer returns `PM_ERR_BUFFER_TOO_SMALL` and is left untouched (prefill with a sentinel and compare); with a 26-byte buffer the last byte is left untouched; `pm_frame_next_seq` examples 0 → 1 and 65535 → 0.
    - Comment the why: briefly explain little-endian; why fields are written with shifts instead of `memcpy` of the struct (padding differs: 21 bytes on AVR, 24 on the Mac); why converting a signed value to `uint32_t` gives two's-complement bytes (C defines it as modulo 2³²); why `PM_FRAME_LEN_BYTES` is a plain integer (it must work in `#if`); why `pm_frame_next_seq` casts after adding (promotion to `int`).
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.7, 14.4, 15.3_

  - [ ] 3.2 Add the exhaustive sequence-wrap test (Unity, required)
    - **Property 9: Sequence numbers wrap**
    - **Validates: Requirements 7.5**
    - In `test/test_frame/test_main.c`, loop over all 65,536 values of `s` and assert `pm_frame_next_seq(s) == (s + 1) % 65536`.
    - Comment the why: why the loop variable is `uint32_t` and not `uint16_t` (a `uint16_t` counter can never exceed 65535, so `s <= 65535` is always true and the loop never ends); why testing all inputs is stronger than random sampling here.
    - _Requirements: 7.5_

- [ ] 4. Implement the host protocol module
  - _Requirements: 7.8, 9.1, 9.8, 14.1, 14.2, 14.4_

  - [ ] 4.1 Implement `protocol.py` with `test_protocol.py`
    - Create `host/power_monitor/protocol.py` with the design's constants (`SYNC`, `FRAME_LEN_BYTES`, `PAYLOAD_STRUCT = struct.Struct("<HIBHiii")`, CRC-8 parameters, flag bits), the frozen `Sample` dataclass with `slots=True`, `crc8()`, `encode_frame()` (raises `ValueError` if any field is outside its wire type range), `decode_frame()` (raises `ValueError` on wrong input length, bad sync, bad length byte, or bad CRC), and `flag_names()` (names bits 3–7 `RESERVED_BITn`).
    - Create `host/tests/test_protocol.py` with a module-level `GOLDEN_VECTORS: list[tuple[Sample, bytes]]`, one `bytes.fromhex("AA 55 ...")` literal per vector, copied from `docs/packet_format.md`.
    - Tests: GV1–GV3 encode to their bytes and decode to their Samples; `crc8(b"123456789") == 0xF4`; `PAYLOAD_STRUCT.size == 21` and `FRAME_LEN_BYTES == 25`; each field one past its minimum and maximum raises `ValueError` on encode; bad sync, bad length byte, bad CRC, and wrong input length raise on decode; `flag_names(0x06) == ["OVERRANGE", "ENERGY_SATURATED"]`; `flag_names(0x20) == ["RESERVED_BIT5"]`.
    - Delete `host/tests/test_smoke.py`.
    - Comment the why: what `<` means in a `struct` format (little-endian, standard sizes, no padding); why `encode_frame` validates ranges instead of letting `struct` wrap or error; why reserved flag bits are named and not hidden; why `Sample` is frozen (it can be compared and hashed in tests and can't change after decoding).
    - _Requirements: 7.8, 9.1, 9.8, 13.8, 14.1, 14.2, 14.4_

- [ ] 5. Add the Golden_Vector drift test
  - _Requirements: 14.3, 14.4_

  - [ ] 5.1 Implement `test_golden_vectors_sync.py`
    - Create `host/tests/test_golden_vectors_sync.py`. It parses the `GVn:` lines in `docs/packet_format.md`, the `GVn_BYTES` arrays in `test/test_frame/test_main.c`, and the `bytes.fromhex(...)` literals in `host/tests/test_protocol.py` (read as text, found in order), then asserts all three sources hold the same vectors in the same order.
    - Also assert: at least two vectors; each is 25 bytes; at least one decodes to negative Current, negative Power, and non-zero `status_flags`; at least one has LSB ≠ MSB in every multi-byte field (`seq`, `timestamp_ms`, `bus_voltage_mv`, `current_ua`, `power_mw`, `energy_uwh`).
    - Each parser fails with a clear message if it finds zero vectors, so a formatting change can't make the test pass trivially.
    - Comment the why: why a text-parsing drift test was chosen over a code generator (no extra build step to explain); why the LSB ≠ MSB check exists (it makes any byte-order mistake change the bytes); why finding zero vectors must fail.
    - _Requirements: 14.3, 14.4_

- [ ] 6. Checkpoint: packet format agrees on both sides
  - Ensure all tests pass, ask the user if questions arise.
  - `pio test -e native` and `cd host && python -m pytest` are both green. C and Python produce identical bytes for GV1–GV3, and the drift test passes.
  - _Requirements: 7.7, 9.8, 14.1, 14.4, 15.2, 15.5_

### Slice 2: `python -m power_monitor --simulate` runs

LinkMonitor (task 9) and the CLI (task 11) are in this slice because the pipeline needs them: the CLI is how the session starts and stops, and LinkMonitor provides the flag warnings and the exit summary.

- [ ] 7. Implement the FrameParser
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [ ] 7.1 Implement `parser.py` with example tests
    - Create `host/power_monitor/parser.py` with `FrameParser` (`checksum_errors`, `feed(data) -> list[Sample]`) that follows the design's six parsing rules.
    - Create `host/tests/test_parser.py` with examples: one Frame in one read; one Frame split across three reads; garbage before a Frame is dropped; a wrong length byte and a bad CRC are each counted once; a valid Frame that starts inside a rejected candidate is still found; two back-to-back Frames; the internal buffer holds at most 24 bytes after `feed` returns.
    - Comment the why: briefly explain sync-byte resynchronization; why a failed candidate drops only its first byte and not all 25; why the length byte is checked before waiting for the full Frame; why a length mismatch counts as a checksum error (Req 9.2 groups them).
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ] 7.2 Write the round-trip property test and shared strategies
    - **Property 11: Encode then parse is the identity**
    - **Validates: Requirements 9.1, 9.5**
    - Create `host/tests/strategies.py` with `valid_samples()`, built from per-field `st.one_of(edge values, full range)`. Edge values: 0, min, max, `0x7FFF`/`0x8000`, 6000 mV, ±1,000,000 µA, `seq` near 65535, Timestamps within 10 s of 2³².
    - In `test_parser.py`, feed `encode_frame(s)` to a new parser and assert it returns `[s]` with `checksum_errors == 0`. Add the tag comment `# Feature: power-monitor, Property 11: ...`.
    - Comment the why: briefly explain property-based testing and shrinking; why the generators lean toward edge values (random 32-bit integers almost never hit boundaries).
    - _Requirements: 9.1, 9.5, 15.5_

  - [ ] 7.3 Write the single-bit-error property test
    - **Property 12: Single-bit errors are rejected and counted**
    - **Validates: Requirements 9.2, 9.6**
    - Generate a valid Sample and a bit index covering bytes 2–24 (bits 16–199), flip that bit, feed the result to a new parser, and assert no Sample is returned and `checksum_errors >= 1`.
    - Comment the why: why sync bytes are excluded (a corrupted sync is never a candidate, so it's handled as garbage, not as a checksum error); why the CRC detects every single-bit error (the polynomial has more than one term).
    - _Requirements: 9.2, 9.6, 15.5_

  - [ ] 7.4 Write the resynchronization property test
    - **Property 13: The parser resynchronizes across garbage and split reads**
    - **Validates: Requirements 9.3, 9.4, 9.7**
    - Generate a list of valid Samples, garbage byte strings with no `0xAA` inserted before, between, and after their Frames, and random split points. Feed the chunks in order and assert the exact Samples come back in order with `checksum_errors == 0`.
    - Comment the why: why garbage excludes `0xAA` (garbage containing a false sync can cost a checksum error, which Req 9.7 excludes); why random split points model serial reads that return arbitrary byte counts.
    - _Requirements: 9.3, 9.4, 9.7, 15.5_

- [ ] 8. Implement the Simulator
  - _Requirements: 13.2, 13.3_

  - [ ] 8.1 Implement `simulator.py` with its tests
    - Create `host/power_monitor/simulator.py` with `SIM_SAMPLE_RATE_HZ = 10`, `simulated_values(t_s)` (the design's sine waves, rounded to multiples of 4 mV and 100 µA), `Simulator.next_sample(timestamp_ms)` (seq from 0 with wrap, Power rounded half away from zero, Energy accumulated exactly in integer pJ and truncated toward zero), and `SimulatedSource(simulator, clock, sleep)` with `read()` (sleeps until the next Frame is due, then returns its 25 encoded bytes) and `close()`.
    - Create `host/tests/test_simulator.py`: the first Sample has seq 0; seq wraps 65535 → 0; values change over time; negative current appears; Power matches the round-half-away rule at 617.5 → 618 and −3866.5 → −3867; `decode_frame(source.read())` gives the Simulator's Sample; with a fake clock and fake sleep, `read()` sleeps the right amount and Frames are 100 ms apart.
    - Comment the why: why Power uses integer arithmetic instead of `round()` (Python's `round()` rounds half to even); why `//` isn't used directly for truncation toward zero (Python floors toward −∞, while C truncates toward zero); why Energy is kept in integer pJ (same exactness argument as the firmware); why floats are acceptable here (the no-float rule is for firmware); why clock and sleep are injected (tests run instantly).
    - _Requirements: 13.2, 13.3_

  - [ ] 8.2 Write the Rated_Range property test for simulated values
    - **Property 18: Simulated values stay inside the Rated_Range**
    - **Validates: Requirements 13.2**
    - In `test_simulator.py`, for `t` in 0…10⁷ s, assert Bus_Voltage is in 0…6000 mV and a multiple of 4, and Current is in ±1,000,000 µA and a multiple of 100.
    - Comment the why: why the multiples matter (they match real hardware steps, so simulated data looks like real data).
    - _Requirements: 13.2, 15.5_

- [ ] 9. Implement the LinkMonitor
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [ ] 9.1 Implement `link_monitor.py` with example tests
    - Create `host/power_monitor/link_monitor.py` with `LinkStats`, `LinkMonitor.update(sample) -> list[str]` (the design's three steps, starting from flags 0), and `format_summary(stats, checksum_errors)`.
    - Create `host/tests/test_link_monitor.py`: a consecutive stream gives 0 drops; a gap of 3 counts 3; 65535 → 0 is not a drop; 65534 → 1 counts 1; seq 0 with a smaller Timestamp counts one reset and no drops; flag set/clear messages; the summary contains frames received, dropped frames, checksum errors, and device resets.
    - Comment the why: why the gap uses `% 65536` (modular arithmetic handles the wrap); why a reset needs both seq 0 and a smaller Timestamp; why "cleared" messages are printed even though Req 10.4 doesn't require them (they make Req 17.8 visible); the known 1-in-65,536 blind spot for a reset at seq 65535.
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [ ] 9.2 Write the drop and reset counting property test
    - **Property 14: Dropped Frames and resets are counted exactly**
    - **Validates: Requirements 10.1, 10.2**
    - In `test_link_monitor.py`, build runs of consecutive seq values (random start, increasing Timestamps, crossing 65535 → 0), separated by resets (the next run starts at seq 0 with a smaller Timestamp and the previous seq isn't 65535). Remove random Frames, never the first or last of a run. Assert the drop count and the reset count.
    - Comment the why: why the first and last Frames of a run are never removed (a loss at a run's edge looks the same as the reset itself); why the previous seq must not be 65535.
    - _Requirements: 10.1, 10.2, 15.5_

  - [ ] 9.3 Write the flag message property test
    - **Property 15: One message per flag state change**
    - **Validates: Requirements 10.4**
    - For a random sequence of `status_flags` values, assert that for each bit, the number of messages naming that flag equals the number of changes of that bit (starting from 0), with "set" for 0 → 1 and "cleared" for 1 → 0.
    - Comment the why: why counting per bit is the right model (flags change independently).
    - _Requirements: 10.4, 15.5_

- [ ] 10. Implement the CsvLogger
  - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ] 10.1 Implement `csv_logger.py` with its tests
    - Create `host/power_monitor/csv_logger.py` with `CSV_HEADER`, `CSV_FLUSH_PERIOD_S = 1.0`, and `CsvLogger` (`__init__(stream, clock)`, `open(path)` writes the header, `write(host_time_unix_s, sample)` with 3-decimal host time, `maybe_flush()` calls flush + `os.fsync` when at least 1 s has passed, `close()` flushes, fsyncs, and is safe to call twice).
    - Create `host/tests/test_csv_logger.py` using real files in `tmp_path` and a fake clock: the header matches `CSV_HEADER` and every measurement column ends in its unit suffix; one row per write with the right values; no flush before 1 s, one flush at 1 s; `os.fsync` is called (monkeypatched counter); `close()` twice doesn't raise and the file contains every row.
    - Comment the why: why `fsync` in addition to `flush` (`flush` only reaches the OS buffer, `fsync` reaches the disk); why `maybe_flush` is called every loop and not only after a write; why integers are written without unit conversion (no precision is lost).
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [ ] 10.2 Write the CSV round-trip property test
    - **Property 16: CSV rows round-trip**
    - **Validates: Requirements 11.2**
    - Using `valid_samples()` and a random host time, write one row, read it back with `csv.reader`, and assert the seven integer fields match and the host time is within 1 ms.
    - Comment the why: why the tolerance is 1 ms (the column has 3 decimals).
    - _Requirements: 11.2, 15.5_

- [ ] 11. Implement the CLI and wire the pipeline
  - _Requirements: 8.1, 10.3, 10.4, 11.3, 11.4, 13.1, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [ ] 11.1 Implement `build_arg_parser` and `run_session` with their tests
    - Create `host/power_monitor/cli.py` with the `ByteSource` Protocol, a `PlotSink` Protocol (`add(t_s, sample)`, `maybe_refresh(now_s)`), and `build_arg_parser()`: a required mutually exclusive group `--port`/`--simulate`, `--baud` (default 115200), `--csv` (default `./power_log_YYYYmmdd_HHMMSS.csv`), `--no-plot`.
    - Implement `run_session(source, parser, monitor, logger, plot, wall_clock) -> int`: read, feed, and for each Sample update the monitor (print its messages), write the CSV row, and add to the plot if present; then `logger.maybe_flush()` and `plot.maybe_refresh()` every loop. Return 0 on `KeyboardInterrupt` and 1 on `OSError`. A `finally` block closes the source and the CSV and prints the summary.
    - Create `host/tests/test_cli.py`: defaults and options parse; no arguments and both `--port` and `--simulate` each exit with status 2; a fake source that raises `KeyboardInterrupt` returns 0, closes the CSV, and prints all four counts; a fake source that raises `OSError` returns 1, closes the CSV, and prints the summary; flag warnings are printed; `maybe_flush` runs even when a read returns no bytes.
    - Comment the why: what a `typing.Protocol` is (structural typing: any object with those methods fits); why `PlotSink` is a Protocol here (`run_session` doesn't depend on `live_plot.py`, which comes in 12.1, and tests can pass a fake); why cleanup is in `finally`; why catching `OSError` also catches pyserial's `SerialException` (it subclasses `OSError`, so `cli` needn't import `serial` here); the meaning of exit codes 0, 1, and 2.
    - _Requirements: 10.3, 10.4, 11.3, 11.4, 13.1, 13.5, 13.7_

  - [ ] 11.2 Implement `main`, `SerialSource`, and `__main__.py` with their tests
    - In `cli.py`, add `SerialSource` (imports `serial` only when constructed; opens 8N1 at the given baud with a 50 ms timeout; `read()` returns the available bytes; `close()`). Add `main(argv) -> int`, which builds the source (`SimulatedSource` for `--simulate`, `SerialSource` for `--port`), `FrameParser`, `LinkMonitor`, and `CsvLogger`, and calls `run_session` with `plot=None` for now. On a port open failure it prints `error: cannot open serial port <PORT>: <reason>` and returns 1; on an unwritable CSV path it prints `error: cannot open CSV file <PATH>: <reason>` and returns 1.
    - Create `host/power_monitor/__main__.py` containing `raise SystemExit(main())`.
    - Extend `test_cli.py`: `--simulate` builds a `SimulatedSource` and `serial` is never imported (check `sys.modules`); a fake `serial` module whose `Serial()` raises makes `main` return 1 with the port name in stderr; an unwritable CSV path returns 1; `--simulate` feeds the same `FrameParser` class used for serial input (monkeypatch `run_session` to capture its arguments).
    - After this sub-task, `python -m power_monitor --simulate --no-plot` runs end to end. No plot window appears yet with or without `--no-plot`; that comes in 12.3.
    - Comment the why: why pyserial is imported lazily (`--simulate` must work without it and without hardware); why the timeout is 50 ms (it keeps the loop responsive for CSV flushes and plot refreshes without busy-waiting); why macOS opening the port resets the Uno (DTR) and how LinkMonitor handles that.
    - _Requirements: 8.1, 13.1, 13.3, 13.4, 13.5, 13.6_

- [ ] 12. Implement the Live_Plot and wire it in
  - _Requirements: 12.1, 12.2, 12.3_

  - [ ] 12.1 Implement `live_plot.py` with its tests
    - Create `host/power_monitor/live_plot.py` with `PLOT_WINDOW_S = 60.0`, `PLOT_REFRESH_PERIOD_S = 0.5`, a pure `PlotBuffer` (`add(t_s, sample)` drops points older than 60 s; `series()` returns t, V, mA, mW), and `LivePlot(buffer)`. `LivePlot` imports matplotlib inside `__init__`, creates three stacked subplots sharing the x-axis, and has `add(t_s, sample)` (forwards to the buffer, so it fits `PlotSink`) and `maybe_refresh(now_s)` (every 0.5 s: `set_data()`, rescale, x-limits to the last 60 s, `plt.pause(0.001)`). If the window is closed, plotting stops without raising.
    - Create `host/tests/test_live_plot.py` using the `Agg` backend and a monkeypatched `plt.pause` counter: `PlotBuffer` drops old points; refresh happens at 0 s, not at 0.2 s, and again at 0.5 s; a closed figure stops refreshes without an exception.
    - Comment the why: why matplotlib is imported lazily (`--no-plot` and tests shouldn't load a GUI toolkit); why `set_data()` on existing lines instead of redrawing (faster); what `plt.pause` does (lets the GUI event loop run); why host time is on the x-axis and not device Timestamps (a device reset doesn't scramble the axis); why the loop is single-threaded (the macOS backend needs the main thread).
    - _Requirements: 12.1, 12.2_

  - [ ] 12.2 Write the plot buffer property test
    - **Property 17: The plot buffer holds exactly the last 60 seconds**
    - **Validates: Requirements 12.1**
    - In `test_live_plot.py`, for random non-decreasing host times, assert `series()` holds exactly the points at or after latest − 60 s, in arrival order.
    - Comment the why: why the boundary is inclusive (a point exactly 60 s old is still "within the most recent 60 seconds").
    - _Requirements: 12.1, 15.5_

  - [ ] 12.3 Wire LivePlot into `main`
    - In `cli.py`, have `main` pass `LivePlot(PlotBuffer())` unless `--no-plot` is given, in which case it passes `None`.
    - Extend `test_cli.py`: `--no-plot` passes `plot=None` (captured via monkeypatched `run_session`); without it, a `LivePlot` is passed (Agg backend); a subprocess test runs `python -c "import power_monitor.cli, sys; assert 'matplotlib' not in sys.modules"`.
    - Comment the why: why the matplotlib check runs in a subprocess (the test process already imported matplotlib in other tests, so `sys.modules` there proves nothing).
    - _Requirements: 12.1, 12.3, 13.1_

- [ ] 13. Check type hints across the host package
  - _Requirements: 13.8_

  - [ ] 13.1 Implement `test_type_hints.py`
    - Create `host/tests/test_type_hints.py`, which parses every `host/power_monitor/*.py` with `ast` and asserts every function and method annotates all parameters (except `self`/`cls`) and its return type. It fails with the file, line, and function name, and fails if fewer files are found than expected.
    - Comment the why: why `ast` instead of importing and using `inspect` (no side effects such as importing matplotlib or pyserial); why an empty scan must fail.
    - _Requirements: 13.8_

- [ ] 14. Checkpoint: simulate mode works end to end
  - Ensure all tests pass, ask the user if questions arise.
  - `cd host && python -m pytest` is green. The user runs `python -m power_monitor --simulate` and confirms the plot shows V, mA, and mW over 60 s and the CSV grows; Ctrl+C prints the summary and exits 0 (`echo $?`); `python -m power_monitor --simulate --no-plot` runs without opening a window.
  - _Requirements: 10.3, 11.3, 11.4, 12.1, 12.2, 12.3, 13.2, 13.3, 13.4, 15.5_

### Slice 3: Firmware logic modules with Unity tests

- [ ] 15. Implement the Scaling_Module
  - _Requirements: 1.2, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 16.5_

  - [ ] 15.1 Implement `pm_scaling` with its Unity tests
    - Create `lib/pm_logic/src/pm_scaling.h/.c` with every constant from the design (`SHUNT_RESISTANCE_MOHM`, `SHUNT_ADC_LSB_UV`, `MAX_EXPECTED_CURRENT_UA`, `UA_PER_MA`, `INA219_CURRENT_REG_MAX_COUNT`, derived `CURRENT_LSB_UA`, `INA219_CAL_NUMERATOR_UA_MOHM`, `INA219_CAL_VALUE`, bus register constants, Rated_Range limits), the four `#error` checks, `pm_raw_reading_t`, and `pm_scale_bus_voltage_mv`, `pm_scale_current_ua` (explicit two's-complement decode), `pm_bus_math_overflow`, and `pm_is_overrange`.
    - Create `test/test_scaling/test_main.c`: `CURRENT_LSB_UA == 100`; `INA219_CAL_VALUE == 4096`; bus `0x0000` → 0 mV, `0x2710` → 5000 mV, `0xFFF8` → 32764 mV, and the CNVR and OVF bits don't change the voltage; OVF is detected; current `0x0000` → 0, `0x7FFF` → 3,276,700 µA, `0x8000` → −3,276,800 µA, `0xFFFF` → −100 µA; overrange boundaries 6000 mV in, 6004 out, ±1,000,000 µA in, ±1,000,100 out.
    - Comment the why: briefly explain fixed point and the calibration register formula in integer units; why `L` suffixes are needed (`int` is 16 bits on AVR); why `CURRENT_LSB_UA` is derived from the shunt ADC step (a finer LSB adds no resolution); what each `#error` protects against; why the explicit `raw >= 0x8000` decode instead of an `int16_t` cast (implementation-defined in C99); why boundary values count as in range.
    - _Requirements: 1.2, 2.6, 3.1, 3.2, 3.5, 15.3, 16.5_

  - [ ] 15.2 Add the exhaustive bus voltage scaling test (Unity, required)
    - **Property 1: Bus voltage scaling is exact**
    - **Validates: Requirements 3.1, 3.3**
    - In `test_scaling`, for all 65,536 register values `r`, assert `pm_scale_bus_voltage_mv(r) == (r >> 3) * 4` and `pm_bus_math_overflow(r) == ((r & 1) == 1)`.
    - Comment the why: why `uint32_t` is the loop variable; why exhaustive beats sampling for 16-bit inputs.
    - _Requirements: 3.1, 3.3_

  - [ ] 15.3 Add the exhaustive current scaling test (Unity, required)
    - **Property 2: Current scaling is exact and preserves sign**
    - **Validates: Requirements 3.2, 3.4**
    - In `test_scaling`, for all 65,536 register values `r`, assert `pm_scale_current_ua(r) == s * 100`, where `s = r >= 0x8000 ? (int32_t)r - 65536 : (int32_t)r`.
    - Comment the why: why the reference is written independently of the implementation (a test that reuses the code under test proves nothing).
    - _Requirements: 3.2, 3.4_

- [ ] 16. Implement the wrap-safe time helpers
  - _Requirements: 5.2, 6.4_

  - [ ] 16.1 Implement `pm_time` with its Unity tests
    - Create `lib/pm_logic/src/pm_time.h/.c` with `pm_elapsed_ms` and `pm_deadline_reached`.
    - Create `test/test_time/test_main.c`: elapsed 4,294,967,196 → 4 is 104 ms; elapsed with no wrap; `pm_deadline_reached(4294967290, 4)` is false; `(4, 4)` and `(5, 4)` are true; a deadline 2³¹ − 1 ms ahead is not reached.
    - Comment the why: why unsigned subtraction is wrap-safe (C defines it modulo 2³²); why `now >= deadline` breaks at the wrap (with an example); why the signed difference works and its 2³¹ ms limit; the implementation-defined cast and why it's acceptable for avr-gcc and clang; why these are `.c` functions and not `static inline` (ctypes needs exported symbols).
    - _Requirements: 5.2, 6.4, 15.3_

- [ ] 17. Implement the Power_Calculator
  - _Requirements: 4.1, 4.2, 4.3_

  - [ ] 17.1 Implement `pm_power` with its Unity tests
    - Create `lib/pm_logic/src/pm_power.h/.c` with `NW_PER_MW` and `pm_power_mw` (64-bit product, one division rounded half away from zero).
    - Create `test/test_power/test_main.c`: zero; Rated_Range corners (6000 mV × ±1,000,000 µA = ±6000 mW); GV1 5000 × 123,500 → 618; GV2 3700 × −1,045,000 → −3867; half-way cases in both signs; 4 mV × 374,900 µA = 1,499,600 nW → 1 mW (two-step rounding would give 2); extremes 65,535 mV × `INT32_MAX` and × `INT32_MIN` run under the overflow trap without crashing and give the exact expected value.
    - Comment the why: why mV × µA is nW; why one rounded division instead of two (the 1,499,600 nW example); how biasing by half in the sign's direction gives half-away-from-zero with C's truncating division; the overflow table (48-bit product in `int64_t`, result fits `int32_t`).
    - _Requirements: 4.1, 4.2, 4.3, 15.3_

- [ ] 18. Implement the Energy_Integrator
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 15.4_

  - [ ] 18.1 Implement exact energy integration with its Unity tests
    - Create `lib/pm_logic/src/pm_energy.h/.c` with `PJ_PER_UWH`, `ENERGY_REPORT_MAX_UWH`, `ENERGY_ACC_LIMIT_PJ`, `pm_energy_state_t`, `pm_energy_init`, `pm_energy_update` (right-endpoint rule, both Samples valid, `pm_elapsed_ms` for dt, exact `int64_t` pJ accumulation), `pm_energy_uwh` (truncated toward zero), and `pm_energy_is_saturated` (always false until 18.2).
    - Create `test/test_energy/test_main.c`: starts at 0 µWh; the first Sample only sets the baseline; 3600 mV × 1000 µA × 1000 ms adds exactly 1 µWh; a positive interval that isn't a whole µWh truncates down; a negative interval decreases Energy and truncates toward zero; Timestamp wrap 4,294,967,196 → 4 integrates 104 ms; an invalid Sample adds nothing and the next valid Sample only re-baselines; dt = 0 adds nothing; 300 h at 6 W (6000 mV × 1,000,000 µA, 1000 ms steps) gives exactly 1,800,000,000 µWh and is not saturated.
    - Comment the why: the pJ unit derivation (mV × µA × ms); why accumulating exact integers means rounding never builds up; the right-endpoint rule and why an interval touching an error Sample is skipped; why OVERRANGE Samples are still integrated; why truncation toward zero; the 300 h capacity table.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 5.7, 15.3, 15.4_

  - [ ] 18.2 Add saturation with check-before-multiply and its Unity tests
    - In `pm_energy.c`, add private `headroom_up()`/`headroom_down()` computed in `uint64_t`, the `mag > room / dt_ms` check before multiplying, clamping to ±`ENERGY_ACC_LIMIT_PJ`, and the latched `saturated` flag. Make `pm_energy_is_saturated` return it.
    - Extend `test_energy`: set `acc_pj` just below +limit (tests may write the struct directly) and exceed it → Energy is `INT32_MAX` µWh and saturated; later positive and negative updates change nothing; the negative mirror gives −2,147,483,647 µWh; a step landing exactly on the limit isn't saturated, and the next positive step is; extremes (65,535 mV, `INT32_MIN`/`INT32_MAX` µA, dt = `UINT32_MAX`) saturate without triggering the overflow trap.
    - Comment the why: why the check runs before the multiply (`mag × dt` can exceed `int64_t`); why `mag > floor(room / dt)` is exactly the overflow condition for non-negative integers; why headroom needs `uint64_t` (the distance can reach 2 × limit); why saturation latches instead of resuming (a clipped total is no longer true).
    - _Requirements: 5.5, 15.3, 15.4_

- [ ] 19. Implement the sampler
  - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 5.1, 5.5, 6.5, 7.5, 7.6_

  - [ ] 19.1 Implement `pm_sampler` with its Unity tests
    - Create `lib/pm_logic/src/pm_sampler.h/.c` with `pm_sensor_action_t`, `pm_sampler_t`, `pm_sampler_init`, `pm_sampler_next_action`, and `pm_sampler_finish` following the design's four-row rule table, then energy update, ENERGY_SATURATED, Timestamp, and seq.
    - Create `test/test_sampler/test_main.c`: the first Sample has seq 0; boot unconfigured → REINIT; the sequence READ fail → REINIT fail → REINIT OK (still SENSOR_ERROR, V = I = P = 0) → READ OK (flag clear, scaled values, Power) is followed step by step; every call yields one Sample carrying the given Timestamp; OVERRANGE from the OVF bit and from an over-range current; ENERGY_SATURATED set when the integrator is saturated (poke `energy.acc_pj`); seq wraps 65535 → 0.
    - Comment the why: why this logic lives in the library and not in `main.cpp` (testable without hardware); why a successful re-init still reports SENSOR_ERROR (the INA219 needs one conversion pair before its registers are valid); why SENSOR_ERROR and OVERRANGE are never set together; why a Sample is produced on every call (Req 2.3).
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 5.1, 5.5, 6.5, 7.5, 7.6, 15.3_

- [ ] 20. Checkpoint: firmware logic is green on the Mac
  - Ensure all tests pass, ask the user if questions arise.
  - `pio test -e native` runs `test_crc8`, `test_frame`, `test_scaling`, `test_time`, `test_power`, `test_energy`, and `test_sampler`, all green, with the overflow trap enabled.
  - _Requirements: 15.1, 15.2, 15.3, 15.4_

### Slice 4: Firmware on the Uno

- [ ] 21. Add compile-time configuration
  - _Requirements: 2.1, 6.1, 6.2, 6.3_

  - [ ] 21.1 Implement `pm_config.h` and `test_compile_time_checks.py`
    - Create `src/pm_config.h` as in the design: overridable `SAMPLE_RATE_HZ` (default 10) and `UART_BAUD_BPS`, named limits and unit constants, `SAMPLE_PERIOD_MS`, I2C settings, and the three `#error` checks.
    - Create `host/tests/test_compile_time_checks.py`, which runs `cc -fsyntax-only` from the repo root with `-I src -I lib/pm_logic/src` and `-D` overrides. The defaults pass, and a stdin snippet `#include "pm_config.h"` plus `_Static_assert(SAMPLE_PERIOD_MS == 100, ...)` compiles (`-std=c11 -x c -`). `SAMPLE_RATE_HZ=0` and `=51` fail with `SAMPLE_RATE_HZ` in stderr; `=1` and `=50` pass. `UART_BAUD_BPS=9600` with `SAMPLE_RATE_HZ=50` fails with the UART message. A `_Static_assert(I2C_TIMEOUT_US < US_PER_S / SAMPLE_RATE_MAX_HZ, ...)` snippet passes.
    - Comment the why: why `#if`/`#error` (works the same in C and C++ and names the constant); why the UART check multiplies both sides by 100 instead of dividing (integer-only preprocessor math); why `#ifndef` guards allow `-D` overrides; that `SAMPLE_PERIOD_MS` truncates for rates that don't divide 1000; why the 5 ms timeout is below the 20 ms period.
    - _Requirements: 2.1, 6.1, 6.2, 6.3, 16.5_

- [ ] 22. Implement the UART_Driver
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ] 22.1 Implement `uart_driver.h/.cpp`
    - Create `src/uart_driver.h` (no Arduino includes, `extern "C"` guards) and `src/uart_driver.cpp` with `uart_init()` (`Serial.begin(UART_BAUD_BPS, SERIAL_8N1)`, returns `PM_OK`) and `uart_write_frame()`: it returns `PM_ERR_UART_BUSY` without writing if `Serial.availableForWrite() < len`, and `PM_ERR_UART_WRITE` if `Serial.write()` accepts fewer bytes.
    - Run `pio run -e uno`: the driver compiles, but linking fails with undefined `setup`/`loop` until `main.cpp` exists in 24.1. Native and host tests stay green.
    - Comment the why: why the write is non-blocking (a blocked write would delay the next Sample); why 63 of 64 ring-buffer bytes are usable; why `uart_init` returns a status even with no failure path; why there's no `Serial.print` anywhere (only Frames go on the wire, and it saves about 1 KB of flash); the 117,647-baud note and why both ends match.
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [ ] 23. Implement the INA219_Driver
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1_

  - [ ] 23.1 Write the driver header and check the configuration value
    - Create `src/ina219_driver.h` with `INA219_I2C_ADDR`, the four register addresses, the configuration bit-field macros composing `INA219_CONFIG_VALUE`, and the three prototypes. It includes only `pm_status.h` and `pm_scaling.h` (no Arduino headers) and is valid C with `extern "C"` guards.
    - Extend `test/test_scaling/test_main.c` to `#include "../../src/ina219_driver.h"` and assert `INA219_CONFIG_VALUE == 0x119F` and `INA219_I2C_ADDR == 0x40`.
    - Comment the why: briefly explain the PGA and why /4 (fits 100 mV at 1 A with headroom, better offset than /8); why BRNG 16 V (smallest range covering 6 V; LSB is 4 mV either way); how each field lands at its bit position; why the header is Arduino-free (so a native test can check the value); why a relative include path (`platformio.ini` stays as designed, and `test_build_src = no` only stops `.cpp` compilation, not header includes).
    - _Requirements: 1.1, 1.2, 16.5_

  - [ ] 23.2 Implement the bus setup and register helpers
    - Create `src/ina219_driver.cpp` with `ina219_bus_begin()` (`Wire.begin()`, `Wire.setClock(I2C_CLOCK_HZ)`, `Wire.setWireTimeout(I2C_TIMEOUT_US, true)`) and private `write_reg16()`/`read_reg16()`. The helpers send and reassemble registers MSB first, clear the timeout flag before each transaction, and map Wire results to status codes per the design table.
    - Run `pio run -e uno`: compiles, with the link still expected to fail until 24.1.
    - Comment the why: why the INA219 register byte order (MSB first) is independent of the Frame byte order; why the timeout flag is cleared first (a stale flag would misclassify a later failure); why `reset_with_timeout = true`; that the Wire timeout is per wait loop, so a pathological transaction can take about 10 ms; each status mapping.
    - _Requirements: 1.4, 1.5, 2.1_

  - [ ] 23.3 Implement `ina219_configure` and `ina219_read`
    - In `ina219_driver.cpp`, `ina219_configure()` writes `INA219_CONFIG_VALUE` and `INA219_CAL_VALUE` and reads both back (`PM_ERR_SENSOR_VERIFY` on mismatch). `ina219_read()` reads the bus register, then the current register, into `pm_raw_reading_t`, returning at the first failure.
    - Run `pio run -e uno`: compiles, with the link still expected to fail until 24.1.
    - Comment the why: why the read-back (a device that ACKs but didn't keep the settings); why the driver stops at the first failure and never retries (bounds the worst-case Sample time; the re-init policy lives in `pm_sampler`).
    - _Requirements: 1.1, 1.3, 1.4, 2.1_

- [ ] 24. Implement the firmware entry point
  - _Requirements: 1.3, 2.3, 2.4, 6.4, 6.5, 7.6, 8.2, 8.3_

  - [ ] 24.1 Implement `main.cpp` and build the firmware
    - Create `src/main.cpp` with `setup()` and `loop()` as in the design: static `sampler`, `frame_buf`, `next_sample_ms`; `uart_init`, `ina219_bus_begin`, `ina219_configure`, `pm_sampler_init`; the first Sample one period after boot; the `pm_deadline_reached` fixed-rate schedule; READ or REINIT per `pm_sampler_next_action`; `pm_sampler_finish`, `pm_frame_encode`, `uart_write_frame`.
    - Run `pio run -e uno`: it builds and links with no warnings from project files. Note the RAM and flash lines for 26.1.
    - Comment the why: briefly explain the super-loop with a `millis()` schedule; why `next_sample_ms += SAMPLE_PERIOD_MS` instead of `now + period` (jitter doesn't accumulate, so the mean interval is exact); why no catch-up logic is needed (worst case ≈ 11 ms < 20 ms); why the first Sample waits one period; why `uart_write_frame`'s result is ignored (no other output channel, and the seq gap shows the host the loss); why `pm_sample_t` is a local (stack, not static RAM).
    - _Requirements: 1.3, 2.3, 2.4, 6.4, 6.5, 7.6, 8.2, 8.3, 16.3, 16.4_

- [ ] 25. Add firmware source-rule checks
  - _Requirements: 1.5, 8.3, 15.1, 16.3, 16.4_

  - [ ] 25.1 Implement `test_firmware_rules.py`
    - Create `host/tests/test_firmware_rules.py` with a comment-and-string-literal stripper and its own small tests (`//` comments, `/* */` block comments spanning lines, a `"float"` string literal, and code after a comment are handled correctly).
    - Checks on stripped `src/` and `lib/pm_logic/src/` sources: no `float` or `double`; no `malloc`, `calloc`, `realloc`, `free(`, `new`, or `delete` (word-boundary matches); no Arduino headers (`Arduino.h`, `Wire.h`, `HardwareSerial.h`) in `lib/pm_logic`; `<Wire.h>` only in `ina219_driver.cpp`; `Serial` only in `uart_driver.cpp`; no `print`/`println` anywhere. The test asserts the scan found the expected files (at least `main.cpp`, both drivers, and every `pm_*.c`).
    - Comment the why: why comments and strings are stripped first (a comment explaining "no float" must not fail the test); why word boundaries (`new` must not match `next_seq`); why an empty scan must fail; that the native build also fails on Arduino headers, so this check doubles as a readable error.
    - _Requirements: 1.5, 8.3, 15.1, 16.3, 16.4_

- [ ] 26. Start the verification report with measured resources
  - _Requirements: 16.1, 16.2_

  - [ ] 26.1 Create `docs/verification_report.md` and fill V6
    - Create `docs/verification_report.md` with headings for setup and equipment (DMM model and per-range accuracy, INA219 grade, shunt tolerance), V1–V6, and a summary. Leave V1–V5 as "not yet run".
    - Fill V6 from `pio run -e uno`: static RAM vs the 1024 B limit and flash vs the 16384 B limit with pass/fail, and a comparison with the design's estimates (≈ 430 B RAM, ≈ 7,450 B flash) that explains any large difference.
    - Explain in the report: what "static RAM" means (`.data` + `.bss`) and why the stack isn't included in that figure.
    - _Requirements: 16.1, 16.2_

- [ ] 27. Checkpoint: the Uno streams Frames with no sensor attached
  - Ensure all tests pass, ask the user if questions arise.
  - The user flashes with `pio run -e uno -t upload` and runs `python -m power_monitor --port <PORT> --no-plot` for about 1 minute with no INA219 connected. Expected: Frames at 10 Hz with SENSOR_ERROR set and zero values, one `warning: SENSOR_ERROR set` line, 0 dropped Frames, 0 checksum errors (one startup device reset is possible).
  - _Requirements: 2.2, 2.3, 2.4, 8.1, 8.2, 8.3, 10.3, 10.4_

### Slice 5: Fault-handling properties and verification report

The `ctypes`/hypothesis properties for the firmware logic (tasks 28–30) are optional and lowest priority. The Unity tests from slice 3 are the baseline and already cover the same modules with examples and boundaries.

- [ ] 28. Build the C logic library for Python tests
  - _Requirements: 15.1, 15.5_

  - [ ]* 28.1 Add the `ctypes` shared-library fixture to `conftest.py`
    - In `host/tests/conftest.py`, add a session fixture that compiles `lib/pm_logic/src/*.c` with `cc -std=c99 -O1 -Wall -Wextra -fsanitize=signed-integer-overflow -fsanitize-trap=signed-integer-overflow -shared -fPIC` into a temp `.dylib`, loads it with `ctypes.CDLL`, declares `argtypes`/`restype` for every function the properties use, and mirrors `pm_sample_t`, `pm_raw_reading_t`, `pm_energy_state_t`, and `pm_sampler_t` as `ctypes.Structure` classes.
    - Comment the why: briefly explain `ctypes`; why declaring `argtypes`/`restype` matters (ctypes otherwise assumes `int` and truncates 64-bit values); why the Structures match the C layout (same compiler); that an overflow trap kills the Python process (exit 133) and the Unity extremes pin it down readably; why this adds no dependency (`ctypes` is standard library and `cc` is already required).
    - _Requirements: 15.1, 15.5_

- [ ] 29. Fault-handling property tests (firmware logic via `ctypes`)
  - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 5.5, 6.5_

  - [ ]* 29.1 Write the sampler fault policy property test
    - **Property 8: The sampler follows the fault policy**
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 6.5**
    - Create `host/tests/test_fw_logic_properties.py`. For a random initial sensor state and a random sequence of (status code, raw registers), compare `pm_sampler` step by step with a small Python reference model of the design's state table: requested action, SENSOR_ERROR, zeroed values, READ after a successful REINIT, one Sample per step with the given Timestamp, and seq equal to the step index mod 65,536.
    - Comment the why: briefly explain model-based testing (a simple model written from the spec, compared with the real code).
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 6.5_

  - [ ]* 29.2 Write the OVERRANGE classification property test
    - **Property 3: OVERRANGE classification**
    - **Validates: Requirements 2.6**
    - In `test_fw_logic_properties.py`, for any bus and current registers read with `PM_OK`, assert OVERRANGE is set exactly when the OVF bit is 1, or V > 6000 mV, or |I| > 1,000,000 µA.
    - Comment the why: why generators are biased toward the 6000 mV and ±1,000,000 µA boundaries.
    - _Requirements: 2.6_

  - [ ]* 29.3 Write the energy saturation latch property test
    - **Property 6: Energy saturation is latched at the limit**
    - **Validates: Requirements 5.5**
    - In `test_fw_logic_properties.py`, for sequences whose exact total would pass ±`ENERGY_ACC_LIMIT_PJ` (start near the limit by setting `acc_pj`), assert that from the first passing step the accumulator equals the signed limit, `saturated` is set, and nothing changes afterward.
    - Comment the why: why the test starts near the limit (reaching 7.7 × 10¹⁸ pJ from zero would need millions of steps).
    - _Requirements: 5.5_

- [ ] 30. Remaining firmware-logic property tests (via `ctypes`)
  - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.7, 6.4, 7.1, 7.2, 7.3_

  - [ ]* 30.1 Write the power bound property test
    - **Property 4: Power is within half a milliwatt and never overflows**
    - **Validates: Requirements 4.1, 4.2, 4.3**
    - In `test_fw_logic_properties.py`, for `v` in 0…65,535 and `i` over the full `int32_t` range, assert `|p × 10⁶ − v × i| ≤ 5 × 10⁵` using Python's exact integers, and that the call returns (no trap).
    - Comment the why: why Python integers are a good oracle (arbitrary precision, no overflow).
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ]* 30.2 Write the exact energy model property test
    - **Property 5: Energy equals the exact model**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.7**
    - In `test_fw_logic_properties.py`, for a random start Timestamp (including within seconds of 2³²) and random (valid, V, I, time advance) steps whose exact total stays within the limit, assert `acc_pj` equals the Python sum of V × I × dt over consecutive valid pairs, and `pm_energy_uwh` equals that sum truncated toward zero.
    - Comment the why: why the model computes truncation with `abs` and sign instead of `//`.
    - _Requirements: 5.1, 5.2, 5.3, 5.7_

  - [ ]* 30.3 Write the time wrap property test
    - **Property 7: Time arithmetic is wrap-safe**
    - **Validates: Requirements 5.2, 6.4**
    - In `test_fw_logic_properties.py`, for `t` in 0…2³²−1 and `d` in 0…2³¹−1, assert `pm_elapsed_ms(t, (t+d) mod 2³²) == d` and `pm_deadline_reached((t+d) mod 2³², t)`; for `d` in 1…2³¹−1, assert `pm_deadline_reached(t, (t+d) mod 2³²)` is false.
    - Comment the why: why `d` stops at 2³¹ − 1 (the documented validity limit of the signed comparison).
    - _Requirements: 5.2, 6.4_

  - [ ]* 30.4 Write the C/Python encoder agreement property test
    - **Property 10: Firmware and host encoders agree byte for byte**
    - **Validates: Requirements 7.1, 7.2, 7.3**
    - In `test_fw_logic_properties.py`, for any Sample from `valid_samples()` (with `status_flags` restricted to bits 0–2, as firmware sends), fill the `ctypes` `pm_sample_t`, call `pm_frame_encode` into a 25-byte buffer, and assert the bytes equal `encode_frame(sample)`.
    - Comment the why: why two independent encoders agreeing is stronger than either alone (a shared misunderstanding would also have to be repeated in the Golden_Vectors).
    - _Requirements: 7.1, 7.2, 7.3_

- [ ] 31. Hardware verification and report [Needs INA219]
  - The user does the wiring and bench work. The agent computes results from the logged CSV with one-off commands (no new scripts in the repo) and writes `docs/verification_report.md`. A failing point is recorded and explained with the design's error budget, not fixed by tuning code.
  - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9_

  - [ ] 31.1 [Needs INA219] Wire the sensor, smoke test, and record equipment
    - The user wires the INA219 per the design's wiring table (high side, common ground) and runs `--port <PORT>` with an open-circuit load. Expected: SENSOR_ERROR clears, Bus_Voltage tracks the source, Current reads near 0.
    - Record in the report: DMM make and model with its datasheet accuracy for each range used, the INA219 grade (A or B), the shunt marking or tolerance, the Uno 5V-pin voltage, and the open-circuit current (offset diagnostic).
    - Explain in the report: why high-side placement and a common ground are required; why DMM accuracy is recorded (it consumes part of each tolerance).
    - _Requirements: 1.1, 1.3, 2.5, 17.9_

  - [ ] 31.2 [Needs INA219] V1: bus voltage accuracy
    - The user sets up points a–e from the design (1.0 V divider, 1–3 AA, highest source from 4.5 to 6.0 V) and reads the DMM at VIN−. The agent averages the 10 CSV rows spanning each DMM reading and computes error vs max(1%, 20 mV). The report records points in ascending order, which source was used for the top point, and pass/fail.
    - Explain in the report: why 10-Sample means; any failure, using the bus voltage error budget.
    - _Requirements: 17.1, 17.2, 17.9_

  - [ ] 31.3 [Needs INA219] V2: current accuracy
    - Before each point, the agent checks the 50%-of-rating rule (`V_R² / R ≤ 125 mW`) from the user's measured rail voltage and resistances. The user sets up points a–e (or the fallback) with the DMM in series. The agent computes error vs max(2%, 2 mA). The report records points in ascending order, the highest verified Current, and states that Current accuracy above it is unverified.
    - Explain in the report: the resistor power check; any failure, using the current error budget and the open-circuit offset.
    - _Requirements: 17.3, 17.4, 17.9_

  - [ ] 31.4 [Needs INA219] V3: energy over at least 10 minutes
    - The user follows the design's six V3 steps with the V2 point e load. The agent computes `E₁ − E₀` from the CSV and `E_ref` from the DMM readings and elapsed host time, and checks ±3%.
    - Explain in the report: why the jumper/ammeter swap barely moves VIN−; why the Uno's resonator error is part of what's measured; any failure, using the energy error budget.
    - _Requirements: 17.5_

  - [ ] 31.5 [Needs INA219] V4: link and timing for 10 minutes
    - The user runs the default firmware for 10 minutes. The agent records the summary (≈ 6000 Frames, 0 dropped, 0 checksum errors, startup reset noted) and checks from the CSV that the mean Timestamp interval over every 100-Frame window is 99–101 ms, plus the firmware-vs-host elapsed time drift as a diagnostic.
    - Explain in the report: why individual intervals jitter ±1 ms while the mean is exact.
    - _Requirements: 6.4, 8.1, 8.2, 8.3, 17.6_

  - [ ] 31.6 [Needs INA219] V5: sensor faults
    - The user performs the SDA disconnect, whole-breakout power loss, and SDA-to-GND stuck bus tests. The agent checks from the CSV: SENSOR_ERROR with zero values during the fault, recovery afterward, no seq gaps, no Timestamp gaps longer than one period, and Current matching the DMM again after power loss (calibration rewritten).
    - Optional direct timing check: the user rebuilds and flashes with `PLATFORMIO_BUILD_FLAGS="-DSAMPLE_RATE_HZ=1" pio run -e uno -t upload`, films the breadboard and terminal, and confirms the set/cleared lines appear within 2 s. Reflash the default build afterward.
    - Explain in the report: why reaction time is bounded by construction (one transaction per period, flag logic tested by Unity and Property 8); why shorting SDA to GND is safe (open-drain with pull-ups).
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 17.7, 17.8_

  - [ ] 31.7 [Needs INA219] Finish the verification report
    - Complete the summary table (V1–V6 pass/fail), attach or describe the setup photo, and for every failure name the error-budget term that explains it. Confirm every item listed under "Verification_Report contents" in the design is present.
    - Explain in the report: which results are limited by the DMM rather than the design.
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9_

- [ ] 32. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.
  - `pio test -e native` and `cd host && python -m pytest` are green (including any optional property tests that were done), `pio run -e uno` is within the RAM and flash limits, and `docs/verification_report.md` is complete.
  - _Requirements: 15.2, 15.5, 16.1, 16.2, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9_

## Notes

- Only sub-tasks 28.1, 29.1–29.3, and 30.1–30.4 are optional (`*`): the `ctypes` fixture and the firmware-logic hypothesis properties. Every Unity test (including the exhaustive Properties 1, 2, 9) and every host property (11–18) is required.
- Tasks 31.1–31.7 need the INA219 and bench work by the user.
- `pio run -e uno` first links in 24.1. Before that, "the build passes" means the native Unity suites and pytest.
- Sub-task 11.1 adds a small `PlotSink` Protocol so `run_session` can be written and tested before `live_plot.py` exists. `LivePlot.add()` (12.1) forwards to its `PlotBuffer` so it fits that Protocol.
- Packet format changes require updating `docs/packet_format.md`, the Unity Golden_Vectors, and the Python fixture together; the drift test (5.1) enforces this.
- To start, open this file and click "Start task" next to a sub-task.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "4.1"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["7.1", "8.1", "9.1", "10.1"] },
    { "id": 6, "tasks": ["7.2", "8.2", "9.2", "11.1"] },
    { "id": 7, "tasks": ["7.3", "9.3", "10.2", "11.2"] },
    { "id": 8, "tasks": ["7.4", "12.1"] },
    { "id": 9, "tasks": ["12.2", "12.3"] },
    { "id": 10, "tasks": ["13.1"] },
    { "id": 11, "tasks": ["15.1", "16.1", "17.1"] },
    { "id": 12, "tasks": ["15.2", "18.1"] },
    { "id": 13, "tasks": ["15.3", "18.2"] },
    { "id": 14, "tasks": ["19.1"] },
    { "id": 15, "tasks": ["21.1", "22.1", "23.1"] },
    { "id": 16, "tasks": ["23.2"] },
    { "id": 17, "tasks": ["23.3"] },
    { "id": 18, "tasks": ["24.1"] },
    { "id": 19, "tasks": ["25.1", "26.1"] },
    { "id": 20, "tasks": ["28.1"] },
    { "id": 21, "tasks": ["29.1"] },
    { "id": 22, "tasks": ["29.2"] },
    { "id": 23, "tasks": ["29.3"] },
    { "id": 24, "tasks": ["30.1"] },
    { "id": 25, "tasks": ["30.2"] },
    { "id": 26, "tasks": ["30.3"] },
    { "id": 27, "tasks": ["30.4"] },
    { "id": 28, "tasks": ["31.1"] },
    { "id": 29, "tasks": ["31.2"] },
    { "id": 30, "tasks": ["31.3"] },
    { "id": 31, "tasks": ["31.4"] },
    { "id": 32, "tasks": ["31.5"] },
    { "id": 33, "tasks": ["31.6"] },
    { "id": 34, "tasks": ["31.7"] }
  ]
}
```
