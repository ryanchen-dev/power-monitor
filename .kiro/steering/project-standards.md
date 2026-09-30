---
inclusion: always
---
# Project Standards

## About this project
Power/battery monitor: microcontroller + INA219 over I2C, UART packets
to a Python host tool. Portfolio project by a 1A EE student. Clarity and correctness matter more than cleverness.

## Architecture rules
- Separate hardware access (I2C, UART drivers) from logic (scaling,
  energy integration, packet framing). Logic must compile and be unit
  tested on the host machine with no hardware.
- No new dependencies without asking me first and explaining why.
- Keep scope tight. Anything extra goes under "Future work," not in code.

## C/C++ conventions
- snake_case for functions and variables, UPPER_SNAKE_CASE for macros.
- No dynamic allocation in firmware. No magic numbers; use named constants
  with units in the name (e.g., SHUNT_RESISTANCE_MOHM).
- Every function that touches hardware returns a status code.

## Python conventions
- Python 3.x, type hints, and argparse for the CLI.
- Every tool supports --simulate so it runs without hardware.

## Testing
- Every logic module gets tests in the same task that creates it.
- Packet format changes require updating the spec doc and the tests.

## Working style
- Comment the "why" behind non-obvious decisions, not the "what."
- Explain unfamiliar concepts briefly. I need to be able to defend
  every line in an interview.
- Work on one task at a time and stop for my review.