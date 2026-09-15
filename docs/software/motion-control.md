# Motion Control

**What this page is for:** exactly how the software makes the motors move.
Wire protocols, encoder arithmetic, backlash, verification, fault recovery and
every alignment search algorithm with its real constants.

Read this if a motor misbehaves, if you are porting the code to different
hardware, or if a reviewer asks *"how do you know the analyser was actually at
$+45^\circ$?"*.

---

## Contents

1. [Two philosophies of motion](#1-two-philosophies-of-motion)
2. [The Elliptec ELL14 rotators](#2-the-elliptec-ell14-rotators)
3. [Rotator move strategies](#3-rotator-move-strategies)
4. [The Thorlabs XY stage](#4-the-thorlabs-xy-stage)
5. [Stage alignment algorithms](#5-stage-alignment-algorithms)
6. [Measurement hygiene during a search](#6-measurement-hygiene-during-a-search)

---

## 1. Two philosophies of motion

Every motorised axis is driven under one of two policies, and the choice is
always explicit at the call site.

| Policy | Used for | Characteristics |
| --- | --- | --- |
| **Accurate** | Calibration, null searches, anything whose absolute angle enters the physics | Chunked moves, backlash compensation, tight tolerance (0.08° rotators, 5 µm stage), up to 3 verify-and-retry rounds |
| **Fast** | The thousands of routine moves inside a chip map | One direct absolute move, then readback verification with **one** corrective retry at 0.5° tolerance, then bounded recovery |

The fast path exists because the arithmetic is brutal: 100 pixels × 9 HWP
angles × 3 analyser points is roughly 3 000 rotator moves. At the ~2 s that the
accurate path costs per move, that is over an hour of pure motion before a
single measurement is taken. The fast path cuts that dramatically **without**
giving up verification: every fast move is still read back, and a move that
cannot be verified stops the run rather than silently corrupting an angle.

| Flag | Effect |
| --- | --- |
| `--conservative-rotator-moves` | Force the accurate, boundary-safe chunked path everywhere. |
| `--no-verify-fast-rotator-moves` | Drop readback verification. **Motion tests only**, never for a real measurement. |
| `--rotator-verify-tol` | The fast-path tolerance in degrees (default 0.5). |
| `--rotator-velocity-pct` | Elliptec velocity as a percentage of maximum (default 50, clamped to the range 25 to 100). |

---

## 2. The Elliptec ELL14 rotators

Driver: [`pockels/elliptec_serial.py`](../../pockels/elliptec_serial.py),
class `ElliptecRotator`. Three units share the optical path: the half-wave
plate (COM5, address 1), the quarter-wave plate (COM4, address 2) and the
analyser (COM8, address 2).

### 2.1 The wire protocol

Thorlabs Elliptec devices speak a simple ASCII-hex protocol over 9600 8N1
serial. Several devices can share one bus, distinguished by a single-character
address (`0` to `F`).

```text
Packet out:   <addr><2-char command><hex payload>\n
Packet in:    <addr><2-char reply><hex data>\r\n
```

The reply is parsed as three fields: one address character, a two-character
reply type and the remainder as a big-endian hex integer. A mismatched address
raises immediately: on a shared bus, reading another device's answer is worse
than reading nothing.

| Command | Meaning | Reply |
| --- | --- | --- |
| `gs` | Get status | `GS` + status code |
| `gp` | Get position | `PO` + 32-bit two's-complement encoder count |
| `ho` + 1 byte | Home (`0` = CW, `1` = CCW) | status |
| `mr` + 4 bytes | **Move relative** by a signed encoder delta | `GS` (status) or `PO` (position) |
| `sv` + 2 hex digits | Set velocity, 1 to 100 % | status |

The status codes, in index order, are the module-level `RESPONSES` list:

<details>
<summary>ELL14 status codes (index → meaning)</summary>

| Code | Meaning | Code | Meaning |
| --- | --- | --- | --- |
| 0 | `ok` | 7 | `initialization error` |
| 1 | `communication timeout` | 8 | `thermal error` |
| 2 | `mechanical timeout` | 9 | `busy` |
| 3 | `command error` | 10 | `sensor error` |
| 4 | `value out of range` | 11 | `motor error` |
| 5 | `module isolated` | 12 | `out of range` |
| 6 | `module out of isolation` | 13 | `overcurrent` |

Anything beyond index 13 is reported verbatim as `Unknown status N`.

</details>

`query()` wraps every exchange: it flushes the input buffer, writes the packet,
reads until `\r\n`, and retries (default 2 attempts, 0.15 s apart, flushing the
buffer between) on timeout, parse failure or `serial.SerialException`.
`move_by()` adds its own loop of up to 3 attempts, 0.3 s apart, treating any
non-`ok` `GS` reply as a retryable error.

### 2.2 Encoder arithmetic and the sign convention

```python
COUNTS_PER_REVOLUTION = 143360        # = 398.222 counts per degree
```

A raw `gp` reply is decoded from 32-bit two's complement and converted through
the software offset set by `tare()`:

```python
angle_unwrapped = -360 * (position + offset) / COUNTS_PER_REVOLUTION
angle           = angle_unwrapped % 360
```

> **Note: that minus sign is load-bearing.**
> The Elliptec protocol counts **clockwise-positive**; the software (and all of
> the optical convention in this instrument) is **counter-clockwise-positive**.
> Every conversion in both directions carries the negation. Relative moves
> invert identically:
> ```python
> delta_counts = -round(degrees * COUNTS_PER_REVOLUTION / 360)
> data = to_twos_complement(delta_counts).to_bytes(4, 'big')
> ```
> If you ever see a response whose angular dependence is mirrored about
> $\theta = 0$, this sign is the first thing to check.

At 398.222 counts per degree, one encoder count is about $0.0025^\circ$, well
below the ELL14's own $\pm 0.05^\circ$ specification, so the encoder is never
the limiting term.

### 2.3 There is no absolute-move command

The device only does **relative** moves. `set_angle()` therefore has to
synthesise an absolute move:

1. Read the current angle (`gp`).
2. Compute the shortest signed delta, wrapped into $(-180^\circ, +180^\circ]$.
3. Issue `move_by(delta)`.
4. Sleep `settle_s`.
5. Read back the achieved angle and return it.

**Two implications follow, and they shape the entire design of §3.** First,
every absolute move costs at least one extra round trip. Second, and far more
importantly, *an absolute move is only ever as good as the readback that
preceded it*. If step 1 returns a stale or wrong position, the computed delta
is wrong and the motor lands somewhere the software believes is correct. That
is exactly why readback **verification after the move** is not optional in this
system.

### 2.4 Built-in sensor/limit/range fault recovery

`set_angle()` catches exceptions whose message contains `sensor`, `limit` or
`range` and responds with a re-reference rather than a crash:

```text
home(direction=0, settle_s=2.5)  →  tare()  →
recompute the delta from the new position  →  retry the move
```

This is hard-won behaviour. ELL14 units occasionally throw sensor or limit
faults part-way through a multi-hour campaign. These are transients, not
mis-commands, and the correct response is to re-reference the mechanism and
carry on. Aborting a seven-hour run at pixel 60 because one rotator sneezed is
strictly worse than homing, taring and continuing, because the optical
calibration is expressed relative to the polariser frame and survives a
re-tare. Any *other* exception is re-raised unchanged.

### 2.5 `tare()`, and why arbitrary encoder zeros do not matter

```python
def tare(self):
    self._offset = -self._position    # "wherever I am now is 0 degrees"
```

The ELL14's mechanical home index is **not** aligned with the waveplate's fast
axis, and the offset between them is arbitrary and different for every unit.
`return_home()` = `home()` + `tare()` therefore gives a repeatable *software*
zero, and nothing more.

That is enough, because the optical calibration expresses every angle relative
to the polariser frame, so the arbitrary mechanical zero cancels out of every
physical quantity. See
[The Null-Slope Sénarmont Readout](../physics/03-senarmont-readout.md) for the
frame algebra.

> **Warning**
> If you supply exact **raw** angles (the manual peak scout), the software
> forces `--no-rotator-home` so those coordinates are not re-tared out from
> under you. After a transport recovery, `FastMapHardwareRecovery` writes the
> saved `_offset` back into each reopened rotator for exactly the same reason.

### 2.6 Velocity and the 25 % floor

```python
rot.set_velocity(50)     # percent of maximum, sent as two hex digits via 'sv'
```

The device powers up at 100 %. The fast map uses **50 %**, and the value is
clamped to the range **25 to 100 %**. The floor is not arbitrary: below roughly
25 % the ELL14 can stall on reversal, which produces exactly the kind of silent
mis-positioning the verification layer exists to catch. Lower speed reduces
inertial overshoot and makes the motion less jarring, at the cost of a
proportionally longer physical move.

---

## 3. Rotator move strategies

### 3.1 The accurate path: `safe_move_abs()`

In [`POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py):

```text
target          = target_deg mod 360
backlash_target = (target − 0.3°) mod 360            # ROTATOR_BACKLASH_DEG

1. Move to backlash_target in chunks of ≤ 4°         # MAX_CHUNK_DEG
      (each chunk is its own set_angle, 15 ms apart)
2. Final approach directly to target                 # always from below
3. Read back;  err = wrapped |actual − target|
4. If err > 0.08° and tries < 3:  go to 1            # STEP_VERIFY_TOL_DEG / _RETRY
5. On a sensor/limit/range exception: stop, home, tare, retry once
```

| Constant | Value | Meaning |
| --- | --- | --- |
| `MOVE_SETTLE_S` | 0.28 s | Settle after each `set_angle` |
| `MAX_CHUNK_DEG` | 4.0° | Maximum single commanded move |
| `ROTATOR_BACKLASH_DEG` | 0.3° | Deliberate undershoot |
| `STEP_VERIFY_TOL_DEG` | 0.08° | Accept threshold (ELL14 spec is $\pm 0.05^\circ$) |
| `STEP_VERIFY_RETRY` | 3 | Verify-and-retry rounds |

Two ideas are doing all the work here.

**Chunking.** Long single moves are where the ELL14 is most likely to overshoot
or throw a mechanical-timeout fault. Splitting the travel into segments of 4°
or less keeps every individual command inside the well-behaved regime, at the
cost of a few extra round trips.

**Backlash compensation.** Gear backlash means the achieved position depends on
the direction you arrived from. By *always* undershooting by 0.3° and then
making the final approach in the positive direction, every move loads the gear
train identically. The residual error becomes **systematic rather than
random**, and a systematic error that is identical at every angle cancels out
of every angular difference the physics actually uses.

An optional `MotionLogger` records every move (target, actual, attempt number,
whether backlash was applied, settle time and free-text notes) to CSV for
post-hoc analysis. It defaults to `None`, so it costs nothing when unused.

### 3.2 The fast path: `fast_rotator_move_or_fallback()`

In [`pockels_fast_map_gui.py`](../../pockels/pockels_fast_map_gui.py):

```text
1. rotator_move_needs_boundary_safe_path(rot, target)?
      → if the shortest path would cross 0°/360°, use the chunked safe path
2. Otherwise: one direct set_angle(target), read_back=False
3. If verify: rotator_verified_readback()
      take ANGLE_READBACK_SAMPLES = 3 readings, ANGLE_READBACK_DELAY_S = 0.05 s apart
      require the CONSECUTIVE FINAL samples inside tol_deg (default 0.5°)
4. If outside tolerance: ONE corrective move
      (boundary-safe if the target needs it, else direct)
5. Still outside: bounded recovery retries
      ANGLE_RECOVERY_RETRIES = 5, ANGLE_RECOVERY_PAUSE_S = 0.35 s
      each retry uses the chunked safe-wrap path
6. Still outside: raise CriticalAngleError  →  the run stops
```

The function returns a dictionary of `target_deg`, `actual_deg`, `error_deg`,
`corrected`, `fallback_safe_move`, `boundary_safe_move`, `recovery_attempts`,
`readback_samples`, `readback_ok_samples` and `ok`. Those fields are written
straight into the measurement CSV. Every row therefore carries the evidence
that its own angles were verified.

> **Why "consecutive final" and not "best of N"?**
> An earlier version took the *closest* of several samples. That is subtly
> wrong: it will certify a motor that happened to read correctly once and then
> drifted, or that lost telemetry immediately afterwards, because a single good
> sample anywhere in the window is enough to pass. Requiring the **last**
> samples to agree tolerates one stale reading immediately after a move,
> which the ELL14 does genuinely produce, without ever accepting a bad
> landing. In `rotator_verified_readback()` the counter is reset to zero on any
> out-of-tolerance or non-finite sample, so only an unbroken run of good final
> reads can satisfy it.

The pass criterion is `readback_ok_samples >= min(2, ANGLE_READBACK_SAMPLES)`,
i.e. two consecutive good final reads at the default of three samples.

A skip request (`SkipPixelRequested`) is re-raised untouched at every stage, and
every driver exception is first passed through
`raise_if_fatal_hardware_transport_error()`, so a dead USB link escalates to
hardware recovery rather than being mistaken for a stuck motor.

### 3.3 The 0°/360° boundary

The device wraps at 360°, and a "shortest path" that crosses the boundary can
trigger a range fault. `rotator_move_needs_boundary_safe_path()` reads the
current angle, computes the shortest signed delta and checks whether
`current + delta` leaves the interval $[0, 360]$. If it does, the move is routed
through the chunked safe path, which walks *around* the boundary rather than
across it. If the readback is unavailable the function returns `False`, because
an unknown position is not a reason to take the slow path; it is a reason for
the verification layer to catch the result.

The analyser is the usual victim, because a null frequently sits within a
fraction of a degree of 360°, where the same physical position can report
359.99, 360.00 or 0.00 on successive reads.

### 3.4 Verifying an optimiser's landing: `verify_renull_landing()`

Null searches take many small moves and often work from cached positions, so
the optimiser's *final* landing is the one position nobody has explicitly
verified. `verify_renull_landing()` closes that gap: for the QWP and then the
analyser it re-reads the position, and if either has missed, it issues a
corrective move through the accurate path and calls `require_verified_move()`,
which raises if the correction still fails.

The important part is the ordering. **The null power is read only after the
verified landing**, so a correction can never reuse an optical reading that was
taken at the wrong angle. Without this, a re-null could report a beautiful
14 mV null that belongs to an analyser position the run never actually used.

---

## 4. The Thorlabs XY stage

Driver and constants: [`stage_calibration.py`](../../pockels/stage_calibration.py).
Two Thorlabs KCube steppers, serials **26006987** (X) and **26007025** (Y),
25 mm travel each.

### 4.1 The driver stack, and why Windows is required

```python
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.KCube.StepperMotorCLI.dll")
```

`pythonnet` bridges Python to the .NET CLI, and `clr_loader.get_netfx()`
selects the **.NET Framework** runtime specifically. Thorlabs ships Kinesis as
Windows .NET assemblies with no cross-platform equivalent, so **the measurement
must run on Windows.** Everything else in the package, including both
pure-analysis modules and the entire test suite, runs anywhere.

`initialize_device()` performs the full bring-up sequence, and every step of it
matters:

```text
Connect(serial)                          →  0.5 s
WaitForSettingsInitialized(10000)        →  moves fail silently without this
GetDeviceInfo()                          →  log the description
StartPolling(50)                         →  50 ms position polling
EnableDevice()                           →  1.0 s for the enable to take effect
LoadMotorConfiguration(..., UseFileSettings)
config.DeviceSettingsName = "MTS25-Z8"   →  the actual actuator
SetVelocityParams(MaxVelocity=2.0, Acceleration=2.0)
set_velocity_mode(device)                →  front-panel wheel to VELOCITY, not jog
```

### 4.2 Coordinate frames

```python
STAGE_TRAVEL_MM = 25.0
STAGE_CENTER_MM = PyDecimal('12.5')

def motor_to_global(m):  return m - 12.5      # global (0, 0) = stage centre
def global_to_motor(g):  return g + 12.5
```

Motor coordinates run 0 … 25 mm, as the controller reports them. Global
coordinates run −12.5 … +12.5 mm with the origin at the stage centre, which is
where the chip is nominally mounted. The 10 × 10 pixel grid spans the full
travel (`build_grid()`), i.e. a 2.5 mm nominal pitch, numbered 1…100 with the
row index `j` outer and the column index `i` inner.

Soft limits are $-0.3$ … $25.3$ mm (`STAGE_MIN_MM`, `STAGE_MAX_MM`); the
controller's own hard limits are 0 … 25 mm (`HARD_MIN_MM`, `HARD_MAX_MM`).
The difference between those two is the subject of §4.4.

### 4.3 Decimals, not floats

```python
def py_to_net_decimal(py_dec):
    return NetDecimal.Parse(str(py_dec))
```

Positions travel as decimals end-to-end. Binary floating point cannot represent
2.5 mm exactly in every intermediate operation, and a commanded 2.5000 mm that
arrives as 2.4999999999 is both ugly in the logs and capable of flipping a
boundary comparison. `getcontext().prec = 10` is set at import, and the
conversion goes through `str()` so the value never passes through a `double` on
the way into .NET.

### 4.4 Moving: `safe_move_to()`

```text
1. Clamp the target to the soft limits (−0.3 … 25.3 mm)
2. If the reverse limit switch is active:
      MoveContinuous(Forward), 0.3 s, Stop(0), 0.3 s      # nudge off the switch
3. If the target is outside the hard 0 … 25 mm range AND ALLOW_OVERTRAVEL:
      MoveTo(boundary, 60000)                # reach 0 or 25 first
      MoveContinuous(direction)              # then jog past it
      poll read_motor_pos() every 50 ms until reached, or 2 s timeout
      Stop(0); 0.2 s
4. Otherwise: MoveTo(NetDecimal(target), 60000 ms timeout)
5. Read back; warn if the error exceeds 50 µm
```

| Constant | Value |
| --- | --- |
| `ALLOW_OVERTRAVEL` | `True` |
| `OVERTRAVEL_TIMEOUT_S` | 2.0 s |
| `OVERTRAVEL_POLL_S` | 0.05 s |

> **Warning: the over-travel quirk.**
> The Kinesis `MoveTo` API **refuses** targets outside 0 to 25 mm. But
> `MoveJog`/`MoveContinuous` **do physically move** past those limits while
> *also* throwing an exception. The code therefore suppresses those exceptions
> deliberately and polls the real position instead of trusting the API.
>
> **It must never clamp the target into 0 to 25 mm.** Pixels near the edge of the
> chip genuinely sit slightly outside the nominal travel once the chip is
> mounted, and a clamp would quietly measure the wrong pixel: the stage would
> report success at 25.000 mm while the beam sat 300 µm away from the electrode
> gap. Suppressing an exception feels wrong; measuring the wrong pixel is
> worse.

### 4.5 Backlash: `move_to_motor_with_backlash()`

```python
undershoot = target_mm - BACKLASH_MM     # BACKLASH_MM = 0.02 mm = 20 µm
if HARD_MIN_MM <= target_mm <= HARD_MAX_MM and undershoot >= HARD_MIN_MM:
    safe_move_to(device, undershoot, label)
return safe_move_to(device, target_mm, label)
```

Same principle as the rotators: always approach from below, so the mechanics are
loaded identically every time and the residual error is systematic. The guard
clause matters: a backlash undershoot is skipped when it would itself fall
outside the hard range, because the over-travel path in §4.4 is much slower and
is not worth spending on a 20 µm approach move.

`line_scan()` and both golden-section searches do the same thing themselves at
the start of a sweep, undershooting the scan start by `BACKLASH_MM` before the
first point, and then scanning strictly in the positive direction.

### 4.6 Settling and averaging

`wait_for_settle()` requires both axes to stay within
`SETTLE_TOLERANCE_MM = 0.0005` mm (0.5 µm) for `SETTLE_TIME_S = 0.5` s before a
capture is accepted, with a 10 s overall timeout after which it returns the
current position regardless. `read_motor_pos_averaged()` then averages
`CAPTURE_AVERAGES = 10` reads spaced `CAPTURE_AVG_DELAY_S = 0.02` s apart.

### 4.7 Velocity modes

| Mode | Velocity | Acceleration | Used for |
| --- | --- | --- | --- |
| Precision | 0.5 mm/s | 0.5 mm/s² | Scanning and alignment |
| Coarse | 2.0 mm/s | 2.0 mm/s² | Long moves between pixels |

`set_velocity_mode()` also puts the KCube front-panel wheel into **velocity**
mode rather than jog mode, so that a nudge of the wheel during a run produces a
controlled crawl rather than a fixed jump. It falls back from `JoystickMode` to
`WheelMode` across Kinesis versions and, if both fail, prints an instruction to
press MODE on the controller rather than aborting.

---

## 5. Stage alignment algorithms

**The goal at every pixel:** find the $(x, y)$ that maximises transmitted power,
because that is where the beam is centred in the ~7 µm electrode gap. Before
any search runs, the analyser is rotated `BRIGHTEN_OFFSET_DEG` off the
through-sample null so the photodiode sees a bright peak rather than a dark
null. Hill-climbing on a null is hill-climbing on noise.

The full-auto chain tries the cheapest method first and falls back:

```text
hill-climb  →  fast-peak  →  golden-section  →  full line scan
  ~17 evals     ~10 to 15      ~12 s              exhaustive, ~60 s
```

| Toggle | Default | Effect |
| --- | --- | --- |
| `FULL_AUTO_USE_HILL_CLIMB` | `True` | Full-auto tries hill-climb first |
| `FULL_AUTO_USE_FAST` | `True` | Full-auto uses `fast_peak_align` as the next step |
| `HILL_CLIMB_USE_LOCK` | `True` | Enable the 2-D ring-lock phase |

### 5.1 Hill-climb with ring lock: the default first attempt

**Phase 1: the 2-D ring lock** (`_ring_probe`). Sample
`HILL_CLIMB_LOCK_N_POINTS = 8` points evenly spaced on a circle of radius
`HILL_CLIMB_LOCK_RADII_UM = [20.0, 80.0]` µm around the start position, trying
20 µm first. If any ring point reads at least
`HILL_CLIMB_LOCK_THRESHOLD = 1.15` × the centre reading, move there: you have
"locked on" to the bright region. If the 20 µm ring finds nothing, try 80 µm.

This phase exists to escape a specific failure: a local flat spot where both
$\pm 5$ µm 1-D neighbours read the same as the centre, because both are in the
off-peak region. A purely 1-D search reports "already at the peak" and stops.
An octagon at 20 µm sees the gradient that the 1-D probes cannot.

If **no** ring rises above the threshold, the code does not give up. It nudges
to the brightest point of the last ring probed, if that beats the centre, and
hill-climbs from there.

**Phase 2: the adaptive 1-D climb per axis** (`hill_climb_peak`), X first,
then Y from the X-refined position:

```text
1. Measure at start, start + 5 µm, start − 5 µm            (3 evals)
      HILL_CLIMB_INITIAL_STEP_UM = 5.0

2. If the centre is ≥ both neighbours, OR all three are within 0.5 %
   of each other (HILL_CLIMB_FLAT_V_TOL_PCT, the "flat top" case):
      parabolic fit through the 3 points → move to the vertex → verify
      if the vertex reads below 0.98 × the best probe, REVERT to the best probe
      → done, 4 evals

3. Otherwise climb toward whichever side read higher, multiplying the step by
   HILL_CLIMB_STEP_GROWTH = 1.6 each time, capped at
   HILL_CLIMB_STEP_CAP_UM = 50 µm

4. When a reading drops below the previous one you have crossed the peak:
      parabolic fit on the last 3 points → vertex → verify
      (same 0.98 × revert rule)

5. If the travel would leave ±HILL_CLIMB_MAX_RANGE_UM = 200 µm of the start,
   stop and return the best seen; the caller then falls back to a
   broader method

   (a hard safety cap of 25 evaluations also guarantees termination)
```

Typical cost: 1 centre + 8 ring + 4 on X + 4 on Y ≈ 17 measurements, or 4 + 4
when the pixel was already on the peak.

> **The 0.98 revert rule.** A parabolic vertex is an *extrapolation*. If the
> three points are noisy, or the profile is not locally parabolic, the vertex
> can land somewhere worse than the best point actually measured. The code
> therefore always **measures at the vertex** and, if that reading is below
> 0.98 × the best genuine probe, physically moves back to the best probe. The
> search can never end somewhere it has not measured, and it can never end
> somewhere worse than where it has already been. `_parabolic_vertex()` itself
> also rejects an ill-conditioned fit outright: collinear points, a
> convex-upward parabola (a minimum, not a maximum), or a vertex outside the
> bracket all return `None`.

`hill_climb_align()` finally checks how far each axis travelled. If either axis
moved more than 95 % of the allowed range (i.e. it was still climbing when it
ran out of room), that axis is redone with `fast_peak_search()` over a window
of $\pm 200$ µm about the original start.

### 5.2 Fast-peak: golden section plus parabolic refinement

`fast_peak_search()` runs a golden-section search only to a **coarse** 5 µm
tolerance (`ALIGN_FAST_TOL_UM`), then fits a parabola through the best three
evaluations to refine below 1 µm.

The rationale is convergence order. Golden section converges *linearly*, each
iteration narrowing the bracket by a factor $\varphi$, while parabolic
interpolation converges *quadratically* near a smooth peak. Running golden
section to a loose tolerance and then interpolating typically reaches sub-µm
precision in about 10 evaluations instead of about 16, roughly 2.5 to 3× faster
than plain golden section for the same final precision. It uses the fast
measurement settings: `ALIGN_FAST_SETTLE_S = 0.15` s and
`ALIGN_FAST_AVG_READS = 3`.

The bracketing points for the parabola are chosen as the nearest measured
positions on each side of the best point, and the same 0.98 revert rule applies:
if the refined position reads worse than 0.98 × the best golden-section point,
the search reverts and tags itself `GS-best (parabolic worse)`.

### 5.3 Golden-section search

`golden_section_search()` is the classic bracketing optimiser: maintain an
interval, probe at the two golden-ratio points, discard the worse outer third
each iteration, and stop when the interval is narrower than `tol_um`. It uses
the careful settings (`ALIGN_SETTLE_S = 0.5` s, `ALIGN_AVG_READS = 5`) and
records `positions`, `voltages` and the full bracket history for the diagnostic
plot. `golden_section_align()` alternates X and Y and auto-extends the range if
the peak lands at an edge. Robust but slower; this is what the chain falls back
to when hill-climb fails.

### 5.4 The line scan: the exhaustive fallback

`auto_align()` is scan-then-refine, and it makes no assumption of unimodality
at all:

| Phase | Range | Step | Settle | Averages |
| --- | --- | --- | --- | --- |
| Coarse | $\pm 200$ µm (`ALIGN_COARSE_RANGE_UM = 400`) | 5 µm | 0.5 s | 5 |
| Fine | $\pm 5$ µm (`ALIGN_FINE_RANGE_UM = 10`) | 0.5 µm | 0.5 s | 5 |

X coarse → move to the best X → Y coarse → X fine → Y fine → final position.
The scope is fixed at 1 V/div for the whole scan so that a range change cannot
distort the measured profile. It is slow (of order 60 s) but it will always
find the peak if one exists in range, and its full `scan_data` is saved for the
diagnostic plot.

### 5.5 CV-guided pre-positioning

With the camera available, `cv_guided_align()` can place the stage
approximately before any optical search runs, which lets the subsequent golden
section work over $\pm 50$ µm (`CV_GUIDED_RANGE_UM = 100`) instead of
$\pm 200$ µm.

1. Load the one-time pixel-to-stage calibration from
   `stage_calibration/pixel_stage_calib.json`, an affine matrix $M$ mapping
   camera-pixel displacement to the stage displacement that caused it, plus
   the beam position `p_beam` in camera pixels. It is produced by
   `calibrate_pixel_to_stage()`, which steps the stage by
   `ALIGN_CALIB_STEP_MM = 0.050` mm and watches the image move.
2. Grab a frame and run `cv_estimate_gap_position()`. The electrode structure
   is extracted by a blue-excess channel combination
   (`blue − 0.7·max(red, green)`, plus a weighted luma term), Gaussian-blurred,
   Otsu-thresholded, morphologically closed, and stripped of connected
   components smaller than 100 px. Within the dilated structure mask the
   intensity map (weighted 0.7 toward blue, the electrode colour signature) is
   smoothed with a 15 × 15 Gaussian so that the **peak region** is found rather
   than a single noisy pixel. The filter is sized for the expected
   `CV_GAP_WIDTH_UM = 7.0` µm gap through which the laser couples. A clear peak
   is one that exceeds 1.3 × the mean inside the mask; it is then refined by a
   weighted centroid over a 15 px radius. If there is no clear peak, the code
   falls back to the geometric centroid of the largest contour.
3. Compute `correction_mm = M @ (p_beam − gap_px)` and apply it.

> **The 0.5 mm implausibility rejection.** If the computed correction exceeds
> `CV_MAX_CORRECTION_MM = 0.5` mm, it is **rejected** and the code falls back
> to a plain golden-section search. The pixel pitch is 2.5 mm, so a correction
> approaching 0.5 mm means the vision system has almost certainly locked onto
> the wrong feature: a neighbouring electrode, a scratch, a reflection.
> Acting on it would move the stage confidently to the wrong pixel, which is
> far worse than spending an extra 30 s on a blind search. The same fallback
> applies whenever the calibration file is missing, the camera is unavailable,
> or no gap is detected at all.

Every CV-guided attempt writes an annotated diagnostic image
(`pixNNN_cv_guide.png`) showing the beam crosshair, the detected gap circle and
the correction vector.

---

## 6. Measurement hygiene during a search

Four rules apply to every alignment search, and all four are there because
violating them produces a plausible-looking peak that is not a peak.

- **The live-view camera's scope polling is paused** (`pause_scope_reads()`)
  for the duration of the search, synchronously: the call blocks until any
  in-flight live-view read has completed. Without this, the alignment thread
  and the viewer collide on the same VISA session. All scope I/O in
  `ScopeDetector` is additionally serialised through an `RLock`.
- **Auto-ranging is disabled** (`auto_scale=False`) and the scope is pinned
  with `_set_vdiv()` before the first point. A range change midway through a
  scan rescales the readings and can masquerade as a peak or hide a real one.
  Hill-climb and line-scan both fix 1 V/div; null reads use the much finer
  `NULL_SCOPE_START_VDIV = 10 mV/div`.
- **Every point is an average**, not a single read:
  `ALIGN_FAST_AVG_READS = 3` on the fast paths, `ALIGN_AVG_READS = 5` on the
  careful ones. `ScopeDetector.read_averaged()` additionally performs
  median-absolute-deviation outlier rejection at 2.5 × MAD before averaging,
  keeping the raw array if fewer than three samples survive.
- **Every search writes a diagnostic log and plot** into the run folder.
  `save_alignment_log()` and `plot_alignment_diagnostic()` produce a per-pixel
  CSV of every probe and a picture of the profile the optimiser actually saw.
  When a pixel's data looks wrong six months later, this is how you find out
  whether the beam was ever on the gap.

---

**Next:** [Instrument Control and Timing](instrument-control.md) for what
happens once the motors have stopped. For the instruments themselves (models,
addresses, gains) see [Instruments](../experiment/instruments.md); for what to
do when a motor will not cooperate, see
[Troubleshooting](../guide/troubleshooting.md).

---

<div align="center">

[← Architecture](architecture.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Instrument control →](instrument-control.md)

</div>
