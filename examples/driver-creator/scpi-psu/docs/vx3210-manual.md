# Vexar Instruments VX3210 — Programming Manual (Excerpt)

**Single-Output Programmable DC Power Supply**
Document P/N VX3210-PM Rev. C — Remote Programming Reference

---

## 1. Instrument overview

The Vexar VX3210 is a single-output linear bench power supply with remote
programming over Ethernet. One output channel delivers 0–32 V at up to 5 A.
The output is controlled by two independent setpoints — a **voltage setpoint**
and a **current limit setpoint** — plus an **output enable** relay.

Identification string (see `*IDN?`):

```
VEXAR,VX3210,<serial>,1.07
```

where `<serial>` is the unit serial number (e.g. `VX3-24117`) and `1.07` is
the installed firmware revision this manual documents.

## 2. Remote interface

| Property | Value |
|---|---|
| Transport | Raw TCP socket ("SCPI raw") |
| Port | **5025** |
| Encoding | 7-bit ASCII |
| Command terminator | newline (`\n`); carriage return before it is ignored |
| Reply terminator | newline (`\n`) |
| Concurrency | one client connection at a time |

Commands are case-sensitive and must be sent exactly as shown in the command
reference (uppercase mnemonics). A command containing `?` is a **query** and
produces exactly one reply line. A command without `?` is a **write** and
produces **no reply of any kind** — do not wait for one.

### 2.1 Number formats

- Programmed values (`VOLT`, `CURR` arguments) are decimal numbers in volts
  or amperes respectively, with an optional sign and up to three decimal
  places, e.g. `3.3`, `3.300`, `12.500`. No unit suffix is accepted.
- All numeric query replies are fixed-point with **exactly three decimal
  places**, e.g. `12.500`, `0.000`.

## 3. Ratings — absolute programmable limits

> **The values below are the absolute programmable limits of the VX3210.**

| Quantity | Minimum | Maximum | Resolution |
|---|---|---|---|
| Voltage setpoint (`VOLT`) | 0.000 V | **32.000 V** | 1 mV |
| Current limit setpoint (`CURR`) | 0.000 A | **5.000 A** | 1 mA |

### 3.1 SAFETY — client-side range enforcement is mandatory

The VX3210 firmware does **not** report an error for an out-of-range
setpoint: it **clamps the value silently** to the nearest programmable limit
and continues. A controlling program that transmits `VOLT 48.0` will leave
the instrument programmed to 32.000 V with no indication that anything was
wrong — which silently corrupts experiments. **Controlling software MUST
validate every setpoint against the ratings table above and reject
out-of-range values BEFORE transmission to the instrument.** Never rely on
the instrument's clamp as a safety mechanism.

## 4. Command reference

### 4.1 `VOLT <value>` — program the voltage setpoint

Sets the output voltage setpoint in volts. Write; no reply.

```
VOLT 3.300
```

### 4.2 `VOLT?` — query the voltage setpoint

Returns the programmed voltage setpoint (not the measured output), three
decimal places. The setpoint is reported whether the output is on or off.

```
VOLT?            -> 3.300
```

### 4.3 `MEAS:VOLT?` — measure the output voltage

Returns the voltage measured at the output terminals, three decimal places.
With the output **ON** and a valid load, the regulated output equals the
programmed setpoint, so `MEAS:VOLT?` returns the setpoint value. With the
output **OFF**, the terminals are disconnected and the reply is `0.000`.

```
MEAS:VOLT?       -> 12.500     (output ON, setpoint 12.500 V)
MEAS:VOLT?       -> 0.000      (output OFF)
```

### 4.4 `CURR <value>` — program the current limit setpoint

Sets the current limit in amperes. Write; no reply.

```
CURR 2.000
```

### 4.5 `CURR?` — query the current limit setpoint

Returns the programmed current limit, three decimal places.

```
CURR?            -> 2.000
```

### 4.6 `MEAS:CURR?` — measure the output current

Returns the current actually drawn by the load, in amperes, three decimal
places. With the output **OFF** the reply is `0.000`. The measured current
depends on the connected load; it is not the `CURR` setpoint.

```
MEAS:CURR?       -> 0.842      (output ON, load drawing 842 mA)
MEAS:CURR?       -> 0.000      (output OFF)
```

### 4.7 `OUTP ON` / `OUTP OFF` — output enable

Connects (`ON`) or disconnects (`OFF`) the output terminals. Write; no
reply. The argument must be the literal token `ON` or `OFF`.

```
OUTP ON
OUTP OFF
```

### 4.8 `OUTP?` — query output state

Returns `1` when the output is enabled, `0` when disabled.

```
OUTP?            -> 1
```

### 4.9 `*IDN?` — identification query

Returns the four-field identification string.

```
*IDN?            -> VEXAR,VX3210,VX3-24117,1.07
```

## 5. Worked programming session

The exact byte-for-byte exchanges below were captured from a VX3210
(serial VX3-24117, firmware 1.07) with a resistive load drawing 842 mA at
12.5 V. `>` is sent by the controller, `<` is the instrument's reply line;
writes produce no reply.

```
> *IDN?
< VEXAR,VX3210,VX3-24117,1.07

> VOLT 12.500
> VOLT?
< 12.500

> CURR 2.000
> CURR?
< 2.000

> OUTP?
< 0
> MEAS:VOLT?
< 0.000

> OUTP ON
> OUTP?
< 1
> MEAS:VOLT?
< 12.500
> MEAS:CURR?
< 0.842

> OUTP OFF
> MEAS:VOLT?
< 0.000
> MEAS:CURR?
< 0.000
```

## 6. Power-on defaults

| State | Default |
|---|---|
| Voltage setpoint | 0.000 V |
| Current limit setpoint | 0.000 A |
| Output | OFF |
