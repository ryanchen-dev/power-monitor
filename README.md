# Power Monitor

An Arduino Uno R3 reads an INA219 current/voltage sensor over I2C, computes power and energy with integer math, and streams each sample as a 25-byte CRC-8 framed packet over UART at 115200 baud. A Python host tool decodes the stream, logs it to CSV, and plots it live; its `--simulate` mode runs without hardware. The frame format is defined in [docs/packet_format.md](docs/packet_format.md), and the requirements, design, and task plan are in [.kiro/specs/power-monitor/](.kiro/specs/power-monitor/).

Status: in progress. Only the project scaffold exists so far.

## Prerequisites

- [PlatformIO Core](https://platformio.org/install/cli) (built with 6.2.0)
- Python 3.10 or newer (built with 3.12.6)
- Xcode command-line tools (`xcode-select --install`), which provide the `cc` compiler used by the native tests

## Setup

From the repo root:

```sh
python3 -m venv .venv && source .venv/bin/activate && pip install -r host/requirements.txt
```

## Commands

Run these from the repo root with the venv active.

| Command | Purpose |
| --- | --- |
| `pio test -e native` | Builds the firmware logic library for macOS and runs the Unity unit tests, no hardware needed. |
| `cd host && python -m pytest` | Runs the host tool's pytest and hypothesis tests, no hardware needed. |
| `pio run -e uno` | Builds the Uno firmware and prints RAM/flash usage (budget: 1024 B RAM, 16384 B flash). It won't link until `src/main.cpp` is added in task 24.1. |

## Test framework version

Unity 2.6.1 (resolved by PlatformIO Core 6.2.0 as `throwtheswitch/Unity @ ^2.6.1`).
It's recorded here because PlatformIO Core supplies Unity, so `platformio.ini` doesn't pin its version.
