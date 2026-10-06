# Arena Bench Temp-1 — I2C temperature/humidity command reference

Fictional instrument, no real part — the same "Arena Bench" style as
`scpi-psu` and `dmm`, but reached over I2C instead of SCPI. The measurement
command, frame layout and conversion formulas below are adapted from a real
digital humidity/temperature sensor's single-shot I2C protocol (see
determlab/adk-lab `cases/sht31` for the real-world reference this is based
on); write `driver.py` for it: `compatible = "arena,bench-temp1"`, bound
under a `shal,sim-i2c` bus, I2C address `0x44`.

## Measurement command

A measurement is a two-step I2C transaction:

1. **Write** the 2-byte command `0x2C 0x06` to the device address.
2. **Read** 6 bytes back. The device returns the conversion immediately
   (no polling needed):

```
byte 0: T_MSB    byte 1: T_LSB    byte 2: T_CRC
byte 3: RH_MSB   byte 4: RH_LSB   byte 5: RH_CRC
```

Temperature is always first, then relative humidity. Each 16-bit raw value
is big-endian (MSB first) and followed by its own CRC-8 byte (see below).

## Conversion

The raw values `S_T` and `S_RH` are unsigned 16-bit (`0 … 65535`):

```
S_T  = (T_MSB  << 8) | T_LSB
S_RH = (RH_MSB << 8) | RH_LSB

T   [°C]  = -45 + 175 * S_T  / 65535
RH  [%RH] = 100 * S_RH / 65535
```

## Checksum (CRC-8) — optional

Each 16-bit word's CRC byte is computed over its own two data bytes: width
8 bit, polynomial `0x31` (`x^8 + x^5 + x^4 + 1`), init `0xFF`, MSB-first, no
reflect, no final XOR. **Verifying the CRC is optional for basic
operation** — a driver may use bytes 0-1 and 3-4 of the frame as-is.

## Suggested ops

- `read_celsius() -> float` — `side_effect="none"`. The only op this
  instrument's probing role needs.

`shal docs` (in the main `shal` package) is the general guide to writing a
driver; `shal-arena check-driver` is how the arena runner checks yours —
you won't see the harness topology it checks against, only whether your
driver passed.
