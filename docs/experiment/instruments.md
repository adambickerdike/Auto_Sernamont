# Instruments and Control Interfaces

Every instrument in the setup, the interface the software uses to reach it, the
exact command sequences it is sent, and the operating rules that are there for
a reason. If an instrument is behaving oddly, this is the page that tells you
what the software *thinks* it configured.

The motion algorithms built on top of these interfaces are described in
[motion control](../software/motion-control.md); the code layout is in
[instrument control](../software/instrument-control.md).

---

## 1. Connection map

| Device | Interface | Address / port | Defined in |
| --- | --- | --- | --- |
| Laser, Thorlabs KLS1550 + KCube | USB / Kinesis .NET | auto-detect, serial prefix 56 | [`kls1550.py`](../../pockels/kls1550.py) |
| HWP rotator, Elliptec ELL14 | USB serial, 9600 8N1 | **COM5**, bus address **1** | `PORT_HWP, ADDR_HWP` |
| QWP rotator, ELL14 | USB serial, 9600 8N1 | **COM4**, bus address **2** | `PORT_QWP, ADDR_QWP` |
| Analyser rotator, ELL14 | USB serial, 9600 8N1 | **COM8**, bus address **2** | `PORT_ANL, ADDR_ANL` |
| XY stage X, Thorlabs KCubeStepper | USB / Kinesis .NET | serial **26006987** | `STAGE_SERIAL_X` |
| XY stage Y, KCubeStepper | USB / Kinesis .NET | serial **26007025** | `STAGE_SERIAL_Y` |
| Oscilloscope, Tektronix TBS | USB-TMC / VISA | `USB0::0x0699::0x03C7::C021052::0::INSTR`, CH1 | `SCOPE_VISA`, `SCOPE_SOURCE` |
| Lock-in, Signal Recovery DSP7230 | raw TCP socket via VISA | `TCPIP0::169.254.150.230::50001::SOCKET` | `LOCKIN_RESOURCE` |
| Function generator, Aim-TTi TGF3162 | USB-CDC serial / VISA | `ASRL3::INSTR` (COM3) | `FUNCGEN_RESOURCE` |
| SMU, Aim-TTi SMU4201 | USB-CDC serial, 9600 8N1, `\r\n` | **COM11 / COM13**, auto-resolved | [`smu4201_iv_sweep.py`](../../pockels/smu4201_iv_sweep.py) |
| Switch matrix, Arduino Nano | USB serial (CH340), 9600 | **COM12**, auto-resolved | [`arduino_switch_matrix.py`](../../pockels/arduino_switch_matrix.py) |
| Camera | USB / OpenCV | index 0/1/2, DirectShow → MSMF → default | `stage_calibration.py` → `LiveView` |

**Auto-resolution.** Windows renumbers USB COM ports whenever cables move.
`serial_port_resolver.py` identifies the SMU and the Arduino by USB identity
(VID/PID, serial number, hub location) and, for the Arduino, by probing for its
`"Signal Matrix Ready"` banner, so those two rarely need editing. The three
rotator ports are hard-coded; if one changes you must edit the constant.

```bat
python pockels_fast_map_gui.py --cli --list-serial-ports
```

---

## 2. Laser: Thorlabs KLS1550 (Kinesis .NET)

A fibre-coupled 1550 nm diode laser in a KCube laser source. There is no SCPI
here: control goes through Thorlabs' `.NET` class library, bridged into Python
by `pythonnet`.

```python
clr.AddReference("Thorlabs.MotionControl.KCube.LaserSourceCLI")
from Thorlabs.MotionControl.KCube.LaserSourceCLI import KCubeLaserSource, InputSourceSettings

KLS1550.list_serials(prefix=56)     # DeviceManagerCLI.GetDeviceList(56)
kls = KLS1550(serial); kls.connect()
kls.set_power_absolute(7.0)         # mW
kls.on() / kls.off()
kls.get_current(), kls.get_measured_power()
```

The default setpoint is **7.0 mW** (`--laser-power-mw`); `connect()` also sets
the input source explicitly via `LaserSourceInputSourceFlags`. In the GUI,
clicking the laser box in the *Optical Train* panel toggles emission and
**Apply Laser Power** re-applies the setpoint live. Because the whole `.NET`
stack is Windows-only, so is the measurement. The analysis modules are not,
since they import no hardware drivers.

---

## 3. Rotators: Thorlabs Elliptec ELL14

Three identical motorised rotation mounts carry the HWP, the QWP and the
analyser. The driver is
[`pockels/elliptec_serial.py`](../../pockels/elliptec_serial.py), class
`ElliptecRotator`.

### 3.1 The wire protocol

Elliptec devices speak a short ASCII-hex protocol over 9600 8N1. Several
devices can share one bus, each with a one-character address from `0` to `F`.

```text
Packet out:   <addr><2-char command><hex payload>\n
Packet in:    <addr><2-char reply><hex data>\r\n
```

| Command | Meaning | Reply |
| --- | --- | --- |
| `gs` | get status | `GS` + status code |
| `gp` | get position | `PO` + 32-bit two's-complement encoder count |
| `ho` + 1 byte | home (0 = CW, 1 = CCW) | status |
| `mr` + 4 bytes | **move relative** by a signed encoder delta | `GS` or `PO` |
| `sv` + 2 hex digits | set velocity, 1 to 100 % of maximum | status |

Status codes include `ok`, `busy`, `communication timeout`, `mechanical
timeout`, `command error`, `value out of range`, `module isolated`,
`initialization error`, `thermal error`, `sensor error`, `motor error`,
`out of range` and `overcurrent`.

### 3.2 Encoder counts

```python
COUNTS_PER_REVOLUTION = 143360        # = 398.222 counts per degree
angle_unwrapped = -360 * (position + offset) / COUNTS_PER_REVOLUTION
angle           = angle_unwrapped % 360
```

Note the **minus sign**: the protocol counts clockwise-positive while the
software convention is counter-clockwise-positive. Relative moves invert the
same way, `delta_counts = -round(degrees * 143360 / 360)`, encoded as a 4-byte
big-endian two's-complement value.

### 3.3 There is no absolute-move command

The device only moves *relatively*. `set_angle()` therefore reads the position
(`gp`), computes the shortest signed delta wrapped into (−180°, +180°], issues
`move_by()`, settles, and reads the achieved angle back. So every absolute move
costs an extra round trip, and is only as good as the readback that preceded
it, which is why readback verification is not optional in this system.

### 3.4 Addresses, tolerances and recovery

| Item | Value |
| --- | --- |
| Resolution | 143 360 counts/rev ≈ **398.2 counts/degree** |
| Specified accuracy | **±0.05°** |
| Software tolerance | 0.08° on the accurate chunked path; **0.5°** readback tolerance on fast direct moves (`--rotator-verify-tol`) |
| Velocity | `sv`, 1 to 100 %; fast-map default **50 %**, clamped to ≥ 25 % (below which the motor can stall on reversal) |
| Backlash | 0.3° undershoot then approach from below (`ROTATOR_BACKLASH_DEG`) |
| Fault recovery | a `sensor` / `limit` / `range` fault triggers `home()` → `tare()` → recompute → retry, inside `set_angle()` |

> **Warning** The QWP and the analyser both sit at bus address **2**, on
> *different* COM ports; the HWP is address **1** on its own port. If you rewire
> the Elliptec bus you must update **both** the port and the address, and a
> mistake here silently moves the wrong optic.

`tare()` sets `self._offset = -self._position`, i.e. "wherever I am now is 0°".
The mechanical home index is not aligned with any waveplate's fast axis and the
offset is arbitrary per unit, so the software zero is defined by `home()` +
`tare()` and the optical calibration then refers everything to the polariser
frame. If you supply exact **raw** angles the software forces
`--no-rotator-home` so those coordinates are not re-tared out from under you.

---

## 4. XY stage: Thorlabs KCubeStepper (Kinesis .NET)

Two KCube stepper drivers, one per axis, again through `pythonnet`:

```python
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.KCube.StepperMotorCLI.dll")
```

`initialize_device()` connects by serial number, waits for
`IsSettingsInitialized()`, starts 50 ms polling, enables the motor, and loads
the `MTS25-Z8` motor configuration before setting velocity parameters.

| Item | Value |
| --- | --- |
| Serial numbers | **26006987** (X), **26007025** (Y) |
| Travel | 25.0 mm per axis; software frame centred at 12.5 mm so global (0, 0) is the stage centre |
| Soft limits | −0.3 … 25.3 mm (`STAGE_MIN_MM` / `STAGE_MAX_MM`) |
| Position tolerance | **5 µm** |
| Backlash | **20 µm**, always approach from below |
| Scanning velocity | 0.5 mm/s at 0.5 mm/s² |
| Settle | position stable within **0.5 µm** for 0.5 s, then 10 averaged reads 20 ms apart |

**Decimal precision.** Positions are converted by `py_to_net_decimal()` from a
Python `Decimal` straight into a .NET `System.Decimal` via `Parse(str(...))`,
never through a float, so a commanded 2.5000 mm does not arrive as
2.4999999999.

> **Note** The over-travel quirk. Kinesis `MoveTo` refuses targets outside
> 0 to 25 mm, but `MoveJog` / `MoveContinuous` **do physically move** past those
> limits while *also* throwing an exception. The code suppresses those
> exceptions and polls the real position instead, with a 2 s timeout. It must
> never clamp the target into 0 to 25 mm, because pixels near the edge of the
> chip genuinely sit slightly outside.

---

## 5. Oscilloscope: Tektronix TBS as a calibrated DC voltmeter

The scope is **not** used as a waveform viewer. It is a slow, averaged,
auto-ranging DC voltmeter that reads the mean detector level. That level tells
you how deep the null is, how bright the transmission is (which the stage
alignment climbs), and the local Malus slope that converts lock-in volts into a
polarisation rotation.

### 5.1 Setup sequence, applied on connect

`ScopeDetector.connect()` in
[`pockels/stage_calibration.py`](../../pockels/stage_calibration.py):

```text
*CLS
*IDN?                                   (logged)
SELEct:CH1 ON
ACQuire:STOPAfter RUNSTop
ACQuire:STATE RUN
TRIGger:A:TYPe EDGE
TRIGger:A:MODe AUTO
TRIGger:A:EDGE:SOURce LINE              (falls back to CH1 if LINE is refused)
MEASUrement:IMMed:SOURce1 CH1           (falls back to :SOURce CH1)
MEASUrement:IMMed:TYPe MEAN
MEASUrement:IMMed:VALue?                (probe: sets measu_ok)
CH1:SCAle?                              (read the initial V/div)
```

VISA session settings: `timeout = 5000 ms`, `read_termination = '\n'`,
`write_termination = None`, `encoding = 'latin_1'`.

If the immediate-measurement probe fails, the driver reads the raw waveform and
does the arithmetic itself:

```text
HEADER 0 ; DATA:SOURce CH1 ; DATA:ENCdg RIBinary ; DATA:WIDth 1 ; DATA:START 1
WFMPRe:NR_PT? → DATA:STOP <npts>
WFMPRe:YMULT? / YZERO? / YOFF?          → V = (raw - YOFF)*YMULT + YZERO
```

**Why trigger on LINE.** The measurement is a DC level, so there is nothing to
trigger on in the signal. Triggering on the mains line in AUTO mode keeps the
acquisition free-running and phase-stable with respect to mains hum, which is
the dominant periodic contamination on a slow photodiode trace.

### 5.2 Smart V/div ranging

Available scales, from `SCOPE_VDIV_OPTIONS_MV`: 10, 20, 50, 100, 200, 500 mV
and 1, 2, 5 V per division.

The rule, in `_select_optimal_range()`: pick the **smallest** scale for which

$$0.25 \times V_\mathrm{div} \;\le\; |V_\mathrm{signal}| \;\le\; 4.0 \times V_\mathrm{div}$$

that is `SMART_RANGE_MIN_FILL = 0.25` and `SMART_RANGE_MULTIPLIER = 4.0`, with
the finest scale (10 mV/div) allowed to violate the lower bound because there
is nothing finer to fall back to. In words: the signal must be large enough to
resolve (at least a quarter of one division) and small enough not to run off
screen (no more than four divisions).

### 5.3 Why the first 5 samples after a range change are discarded

When the vertical scale changes, the acquisition and the on-screen MEAN do not
update instantaneously: the first readings after the change are computed partly
from pre-change data and are simply **wrong**. Averaging them in biases the
result toward the old range, and because ranging happens exactly when the
signal has just moved a lot (a new pixel, a new analyser angle), the error
lands where it does the most damage. The fix is unconditional: wait
`POST_SCALE_CHANGE_SETTLE_S`, then discard the next
`POST_RANGE_DISCARD_SAMPLES = 5` readings (20 ms apart) before any sample
counts. This was a real bug fix, not a precaution; the same discard applies
after settling at a new operating point (`DISCARD_FIRST_N_SAMPLES = 5`).

### 5.4 Stable averaging

`read_averaged()` / the stable-read path:

```text
1. (optional) probe once and auto-range
2. if the range changed: settle, DISCARD 5 samples
3. sample MEASUrement:IMMed:VALue? repeatedly, ~20 ms apart
4. median-absolute-deviation outlier rejection (MAD_K = 2.5)
5. stop when relative standard deviation < STABLE_RSD_TOL (0.30 %)
   with at least STABLE_MIN_SAMPLES (18) in hand, or the time limit expires
6. return (mean_volts, std_volts, power_watts, vdiv)
```

Null reads start at a finer scale (`NULL_SCOPE_START_VDIV = 10 mV/div`), take
`NULL_SCOPE_READ_AVERAGES = 3` averaged reads, and allow up to
`NULL_SCOPE_AUTORANGE_RETRIES = 5` autorange attempts. Alignment points use 3
averaged reads (fast) or 5 (careful), **with auto-ranging disabled** so a range
change can never masquerade as a peak.

`V_TO_W = 1/(0.875 × 4.75e3)` converts volts to watts and is valid **only** for
the PDA30B2 at 10 dB into a high-impedance load.

**Thread safety.** Every scope access is taken under an `RLock`, because the
live-view ticker and the alignment worker both touch the same VISA session.
`recover()` clears the session, re-asserts `ACQuire:STATE RUN`, refreshes the
preamble if needed, and proves recovery with up to three test reads.

---

## 6. Lock-in: Signal Recovery DSP7230

The AC channel: it extracts the microvolt-level 30 kHz modulation from the
photodiode signal. Driver: class `DSP7230` in
[`pockels/Lock_In_Mag_Phase_Track.py`](../../pockels/Lock_In_Mag_Phase_Track.py).

### 6.1 Socket settings

| Setting | Value |
| --- | --- |
| Resource | `TCPIP0::169.254.150.230::50001::SOCKET` (raw TCP, not VXI-11) |
| `read_termination` | `"\r"` |
| `write_termination` | `"\x00"` (NUL) |
| Timeout | 2000 ms |
| On connect | `inst.clear()`, tolerated if unsupported |

The address is a link-local (169.254.x.x) one: the lock-in is on a direct
Ethernet link, not a routed network.

### 6.2 The configuration actually applied

```python
lockin.set_voltage_input_mode()        # IMODE 0      voltage input
lockin.set_ac_coupling()               # DCCOUPLE 0   AC coupled
lockin.set_automatic_ac_gain(False)    # AUTOMATIC 0  no automatic AC gain
lockin.set_reference_mode_single()     # REFMODE 0
lockin.set_reference_source(2)         # IE 2         external ANALOG reference
lockin.disable_synchronous_filter()    # SYNC 0
lockin.disable_fast_output_mode()      # FASTMODE 0
lockin.set_sensitivity(16)             # SEN 16       200 µV RMS full scale
lockin.set_time_constant(14)           # TC 14        500 ms
lockin.set_filter_slope(1)             # SLOPE 1      12 dB/oct
```

plus, from `apply_safe_startup()`, `FLOAT 1` (floating input shell) and
`LF 0 0` (line filter off).

Other commands used during a run: `MP.` (magnitude and phase in one
synchronised query), `XY.` (X and Y together), `N` (overload byte), `ST`
(status byte), `REFN` (harmonic), `REFP.` (reference phase), `FRQ.` (measured
reference frequency; the unit returns 0 if it is unlocked).

> **Note** `MP.` is preferred over separate `MAG.` and `PHA.` queries because
> it returns a *synchronised* pair. Two separate queries can straddle a change
> in the signal and produce a magnitude and a phase that never coexisted.

### 6.3 Sensitivity table

`SENS_TABLE_VOLTS` maps the `SEN` index to full-scale volts in a 1-2-5
sequence:

| Index | Full scale | Index | Full scale | Index | Full scale |
| --- | --- | --- | --- | --- | --- |
| 3 | 10 nV | 12 | 10 µV | 21 | 10 mV |
| 4 | 20 nV | 13 | 20 µV | 22 | 20 mV |
| 5 | 50 nV | 14 | 50 µV | 23 | 50 mV |
| 6 | 100 nV | 15 | 100 µV | 24 | 100 mV |
| 7 | 200 nV | **16** | **200 µV** | 25 | 200 mV |
| 8 | 500 nV | 17 | 500 µV | 26 | 500 mV |
| 9 | 1 µV | 18 | 1 mV | 27 | 1 V |
| 10 | 2 µV | 19 | 2 mV | | |
| 11 | 5 µV | 20 | 5 mV | | |

The map runs at **index 16 = 200 µV RMS full scale, fixed for the whole chip**.
Rows are graded against it: ≥ 85 % of full scale is *warned*
(`lockin_range_near_fullscale`), ≥ 98 % is *invalid*
(`lockin_range_exceeded`), and a set overload byte is *invalid*
(`lockin_overload`).

### 6.4 The 2 × TC × order settling rule

A lock-in's output filter is a low-pass of order $n$ (one pole per 6 dB/oct)
with time constant $\tau$. After a step (a new analyser angle, or the drive
switching on) the output approaches its new value with that filter's impulse
response, so the settling time scales as $n\tau$, not $\tau$. The software
enforces

$$t_\mathrm{settle} \;\ge\; 2 \,\tau \, n$$

`minimum_lockin_settle_s()` computes `2.0 * TC_seconds * order`, where
`order = slope_index + 1`, and refuses any TC index below 8 (5 ms) because
reproducible fast-output mode is disabled. At the production defaults
(TC index 14 = 500 ms, 12 dB/oct so $n = 2$) that is **2.0 s**.

Both the settle time *and* the spacing between averaged samples are auto-raised
to that value, so the averaged samples are approximately independent rather
than repeated looks at the same filtered state. Hysteresis points use ≥ 5 × TC.
Averaging is done in **X/Y (Cartesian)**, never in magnitude: magnitude is
$\sqrt{X^2+Y^2}$, a positive-definite function of noisy quantities, so
averaging magnitudes rectifies noise and biases small signals upward, which is
fatal near a coercive point where the true magnitude passes through zero.

### 6.5 Three deliberate quirks: do not "fix" these

1. **Configuration is write-only; readbacks are skipped.** After applying the
   settings the code records
   `"readback_queries_skipped": ["TC.", "SLOPE", "IE", "SEN", "FRQ."]` and moves
   on. This particular unit sometimes returns an empty reply *even after
   accepting the setting*. A strict readback check would therefore abort a
   correctly configured multi-hour run on a communications artefact. The
   configuration is recorded as `"configuration_mode":
   "fixed_range_write_only"` in the run metadata so that a reader knows the
   settings are *commanded*, not *confirmed*.
2. **The range is fixed.** `install_fixed_lockin_range_policy()` replaces the
   range-changing `check_overload()` helper for the whole process, so in-run AC
   and DC follow-ups cannot change the sensitivity either. Auto-ranging
   mid-map would make pixels measured on different ranges non-comparable, which
   destroys the very thing a chip map is for. (The one exception is the
   optional predictive ranging used in hysteresis loops, which is explicit,
   logged, and restores the fixed range at the end.)
3. **`AS` (auto-sensitivity) and `AQN` (auto-phase) are never called.** Auto
   sensitivity would break cross-pixel comparability; **auto-phase would
   destroy the sign information**, and the sign of the response (the fact that
   +45° and −45° give opposite signs, and that the signed response follows a
   2θ harmonic) is a large part of the physics.

> **Note** The driver contains a defensive parser, `_parse_float_robust()`,
> for a real instrument fault: the DSP7230 occasionally emits a reply with the
> mantissa duplicated before the exponent (`77.95E7.95E-08`). The parser
> repairs those, and retries once after clearing I/O, rather than crashing.

---

## 7. Function generator: Aim-TTi TGF3162

| Channel | Role | Settings |
| --- | --- | --- |
| **CH1** | AC drive to the chip | 1 to 9 Vpp (map default **9 Vpp**, hysteresis probe **4 Vpp**), 30 kHz, `ZLOAD OPEN` |
| **CH2** | Lock-in reference | **0.5 Vpp**, 30 kHz, `ZLOAD OPEN`, **always on** |

**Command style.** The TGF3162 is channel-modal: you select a channel and then
talk to it.

```text
CHN 2 ; ZLOAD OPEN ; FREQ 30000 ; AMPL 0.5  ; OUTPUT ON     # reference, once at startup
CHN 1 ; ZLOAD OPEN ; FREQ 30000 ; AMPL <vpp>                # drive, gated per window
```

`funcgen_send()` adds a 0.1 s processing delay after every command, because
this instrument does not like being talked over.

### 7.1 The select-verify-EER? safety sequence

**The TGF3162 has no documented OUTPUT-state query.** You cannot ask it whether
the chip is currently being driven. For a system that applies voltage to a
device under test, that is unacceptable on its own, so
`_funcgen_set_sweep_output()` builds the strongest confirmation available out
of the queries that *do* exist:

```text
1. EER?              clear/expose any stale execution error, so a later EER?
                     unambiguously belongs to THIS transition
2. CHN 1             select the drive channel
3. CHN?              must read back 1, otherwise you are about to switch the
                     WRONG channel
4. OUTPUT ON | OFF   the actual transition
5. EER?              must be 0.  −80 means the generator disabled its own
                     output because of an output-voltage overload
6. sleep 0.2 s
```

With `required=True` a failure **raises**, which escalates into hardware
recovery rather than silently leaving the chip driven or undriven.

### 7.2 Safety ordering

- `configure_funcgen_safe()` deliberately brings CH1 up **off**. An earlier
  calibration helper turned it on during setup, which is the wrong ordering
  when a pixel may not yet be routed through the switch matrix.
- CH1 is gated on only for a lock-in measurement window and off again
  immediately afterwards. CH2 stays on for the whole session because the
  lock-in needs a continuous reference.
- Amplitude changes are made with the output **off**: changing CH1 from 9 Vpp
  to 4 Vpp *while the output is on* can upset this instrument.

---

## 8. Source-measure unit: Aim-TTi SMU4201

The DC side: the poling bias, and the DC sweep that produces hysteresis loops.
Driver: class `SMU4201` in
[`pockels/smu4201_iv_sweep.py`](../../pockels/smu4201_iv_sweep.py), plain SCPI
over a USB-CDC serial port at 9600 8N1 with `\r\n` terminators.

### 8.1 Session setup

```text
*CLS
*RST                                                (then 0.6 s; *RST takes a moment)
SYSTem:FUNCtion:MODE SOURCEVOLTage
SOURce:VOLTage:TERMinals 2WIRe                      deterministic terminal config
SOURce:VOLTage:SHAPe FIXed
SOURce:VOLTage:SHAPe:COUNt 1
SOURce:VOLTage:SHAPe:TRIGger OFF                    never wait for an external trigger
SOURce:VOLTage:MEASure:PRIMary CURRent
SOURce:VOLTage:MEASure:SECondary VOLTage
SOURce:VOLTage:MEASure:COUNt:INFinite ON            keep sampling while we step the source
SOURce:VOLTage:CURRent:LIMit <compliance>
SOURce:VOLTage:FIXed:APERture:NPLCycles 1.0
SOURce:VOLTage:FIXed:LEVel 0                        start at 0 V
SOURce:VOLTage:DELay:AUTO ON
```

Errors are drained with up to 20 `SYSTem:ERRor?` queries. On close,
`restore_safe_defaults()` sets `OUTPut:STATe OFF`, the level back to 0 V, and
turns infinite measurement off again so the front panel behaves.

### 8.2 Why the secondary measurement is the point

The SMU reports a **primary** result (current) and a **secondary** result (the
**actual terminal voltage**):

```text
MEASure:PRIMary:LIVEdata?     → current through the device
MEASure:SECondary:LIVEdata?   → the voltage the SMU is really measuring
```

The *programmed* level is what you asked for. The *measured terminal voltage*
is what the pixel actually got. They differ whenever something is wrong: an
open probe, a broken bond, a shorted pixel pulling the source into compliance,
a relay that did not close. Recording both turns every hysteresis point into a
small electrical audit, and with the primary current reading you get a leakage
$I(V)$ curve and dead/short pixel detection for free.

### 8.3 The per-point electrical audit

Performed while the AC drive is **off**:

```text
programmed level  ← SOURce:VOLTage:FIXed:LEVel
measured voltage  ← MEASure:SECondary:LIVEdata?   (up to 3 attempts, 0.1 s apart)
measured current  ← MEASure:PRIMary:LIVEdata?
compliance flag   ← instrument status
```

If the measured voltage differs from the target by more than
`SMU_DC_READBACK_TOLERANCE_V = 0.5 V`, or telemetry stays unavailable for three
consecutive attempts, the point is **rejected before the AC drive can turn
on**. Catching it here means a bad contact costs you one point, not a whole
loop of quietly meaningless data.

### 8.4 Ramping, slew and output ordering

| Item | Value | Why |
| --- | --- | --- |
| Voltage range used | 0 … ±40 V | `DC_HYST_VMAX = 40.0`, the hard ceiling of every trajectory |
| Compliance | **1 mA** (`SMU_COMPLIANCE_A = 1e-3`; `--smu-compliance`, entered in mA in the GUI) | the device is a capacitor with leakage; 1 mA is far above any legitimate leakage and far below anything that would damage a pixel |
| Ramp | **5 V chunks, 0.2 s dwell** (`SMU_RAMP_STEP_V`, `SMU_RAMP_DWELL_S`) | a step change into a capacitive load draws $i = C\,\mathrm{d}V/\mathrm{d}t$ and trips compliance |
| Slew limit | **50 V/ms** (`SMU_SLEW_RATE_V_PER_MS`), sent as `SOURce:VOLTage:SLEW 50.000V/ms` | same reason, during the fast bipolar domain-reset train |
| NPLC | 1.0 | one mains cycle per measurement, which rejects mains hum |

**Output ordering.** Always programme the level first, *then* enable:

```python
smu.set_voltage(V)      # while OUTPUT is still OFF
smu.output(True)        # then enable
```

Never the other way round. Enabling the output at a stale level would apply
the *previous* pixel's voltage to the newly routed one.

> **Note** The front-panel banner: enabling the output makes the SMU display
> a "Counts / Shapes" banner for several seconds during which the rails are not
> fully established. The standalone I-V script waits `PRESWEEP_S = 5 s`; the
> campaign code instead keeps the output **on** through a whole domain-reset
> train and software-steps the bipolar pulses, so it never pays that cost
> mid-sequence. That reset (`reset_domains_pulsed()`) is a bipolar depoling
> envelope from 40 V down to 0.05 V over 30 exponentially decaying amplitudes,
> 200 cycles of +Vₙ then −Vₙ at 5 ms each, about 12 000 reversals in ~30 s.

---

## 9. Switch matrix: Arduino Nano

An Arduino Nano (usually a CH340 clone) running
[`firmware/switch_matrix/switch_matrix.ino`](../../firmware/switch_matrix/switch_matrix.ino)
at 9600 baud. Commands are line-terminated ASCII: `1`…`100` for a logical
pixel, `E1`…`E100` for a raw electrical switch, `0` for all off; the boot
banner is `"Signal Matrix Ready"`; switching is exclusive. The full circuit,
protocol and failure modes are on [its own page](switch-matrix.md).

---

## 10. Safety limits enforced in software

| Limit | Value | Enforced by |
| --- | --- | --- |
| DC voltage ceiling | **±40 V** | `DC_HYST_VMAX`; `validate_hysteresis_voltage_limit()` rejects anything higher on every path: in-run, queued, adaptive, reset |
| SMU current compliance | **1 mA** default | `SMU_COMPLIANCE_A`; must be finite and > 0 |
| SMU slew during reset | **50 V/ms** | `SMU_SLEW_RATE_V_PER_MS` |
| DC terminal-voltage error | **0.5 V** | `SMU_DC_READBACK_TOLERANCE_V`, which aborts the point **before** AC turns on |
| Rotator velocity | clamped to 25 to 100 % | `configure_rotator_velocity()` |
| Rotator landing | 0.5° with one corrective retry; a verified move that still fails stops the run | `rotator_verified_move_result()` |
| Stage travel | soft −0.3 … 25.3 mm | `STAGE_MIN_MM` / `STAGE_MAX_MM` |
| Lock-in TC index | ≥ 8 (5 ms) | `minimum_lockin_settle_s()` |
| Null acceptance | 14.5 mV, plus a 1.5 mV continuation margin | `--null-check-max-mv`, `--null-certify-margin-mv` |
| Switch-matrix isolation | all channels off in every error path and between pixels | `routed_arduino_channel()`, `close()` |
| Output ordering | AC off before any DC ramp; matrix routed before any voltage | `ensure_fast_map_outputs_off()`, the hysteresis entry interlock |

> **Warning** Do not relax these "just to try something". If you genuinely
> need a different limit, change the constant, record it in the run log, and
> understand that every measurement taken with it is a different experiment.

---

## 11. Windows and USB

The single most common cause of a failed overnight run is Windows suspending a
USB hub. Run this once from an elevated PowerShell, then reboot:

```powershell
powershell -ExecutionPolicy Bypass -File tools\pockels_usb_stability_setup.ps1
```

It disables USB selective suspend on both power profiles and clears the "allow
the computer to turn off this device" policy on the hubs this setup uses; it
does not disable or remove any device. Also set the power plan to **High
performance**, disable sleep and hibernate, and do not move USB cables between
sockets mid-campaign, because COM numbers change when you do.

See [troubleshooting](../guide/troubleshooting.md) for what each failure looks
like from the operator's seat, and the [glossary](../reference/glossary.md) for
the abbreviations used above.

---

<div align="center">

[← The optical beamline](beamline.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [The BaTiO₃ chip →](chip.md)

</div>
