# Power Monitor Verification Report

This report records the hardware verification of the Power Monitor (Req 17) and the firmware resource measurements (Req 16.1, 16.2). The plan, pass criteria, and error budget are in the Verification Plan section of `.kiro/specs/power-monitor/design.md`.

Status: V6 complete. V1–V5 need the INA219 and have not been run.

## Setup and equipment

To be recorded before V1 (Req 17.9). Nothing below has been measured or chosen yet.

| Item | Value |
|---|---|
| DMM make and model | To be recorded before V1 |
| DMM accuracy, DC V range used | To be recorded before V1 (datasheet spec, per range) |
| DMM accuracy, DC mA range(s) used | To be recorded before V2 (datasheet spec, per range) |
| INA219 grade (A or B) | To be recorded before V1 (from the chip marking or the breakout's listing) |
| Shunt marking and tolerance | To be recorded before V1 |
| Uno 5V-pin voltage under each load | To be recorded during V1 and V2 |
| Open-circuit current reading (offset diagnostic) | To be recorded before V2 |
| Setup photo | To be added |

## V1: Bus voltage accuracy (Req 17.1, 17.2)

Not yet run.

## V2: Current accuracy (Req 17.3, 17.4)

Not yet run.

## V3: Energy over at least 10 minutes (Req 17.5)

Not yet run.

## V4: Link and timing for 10 minutes (Req 17.6, 6.4, 8.1–8.3)

Not yet run.

## V5: Sensor faults (Req 17.7, 17.8, 2.1, 2.3)

Not yet run.

## V6: Resources (Req 16.1, 16.2)

### Build

| Item | Value |
|---|---|
| Commit | `ddda036` (working tree clean apart from this report and spec bookkeeping; no firmware source changed) |
| Command | `pio run -e uno` |
| PlatformIO platform | Atmel AVR 5.3.0 (`platformio/atmelavr@5.3.0`) |
| Toolchain | toolchain-atmelavr 1.70300.191015, avr-gcc 7.3.0 |
| Arduino AVR core | 1.8.8 (`platform.txt`; PlatformIO package framework-arduino-avr 5.4.0) |
| Build flags of note | `-Os -flto -ffunction-sections -fdata-sections` (PlatformIO defaults) |
| `SAMPLE_RATE_HZ` | 10 (default) |

PlatformIO output, verbatim:

```text
RAM:   [==        ]  22.1% (used 452 bytes from 2048 bytes)
Flash: [==        ]  21.3% (used 6880 bytes from 32256 bytes)
```

### Result

| Resource | Measured | Limit | Use of limit | Result |
|---|---|---|---|---|
| Static RAM (`.data` + `.bss`) | 452 B | 1024 B | 44% | **PASS** |
| Flash (`.text` + `.data`) | 6,880 B | 16,384 B | 42% | **PASS** |

The 32,256 B in PlatformIO's flash line is the 32 KB flash minus the 512 B Optiboot bootloader. Req 16.2's limit is 16,384 B regardless.

### What "static RAM" means

Static RAM is `.data` (globals with an initial value, copied from flash into RAM at startup) plus `.bss` (globals that start at zero). Its size is fixed when the firmware is linked, which is why PlatformIO can report it and why Req 16.1 limits it. The stack is not in that figure. It grows down from the top of the 2048 B of RAM while the program runs, by whatever local variables and call frames are active, so its size is only known at run time.

RAM left for the stack: 2048 − 452 = **1,596 B**. The design's peak-stack figure (< 300 B, for `loop()` → `pm_sampler_finish()` → `pm_energy_update()` plus one interrupt frame) is an estimate from the call chain, not a measurement. Nothing in this report measures the stack.

### Evidence: section sizes

`avr-size -A .pio/build/uno/firmware.elf` (debug sections omitted; they are not loaded onto the chip):

| Section | Size (B) |
|---|---|
| `.data` | 42 |
| `.bss` | 410 |
| `.text` | 6,838 |

`.data` + `.bss` = 452 B, the RAM figure. `.text` + `.data` = 6,880 B, the flash figure: `.data`'s initial values are stored in flash and copied to RAM at startup, so they count in both.

### Evidence: RAM by contributor

From `avr-nm -S --size-sort -C .pio/build/uno/firmware.elf`, every `.data` and `.bss` symbol, grouped by owner. The groups sum to exactly 452 B.

| Contributor | Design estimate (B) | Measured (B) | Symbols |
|---|---|---|---|
| Arduino core | 20 | 9 | `timer0_millis` 4, `timer0_overflow_count` 4, `timer0_fract` 1 |
| `Serial` (HardwareSerial) | 157 | 175 | `Serial` object 157; `vtable for HardwareSerial` 18 (`.data`) |
| `Wire` (TwoWire) | 80 | 100 | `TwoWire::rxBuffer` 32, `TwoWire::txBuffer` 32; `Wire` object 12; six 1-byte statics 6; `vtable for TwoWire` 18 (`.data`) |
| `twi.c` | 120 | 116 | `twi_masterBuffer`, `twi_txBuffer`, `twi_rxBuffer` 32 each; `twi_timeout_us` 4; two slave callbacks 2 each; twelve 1-byte state variables 12 |
| Application | 50 | 52 | `frame_buf` 25, `sampler` 17, `next_sample_ms` 4; `CSWTCH.6` 6 (`.data`, see below) |
| **Total** | **≈ 430** | **452** | |

Differences, from that evidence:

- **Total: +22 B (5%).** Small. The two vtables (18 B each, in `.data`) explain more than all of it; the estimate didn't list them as separate items. The Arduino core came in 11 B under its estimate.
- **Serial: +18 B.** The object itself is exactly the estimated 157 B. The extra 18 B is its vtable, a table of virtual-function pointers that lives in `.data`, separate from the object.
- **Wire: +20 B.** The 64 B of buffers and the 6 B of indices match the estimate's description. The 12 B object and the 18 B vtable weren't counted, and the 80 B estimate didn't itemize the rest.
- **twi.c: −4 B.** Small. The three 32 B buffers (96 B) match the estimate. The remaining 20 B of state is 4 B less than estimated.
- **Arduino core: −11 B.** The estimate rounded up. Only the three Timer0 variables behind `millis()` are present.
- **Application: +2 B.** `frame_buf`, `sampler`, and `next_sample_ms` are exactly the 46 B the design listed. `CSWTCH.6` is a 6-byte lookup table that GCC generated for a `switch`. The disassembly shows it is addressed only from `write_reg16` and `read_reg16`, so it is the inlined `status_from_end_transmission()` switch over Wire codes 0–5, one byte per case. The design didn't anticipate it. `pm_sample_t` and `pm_raw_reading_t` don't appear as RAM symbols, as intended: they are locals in `loop()`.

### Flash compared with the estimate

Measured 6,880 B against an estimate of about 7,450 B: 570 B (8%) less. The build uses link-time optimization (`-flto`), which inlines across files. `setup()`, `loop()`, and most of the logic library are merged into one 2,032 B `main` function, so the symbol table can't split flash into the design's per-component rows. The one row that can be checked is the libgcc helpers. The 64-bit multiply, divide, compare, and shift routines, plus the AVR prologue/epilogue helpers, total 922 B against the estimated 1,000 B. The rest of the difference isn't attributed here.

## Summary

| Test | Requirement | Result |
|---|---|---|
| V1 Bus voltage accuracy | 17.1, 17.2 | Not yet run |
| V2 Current accuracy | 17.3, 17.4 | Not yet run |
| V3 Energy | 17.5 | Not yet run |
| V4 Link and timing | 17.6, 6.4 | Not yet run |
| V5 Sensor faults | 17.7, 17.8 | Not yet run |
| V6 Resources | 16.1, 16.2 | **PASS**: static RAM 452 / 1024 B, flash 6,880 / 16,384 B |
