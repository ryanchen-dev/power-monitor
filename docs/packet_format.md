# Power Monitor Packet Format

This document is the authoritative Packet_Spec (Req 14). Any change to it requires updating the Golden_Vectors, the Python fixture, and the Unity test in the same change; a host test fails if the three disagree.

## Layout (25 bytes)

| Offset | Size | Field | Signed | Unit | Byte order | Notes |
|---|---|---|---|---|---|---|
| 0 | 1 | `sync0` | no | – | n/a | `0xAA` |
| 1 | 1 | `sync1` | no | – | n/a | `0x55` |
| 2 | 1 | `length` | no | bytes | n/a | `25` (`0x19`): total Frame length including sync and CRC |
| 3 | 2 | `seq` | no | count | little-endian | +1 per Frame, 65535 → 0; first Frame after reset is 0 |
| 5 | 4 | `timestamp_ms` | no | ms | little-endian | `millis()` at the Sample; wraps after 2³² ms |
| 9 | 1 | `status_flags` | no | bit field | n/a | see flag table |
| 10 | 2 | `bus_voltage_mv` | no | mV | little-endian | multiple of 4 from hardware |
| 12 | 4 | `current_ua` | yes (two's complement) | µA | little-endian | + = source to load; multiple of 100 from hardware |
| 16 | 4 | `power_mw` | yes (two's complement) | mW | little-endian | V × I rounded half away from zero |
| 20 | 4 | `energy_uwh` | yes (two's complement) | µWh | little-endian | net energy, truncated toward zero, ±2,147,483,647 |
| 24 | 1 | `crc8` | no | – | n/a | CRC-8/SMBUS over bytes 2–23 (22 bytes) |

> **Concept: little-endian.** A multi-byte integer is sent least significant byte first. 5000 = `0x1388` is sent as `88 13`. Both the ATmega328P and the Mac are little-endian, but the format is defined by explicit shifts, not by the CPU, so it would stay correct on any machine.

**Length byte semantics.** `length` is the total Frame length (25), not the payload length. The parser checks it before waiting for the rest of the Frame, so a false sync match inside data (`AA 55` appearing in a field) is usually rejected after 3 bytes instead of 25. It also acts as a crude format marker: a future Frame of a different length is rejected instead of misparsed. Total length was chosen over payload length because it is the number the parser actually needs (bytes to buffer), with no arithmetic.

## Status_Flags

| Bit | Mask | Name | Set when |
|---|---|---|---|
| 0 | `0x01` | SENSOR_ERROR | The driver returned non-OK this Sample, or the sensor is being re-initialized. V, I, P are 0. |
| 1 | `0x02` | OVERRANGE | INA219 OVF bit set, or V > 6000 mV, or \|I\| > 1,000,000 µA. |
| 2 | `0x04` | ENERGY_SATURATED | The energy accumulator reached its limit (latched until firmware restart). |
| 3–7 | `0xF8` | reserved | Always 0 from firmware. The host names them `RESERVED_BITn` if seen. |

SENSOR_ERROR and OVERRANGE are never set together, because error Samples carry zero values.

## CRC-8 parameters

| Parameter | Value |
|---|---|
| Name | CRC-8/SMBUS (reveng catalogue) |
| Width | 8 |
| Polynomial | `0x07` (x⁸ + x² + x + 1) |
| Initial value | `0x00` |
| Reflect input / output | false / false |
| XOR out | `0x00` |
| Check value (`"123456789"`) | `0xF4` |
| Coverage | Frame bytes 2 through 23 (length byte through `energy_uwh`); sync bytes excluded |

## Golden_Vectors

Field values:

| Field | GV1 | GV2 | GV3 |
|---|---|---|---|
| `seq` | 4660 (`0x1234`) | 65535 (`0xFFFF`) | 0 |
| `timestamp_ms` | 123,456,789 (`0x075BCD15`) | 4,294,967,196 (`0xFFFFFF9C`) | 100 (`0x00000064`) |
| `status_flags` | `0x00` | `0x06` (OVERRANGE, ENERGY_SATURATED) | `0x01` (SENSOR_ERROR) |
| `bus_voltage_mv` | 5000 (`0x1388`) | 3700 (`0x0E74`) | 0 |
| `current_ua` | 123,500 (`0x0001E26C`) | −1,045,000 (`0xFFF00DF8`) | 0 |
| `power_mw` | 618 (`0x0000026A`) | −3867 (`0xFFFFF0E5`) | 0 |
| `energy_uwh` | 7,654,321 (`0x0074CBB1`) | −2,147,483,647 (`0x80000001`) | 0 |
| `crc8` | `0x77` | `0x6B` | `0x9E` |

Frame bytes (this block is parsed by the drift test; keep the format `GVn:` followed by 25 hex bytes):

```text
GV1: AA 55 19 34 12 15 CD 5B 07 00 88 13 6C E2 01 00 6A 02 00 00 B1 CB 74 00 77
GV2: AA 55 19 FF FF 9C FF FF FF 06 74 0E F8 0D F0 FF E5 F0 FF FF 01 00 00 80 6B
GV3: AA 55 19 00 00 64 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 9E
```

What each vector catches:

- **GV1** (normal Sample): every multi-byte field has a least-significant byte different from its most-significant byte, so any byte-order mistake changes the bytes (Req 14.3). Power 5000 mV × 123,500 µA = 617.5 mW exercises rounding up to 618.
- **GV2** (negative and extreme): negative Current and Power, two flags set, `seq` at its maximum, Timestamp 100 ms before the wrap, and Energy at the negative saturation limit. Sign-extension and signed/unsigned mix-ups show up here. Power 3700 mV × −1,045,000 µA = −3866.5 mW exercises rounding away from zero to −3867. The Current is outside the Rated_Range, consistent with OVERRANGE.
- **GV3** (no sensor): shaped like the Frames the firmware sends before the INA219 is connected, with all measurement fields zero.

The same bytes are hardcoded in `test/test_frame/test_main.c` and `host/tests/test_protocol.py`. A generated shared file (for example a script that emits a C header and a Python module) was not used: it adds a code-generation step to build and explain, and three 25-byte arrays are small. Instead, `host/tests/test_golden_vectors_sync.py` extracts the `GVn:` lines from this file (`docs/packet_format.md`), the byte arrays from the Unity test file, and the Python fixture, and fails if any differ (Req 14.4). The vectors were computed with a throwaway script and cross-checked with bitwise, table-driven, and polynomial-division CRC implementations, plus a decode round trip and a single-bit-flip rejection check over bytes 2–24.
