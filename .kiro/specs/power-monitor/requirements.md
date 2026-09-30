# Requirements Document

## Introduction

The Power Monitor measures the voltage and current delivered to a small DC load (a single-cell Li-ion/LiPo battery or a USB-powered device), computes power and accumulated energy, and streams timestamped samples to a host computer. An Arduino Uno R3 reads an INA219 current/voltage sensor over I2C and sends fixed-length binary frames over UART at 115200 baud. A Python Host Tool on macOS parses the frames, logs samples to CSV, and plots them live.

The project targets a 2–3 weekend scope. Its engineering goals are:

- A clear hardware/software boundary: hardware drivers are thin, and all scaling, energy, and framing logic is unit tested on the Mac with no hardware attached.
- Integer-only (fixed-point) firmware math sized for a 2 KB RAM microcontroller.
- A documented, checksummed packet format tested on both sides of the link.
- A verification plan that compares measurements against a digital multimeter using stated tolerances.

The INA219 breakout has not been purchased yet, so the Host Tool includes a simulation mode, and the Firmware streams frames with a fault flag while no sensor is connected.

## Glossary

- **Firmware**: The C/C++ program running on the Arduino Uno R3 (ATmega328P), built with PlatformIO and the Arduino framework.
- **INA219_Driver**: The Firmware module that configures and reads the INA219 through the Arduino Wire library. This is the only Firmware module that performs I2C access.
- **UART_Driver**: The Firmware module that writes Frame bytes to the Arduino hardware serial port. This is the only Firmware module that performs UART access.
- **Logic_Modules**: The hardware-independent Firmware modules (Scaling_Module, Power_Calculator, Energy_Integrator, Frame_Encoder, CRC_Module). Logic_Modules do not include Arduino headers.
- **Scaling_Module**: The Logic_Module that converts raw INA219 register values into fixed-point engineering units.
- **Power_Calculator**: The Logic_Module that computes instantaneous power from Bus_Voltage and Current.
- **Energy_Integrator**: The Logic_Module that accumulates net energy over time.
- **Frame_Encoder**: The Logic_Module that serializes a Sample into a Frame.
- **CRC_Module**: The Firmware and Host implementations of the CRC-8 checksum defined in the Packet_Spec.
- **Host_Tool**: The Python 3 command-line program running on macOS that receives, parses, logs, and plots Samples.
- **Frame_Parser**: The Host_Tool component that extracts validated Frames from a raw byte stream.
- **Simulator**: The Host_Tool component that generates encoded Frames containing synthetic Sample data, used when the `--simulate` option is given.
- **CSV_Logger**: The Host_Tool component that writes decoded Samples to a CSV file.
- **Live_Plot**: The Host_Tool component that displays decoded Samples in a matplotlib window.
- **Native_Test_Environment**: The PlatformIO `native` environment that compiles Logic_Modules for macOS and runs the Unity unit tests.
- **Sample**: One measurement record: Timestamp, Sequence_Number, Status_Flags, Bus_Voltage, Current, Power, and Energy.
- **Frame**: The fixed-length binary encoding of one Sample: Sync_Bytes, a length byte, little-endian integer fields, and a CRC-8 checksum.
- **Packet_Spec**: The packet format document at `docs/packet_format.md` that defines the Frame layout byte by byte, the CRC-8 parameters, and the Golden_Vectors.
- **Golden_Vector**: A documented pair of (Sample field values, exact Frame bytes) used as a shared test fixture by both the Firmware tests and the Host_Tool tests.
- **Sync_Bytes**: The fixed two-byte pattern that marks the start of a Frame.
- **Sequence_Number**: An unsigned 16-bit counter in each Frame that increments by one per Frame and wraps from 65535 to 0.
- **Timestamp**: The unsigned 32-bit count of milliseconds since Firmware start at the moment a Sample is taken. The Timestamp wraps to 0 after 2^32 ms (about 49.7 days).
- **Status_Flags**: A bit field in each Frame. Defined flags: SENSOR_ERROR (the INA219 read or initialization failed), OVERRANGE (the INA219 reported a math overflow or the reading exceeded the Rated_Range), ENERGY_SATURATED (the Energy accumulator reached a representable limit).
- **Bus_Voltage**: The voltage at the load side of the shunt resistor (INA219 VIN−) relative to ground, in millivolts.
- **Current**: The signed current through the shunt resistor, positive when flowing from the source to the load, expressed in an integer unit of 100 µA or finer.
- **Power**: The signed product of Bus_Voltage and Current, in milliwatts.
- **Energy**: The signed net energy delivered to the load since Firmware start, expressed in an integer unit of 1 mWh or finer.
- **Rated_Range**: Bus_Voltage from 0 V to 6 V and Current from −1 A to +1 A, with a 0.1 Ω shunt resistor.
- **Sample_Period**: The time between consecutive Samples, set at compile time by one named constant. The default rate is 10 Hz (100 ms).
- **DMM**: The digital multimeter used as the reference instrument during accuracy verification.
- **Verification_Report**: A markdown document in the repository that records the accuracy test setup, DMM model and accuracy specification, raw readings, errors, and pass/fail results.

## Requirements

### Requirement 1: Sensor Configuration and Acquisition

**User Story:** As a firmware developer, I want the Firmware to configure and read the INA219 with its own register-level driver, so that I control every scaling decision and avoid a floating-point library.

#### Acceptance Criteria

1. WHEN the Firmware starts, THE INA219_Driver SHALL configure the INA219 bus voltage range, shunt voltage gain, ADC resolution, and calibration register so that the Rated_Range is measurable without sensor overflow.
2. THE INA219_Driver SHALL be configured for a Bus_Voltage resolution of 4 mV and a Current resolution of 100 µA or finer.
3. WHEN a Sample_Period elapses, THE INA219_Driver SHALL read the INA219 bus voltage register and current register.
4. THE INA219_Driver SHALL return a status code from every function that performs I2C access.
5. THE INA219_Driver SHALL access the INA219 using only the Arduino Wire library.

### Requirement 2: Sensor Fault Handling

**User Story:** As a test engineer, I want sensor faults reported in every Frame instead of silently producing bad numbers, so that I can trust logged data and diagnose wiring problems.

#### Acceptance Criteria

1. IF an I2C transaction with the INA219 does not complete within 5 ms, THEN THE INA219_Driver SHALL abort the transaction and return a timeout status code. The timeout SHALL be shorter than the Sample_Period at the 50 Hz maximum sample rate (20 ms).
2. IF the INA219_Driver returns a non-success status code during a Sample, THEN THE Firmware SHALL set the SENSOR_ERROR flag in that Sample and set Bus_Voltage, Current, and Power to zero in that Sample.
3. WHILE the SENSOR_ERROR flag is set, THE Firmware SHALL continue to transmit one Frame per Sample_Period.
4. WHILE the SENSOR_ERROR flag is set, THE Firmware SHALL attempt to reinitialize the INA219 once per Sample_Period.
5. WHEN INA219 reinitialization succeeds, THE Firmware SHALL clear the SENSOR_ERROR flag starting with the next Sample.
6. IF the INA219 reports a math overflow, or the scaled Bus_Voltage or Current lies outside the Rated_Range, THEN THE Firmware SHALL set the OVERRANGE flag in that Sample.

### Requirement 3: Fixed-Point Scaling

**User Story:** As a firmware developer, I want raw register values converted to engineering units with integer math, so that the conversion is exact, fast on an 8-bit MCU, and testable on the Mac.

#### Acceptance Criteria

1. THE Scaling_Module SHALL convert the raw INA219 bus voltage register value into Bus_Voltage in millivolts using integer arithmetic.
2. THE Scaling_Module SHALL convert the raw INA219 current register value into Current using integer arithmetic.
3. FOR ALL raw bus voltage register values, THE Scaling_Module SHALL produce the Bus_Voltage equal to the INA219 datasheet conversion (bus voltage bits multiplied by 4 mV).
4. FOR ALL raw current register values, THE Scaling_Module SHALL produce the Current equal to the signed register value multiplied by the configured current LSB, with the sign preserved.
5. THE Scaling_Module SHALL derive the calibration register value and current LSB from named constants for shunt resistance and maximum expected current.

### Requirement 4: Power Computation

**User Story:** As a user, I want instantaneous power in each Sample, so that I can see load consumption without post-processing.

#### Acceptance Criteria

1. WHEN a Sample has valid Bus_Voltage and Current, THE Power_Calculator SHALL compute Power from Bus_Voltage and Current using integer arithmetic.
2. FOR ALL Bus_Voltage and Current values within the Rated_Range, THE Power_Calculator SHALL produce a Power within ±1 mW of the exact product of Bus_Voltage and Current.
3. FOR ALL Bus_Voltage and Current values within the representable input range, THE Power_Calculator SHALL compute Power without intermediate integer overflow.

### Requirement 5: Energy Integration

**User Story:** As a user measuring a battery or USB device, I want accumulated energy that stays correct over long runs, so that I can compare consumption across tests.

#### Acceptance Criteria

1. WHEN two consecutive Samples both have the SENSOR_ERROR flag clear, THE Energy_Integrator SHALL add the energy for the interval between those Samples, computed as Bus_Voltage × Current × elapsed time using the values of the later Sample.
2. THE Energy_Integrator SHALL compute elapsed time as the unsigned modular difference between consecutive Timestamps, so that elapsed time is correct across a Timestamp wrap.
3. FOR ALL sequences of valid Samples, THE Energy_Integrator SHALL report an Energy within ±1 Energy unit of the exact sum of per-interval energies (model-based property; no per-interval rounding accumulates).
4. THE Energy_Integrator SHALL represent at least 300 hours of continuous operation at 6 W without reaching a representable limit.
5. IF adding an interval's energy would exceed the representable Energy range, THEN THE Energy_Integrator SHALL hold Energy at the corresponding limit and set the ENERGY_SATURATED flag in every subsequent Sample.
6. WHEN the Firmware starts, THE Energy_Integrator SHALL initialize Energy to zero.
7. FOR ALL Samples with negative Current, THE Energy_Integrator SHALL decrease Energy, so that Energy reports net energy delivered to the load.

### Requirement 6: Sampling Rate and Timestamps

**User Story:** As a systems engineer, I want a compile-time sample rate and a timestamp on every Sample, so that timing is predictable and verifiable.

#### Acceptance Criteria

1. THE Firmware SHALL define the sample rate by one named compile-time constant with a default value of 10 Hz.
2. IF the configured sample rate is outside 1 Hz to 50 Hz, THEN THE Firmware build SHALL fail with a compile-time error message naming the constant.
3. IF the configured Frame length, sample rate, and UART baud rate require more than 50% of the UART bit rate (10 bits per byte), THEN THE Firmware build SHALL fail with a compile-time error message.
4. WHILE the Firmware is running, THE Firmware SHALL take one Sample per Sample_Period with the mean interval between Timestamps over 100 consecutive Frames within ±1% of the Sample_Period.
5. WHEN a Sample is taken, THE Firmware SHALL record the Timestamp in that Sample.

### Requirement 7: Frame Encoding

**User Story:** As a firmware developer, I want a fixed-length binary Frame with sync bytes, a sequence number, and a checksum, so that the host can detect corruption and dropped Frames with minimal bandwidth.

#### Acceptance Criteria

1. THE Frame_Encoder SHALL encode each Sample as a Frame containing, in the order defined by the Packet_Spec: Sync_Bytes, a length byte, Sequence_Number, Timestamp, Status_Flags, Bus_Voltage, Current, Power, Energy, and a CRC-8 checksum.
2. THE Frame_Encoder SHALL encode all multi-byte fields as little-endian integers.
3. THE Frame_Encoder SHALL compute the CRC-8 checksum over every byte from the length byte through the last data field, excluding Sync_Bytes.
4. THE Frame_Encoder SHALL write the Frame into a caller-provided fixed-size buffer.
5. WHEN a Frame is encoded, THE Firmware SHALL increment the Sequence_Number by one, wrapping from 65535 to 0.
6. WHEN the Firmware starts, THE Firmware SHALL set the Sequence_Number of the first Frame to 0.
7. WHEN the Golden_Vector Sample field values are encoded, THE Frame_Encoder SHALL produce exactly the Golden_Vector Frame bytes.
8. WHEN the CRC_Module processes the ASCII string "123456789", THE CRC_Module SHALL produce the standard check value for the CRC-8 variant named in the Packet_Spec.

### Requirement 8: UART Streaming

**User Story:** As a user, I want Frames streamed continuously over USB serial, so that the host receives data as soon as the board is powered.

#### Acceptance Criteria

1. THE UART_Driver SHALL transmit Frames at 115200 baud, 8 data bits, no parity, 1 stop bit.
2. WHEN a Frame is encoded, THE UART_Driver SHALL transmit the Frame before the next Sample_Period elapses.
3. THE Firmware SHALL transmit only Frames on the UART, with no text or debug output mixed into the stream.
4. THE UART_Driver SHALL return a status code from every function that writes to the UART.

### Requirement 9: Host Frame Parsing and Resynchronization

**User Story:** As a test engineer, I want the host to recover from corrupted or partial Frames, so that one bad byte does not end a logging session.

#### Acceptance Criteria

1. WHEN a byte sequence matching Sync_Bytes, the expected length byte, and a valid CRC-8 checksum is received, THE Frame_Parser SHALL decode the Frame into a Sample.
2. IF the length byte does not match the Packet_Spec Frame length, or the CRC-8 checksum does not match, THEN THE Frame_Parser SHALL discard the first Sync_Bytes byte of the candidate Frame, increment a checksum-error counter, and resume searching for Sync_Bytes at the next byte.
3. WHEN bytes that do not begin with Sync_Bytes are received outside a Frame, THE Frame_Parser SHALL discard those bytes.
4. WHEN a Frame arrives split across multiple reads, THE Frame_Parser SHALL buffer the partial Frame and decode the Frame once all bytes are received.
5. FOR ALL valid Samples, THE Host_Tool Frame encoder followed by the Frame_Parser SHALL return a Sample equal to the original (round-trip property).
6. FOR ALL valid Frames and ALL single-bit errors in the length byte, data fields, or checksum, THE Frame_Parser SHALL reject the corrupted Frame (error-detection property).
7. FOR ALL streams of valid Frames with garbage bytes inserted between Frames, where the garbage contains no Sync_Bytes first byte, THE Frame_Parser SHALL emit every valid Frame in order (resynchronization property).
8. WHEN the Golden_Vector Frame bytes are parsed, THE Frame_Parser SHALL produce exactly the Golden_Vector Sample field values.

### Requirement 10: Host Link Monitoring

**User Story:** As a test engineer, I want dropped Frames and device resets detected and counted, so that I can quantify link reliability.

#### Acceptance Criteria

1. WHEN a decoded Sequence_Number differs from the previous Sequence_Number plus one (modulo 65536), THE Host_Tool SHALL add the number of missing Frames to a dropped-frame counter.
2. IF a decoded Sequence_Number is 0 and the decoded Timestamp is less than the previous Timestamp, THEN THE Host_Tool SHALL record a device-reset event and exclude that gap from the dropped-frame counter.
3. WHEN the Host_Tool exits, THE Host_Tool SHALL print the total Frames received, dropped-frame count, checksum-error count, and device-reset count.
4. WHEN a decoded Sample has any Status_Flags bit set, THE Host_Tool SHALL print a warning naming each set flag, at most once per flag state change.

### Requirement 11: CSV Logging

**User Story:** As a user, I want every Sample written to a CSV file, so that I can analyze runs in a spreadsheet or script later.

#### Acceptance Criteria

1. WHEN the CSV_Logger opens a log file, THE CSV_Logger SHALL write a header row whose column names include the unit of each quantity.
2. WHEN a Sample is decoded, THE CSV_Logger SHALL append one row containing host receive time, Timestamp, Sequence_Number, Status_Flags, Bus_Voltage, Current, Power, and Energy.
3. WHILE logging, THE CSV_Logger SHALL flush buffered rows to disk at least once per second.
4. WHEN the Host_Tool exits, including exit by keyboard interrupt, THE CSV_Logger SHALL flush and close the log file.

### Requirement 12: Live Plot

**User Story:** As a user, I want a live plot of voltage, current, and power, so that I can watch a load's behavior during a test.

#### Acceptance Criteria

1. WHILE the Host_Tool is receiving Samples, THE Live_Plot SHALL display Bus_Voltage, Current, and Power against time for the most recent 60 seconds.
2. WHILE the Host_Tool is receiving Samples, THE Live_Plot SHALL refresh at least once per second.
3. WHERE the `--no-plot` option is given, THE Host_Tool SHALL run without opening a plot window.

### Requirement 13: Host CLI and Simulation Mode

**User Story:** As a developer without the sensor yet, I want a simulate mode that exercises the full host pipeline, so that I can build and test everything before hardware arrives.

#### Acceptance Criteria

1. THE Host_Tool SHALL provide an argparse command-line interface with options for serial port, baud rate (default 115200), CSV output path, `--simulate`, and `--no-plot`.
2. WHERE the `--simulate` option is given, THE Simulator SHALL generate Samples with time-varying Bus_Voltage and Current within the Rated_Range at the default sample rate.
3. WHERE the `--simulate` option is given, THE Simulator SHALL encode each Sample into Frame bytes and pass the bytes through the same Frame_Parser used for serial input.
4. WHERE the `--simulate` option is given, THE Host_Tool SHALL run without opening a serial port.
5. IF neither a serial port nor `--simulate` is given, THEN THE Host_Tool SHALL print a usage error and exit with a non-zero status.
6. IF the serial port cannot be opened, THEN THE Host_Tool SHALL print an error naming the port and exit with a non-zero status.
7. IF the serial port disconnects during a run, THEN THE Host_Tool SHALL flush the CSV file, print the link summary, and exit with a non-zero status.
8. THE Host_Tool SHALL use type hints on all functions.

### Requirement 14: Packet Format Specification

**User Story:** As a systems engineer, I want one authoritative packet definition shared by both sides of the link, so that firmware and host cannot silently disagree.

#### Acceptance Criteria

1. THE Packet_Spec SHALL define, for every Frame byte, the field name, offset, size, signedness, unit, and byte order.
2. THE Packet_Spec SHALL define the Sync_Bytes values, the Frame length, the CRC-8 polynomial, initial value, and check value.
3. THE Packet_Spec SHALL define at least two Golden_Vectors, including one with negative Current and non-zero Status_Flags, and at least one in which every multi-byte field has a least-significant byte that differs from its most-significant byte, so that a byte-order error changes the encoded Frame bytes.
4. THE Firmware tests and the Host_Tool tests SHALL both verify the Golden_Vectors defined in the Packet_Spec.

### Requirement 15: Hardware/Logic Separation and Native Tests

**User Story:** As a firmware developer, I want all logic unit tested on my Mac, so that I can verify correctness before and without hardware.

#### Acceptance Criteria

1. THE Logic_Modules SHALL compile without the Arduino framework headers.
2. THE Native_Test_Environment SHALL build the Logic_Modules and run the Unity unit tests on macOS with the command `pio test -e native`.
3. THE Native_Test_Environment SHALL include unit tests for the Scaling_Module, Power_Calculator, Energy_Integrator, Frame_Encoder, and CRC_Module.
4. THE Energy_Integrator tests SHALL include a Timestamp wrap case and an ENERGY_SATURATED case.
5. THE Host_Tool SHALL include pytest tests, with hypothesis property tests for Requirements 9.5, 9.6, and 9.7, runnable on macOS with no hardware attached.

### Requirement 16: Firmware Resource and Coding Constraints

**User Story:** As an embedded developer, I want explicit memory and arithmetic limits, so that the Firmware fits the ATmega328P with margin for future work.

#### Acceptance Criteria

1. THE Firmware SHALL use no more than 1024 bytes of static RAM (50% of 2 KB), as reported by the PlatformIO build size output.
2. THE Firmware SHALL use no more than 16384 bytes of flash (50% of 32 KB), as reported by the PlatformIO build size output.
3. THE Firmware SHALL allocate all memory statically, with no calls to malloc, free, new, or delete.
4. THE Firmware SHALL use integer arithmetic only, with no float or double types in Firmware source.
5. THE Firmware SHALL express every numeric constant with physical meaning as a named constant whose name includes the unit.

### Requirement 17: Accuracy and System Verification

**User Story:** As a test/validation engineer, I want measured accuracy compared against a multimeter with stated tolerances, so that I can show the monitor meets a defined specification.

#### Acceptance Criteria

1. WHEN Bus_Voltage is verified, THE Verification_Report SHALL record at least 5 test points in ascending order, including one point within 1.0 V ± 0.1 V and one point at the highest-voltage source available from 4.5 V to 6.0 V inclusive (for example, USB 5 V or a 3×/4×AA battery pack measured by the DMM to be at most 6.0 V). Each test point SHALL compare the mean of 10 consecutive Bus_Voltage values against a DMM reading taken at the INA219 VIN− terminal during those same 10 Samples. THE Verification_Report SHALL record which source was used for the upper test point.
2. THE Firmware SHALL report Bus_Voltage within ±1% of the DMM reading or ±20 mV, whichever is larger, at every voltage test point.
3. WHEN Current is verified, THE Verification_Report SHALL record at least 5 test points in ascending order, including one point within 10 mA to 15 mA and one point within 100 mA to 200 mA, or, if kit parts cannot safely produce 100 mA, the highest current the kit parts can produce with every resistor dissipating no more than 50% of its power rating, so that every test point stays inside the Rated_Range. Each test point SHALL compare the mean of 10 consecutive Current values against a DMM reading in series with the load, taken during those same 10 Samples. THE Verification_Report SHALL record the highest verified Current and SHALL state that Current accuracy above that value is unverified.
4. THE Firmware SHALL report Current within ±2% of the DMM reading or ±2 mA, whichever is larger, at every current test point.
5. WHEN energy is verified with a constant load for at least 10 minutes, THE Firmware SHALL report an Energy change within ±3% of the DMM-derived Bus_Voltage × Current × elapsed time.
6. WHEN the link is verified at the default sample rate for 10 minutes, THE Host_Tool SHALL report zero dropped Frames and zero checksum errors.
7. WHEN the INA219 is disconnected during streaming, THE Firmware SHALL transmit Frames with the SENSOR_ERROR flag set within 2 Sample_Periods.
8. WHEN the INA219 is reconnected during streaming, THE Firmware SHALL transmit Frames with the SENSOR_ERROR flag clear within 2 Sample_Periods.
9. THE Verification_Report SHALL record the DMM model and its datasheet accuracy for each range used, since DMM uncertainty consumes part of each tolerance.

## Future Work

Out of scope for this spec. Listed so the boundary is explicit.

- Host-to-MCU commands (reset energy, change sample rate, request status).
- Runtime-configurable sample rate and INA219 averaging.
- Persisting Energy across resets (EEPROM).
- SD card logging on the device.
- Alerts and thresholds (over-current, under-voltage cut-off).
- Battery state-of-charge and capacity (coulomb counting, voltage curves).
- Two-point or temperature calibration with stored calibration constants.
- Watchdog timer and I2C bus-recovery sequence.
- Simulator fault injection (random corruption and drops at a configurable rate).
- Host Timestamp unwrapping for runs longer than 49.7 days.
- Multiple sensors or channels.
- Custom PCB and enclosure.
