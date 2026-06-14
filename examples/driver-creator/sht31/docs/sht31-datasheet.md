# SHT31-DIS — Digital Humidity and Temperature Sensor

**Datasheet excerpt — Sensirion AG** · Part: SHT31-DIS · Interface: I²C

The SHT31-DIS is a fully calibrated digital humidity and temperature sensor.
Relative humidity and temperature are first-class measurements of equal rank:
every measurement transaction returns **both** quantities in a single 6-byte
frame. The device is read-only in normal operation — it has no programmable
setpoints, output stages, or writable configuration required for measurement.

---

## 1. Product summary

| Parameter | Value |
|---|---|
| Vendor / part | Sensirion SHT31-DIS |
| Measured quantities | Relative humidity (%RH) and temperature (°C) |
| Humidity operating range | 0 … 100 %RH |
| Temperature operating range | −40 … +125 °C |
| Typical RH accuracy | ±2 %RH |
| Typical T accuracy | ±0.2 °C |
| Supply voltage | 2.4 … 5.5 V |
| Interface | I²C, up to 1 MHz |

## 2. I²C addressing

The 7-bit I²C address is selected by the ADDR pin:

| ADDR pin | I²C address |
|---|---|
| low (default) | **0x44** |
| high | 0x45 |

## 3. Single-shot measurement command

A measurement is started by writing a 16-bit command (two command bytes, MSB
first) to the device. In **clock-stretching mode** the sensor holds SCL low
during the conversion and releases it when data is ready, so the master may
issue the read immediately after the command — no polling is required.

Single-shot, clock stretching enabled:

| Repeatability | Command (MSB, LSB) | Max. measurement duration |
|---|---|---|
| **High** (recommended; used throughout this document) | **0x2C, 0x06** | 15 ms |
| Medium | 0x2C, 0x0D | 6 ms |
| Low | 0x2C, 0x10 | 4 ms |

Transaction sequence (high repeatability):

1. **Write** the two command bytes `0x2C 0x06` to the device address.
2. **Read** 6 bytes from the device address. The device clock-stretches until
   the conversion (max 15 ms) is complete, then returns:

```
byte 0: T_MSB    byte 1: T_LSB    byte 2: T_CRC
byte 3: RH_MSB   byte 4: RH_LSB   byte 5: RH_CRC
```

Temperature is always transmitted first, then relative humidity. Each 16-bit
raw value is MSB first and is followed by its CRC byte (section 5).

## 4. Conversion of signal output

The raw signals `S_T` and `S_RH` are unsigned 16-bit values (0 … 65535):

```
S_T  = (T_MSB  << 8) | T_LSB
S_RH = (RH_MSB << 8) | RH_LSB
```

Physical values:

```
T [°C]   = -45 + 175 * S_T  / 65535
RH [%RH] = 100 * S_RH / 65535
```

Inverse relations (useful for verification):

```
S_T  = (T + 45) * 65535 / 175
S_RH = RH * 65535 / 100
```

## 5. Checksum (CRC-8)

Each 16-bit word in the read frame is protected by a CRC byte computed over the
two data bytes of that word only:

| Property | Value |
|---|---|
| Width | 8 bit |
| Polynomial | 0x31 (x⁸ + x⁵ + x⁴ + 1) |
| Initialization | 0xFF |
| Bit order | MSB first |
| Reflect input / output | no / no |
| Final XOR | none (0x00) |
| Check value | CRC(0xBE, 0xEF) = **0x92** |

**Verifying the CRC is recommended but optional for basic operation** — hosts
that skip the check simply use bytes 0–1 and 3–4 of the frame as-is.

## 6. Worked examples (host-side verification vectors)

All divisions are exact IEEE-754 double-precision results.

**Example 1 — temperature, raw 0x6666.**
`S_T = 0x6666 = 26214`.
`T = -45 + 175 × 26214 / 65535 = -45 + 4587450 / 65535 = -45 + 70.0`
→ **T = 25.0 °C** (exact).

**Example 2 — temperature, raw 0x851E.**
`S_T = 0x851E = 34078`.
`T = -45 + 175 × 34078 / 65535 = -45 + 5963650 / 65535 = -45 + 90.99946593423361`
→ **T = 45.99946593423361 °C** (≈ 45.9995 °C).

**Example 3 — relative humidity, raw 0x8000.**
`S_RH = 0x8000 = 32768`.
`RH = 100 × 32768 / 65535 = 3276800 / 65535`
→ **RH = 50.000762951094835 %RH** (≈ 50.0008 %RH).

**Example 4 — relative humidity, raw 0x3333.**
`S_RH = 0x3333 = 13107`.
`RH = 100 × 13107 / 65535 = 1310700 / 65535 = 20.0`
→ **RH = 20.0 %RH** (exact).

**Example 5 — complete frame.** For ambient conditions T = 25.0 °C and
RH ≈ 50.0008 %RH the device returns, after the `0x2C 0x06` command:

```
0x66 0x66 0x93   0x80 0x00 0xA2
 T_MSB T_LSB CRC  RH_MSB RH_LSB CRC
```

where `CRC(0x66, 0x66) = 0x93` and `CRC(0x80, 0x00) = 0xA2`.
Further CRC vectors: `CRC(0x85, 0x1E) = 0x89`, `CRC(0x33, 0x33) = 0x88`.

## 7. Operating conditions

| Parameter | Min | Max | Unit |
|---|---|---|---|
| Supply voltage VDD | 2.4 | 5.5 | V |
| Temperature (measurement range) | −40 | +125 | °C |
| Relative humidity (measurement range) | 0 | 100 | %RH |
| Measurement duration, high repeatability | — | 15 | ms |

The sensor is a measurement-only device: there are no host-settable operating
parameters, and therefore no programmable limits to enforce on writes.
