# Troubleshooting

Symptom → likely cause → what to do, organised by where the problem shows up.
Most entries end by telling you which file or flag proves the diagnosis, so you
can confirm rather than guess.

If you are new here, read [Quick Start](quickstart.md) first — several "faults"
turn out to be a step that has not been done yet.

---

## Before anything else, three questions

1. **Are you running Windows Python, not WSL?** The stage and the laser need the
   Kinesis .NET assemblies. See
   [Installation §1](installation.md#1-windows-is-required-for-measurement).
2. **Is the run simulated?** Check `"simulated": true` in
   `lockin_configuration.json`. Simulated data looks entirely plausible and is
   fiction.
3. **Does `--list-serial-ports` show every instrument?**

```bat
python pockels\pockels_fast_map_gui.py --cli --list-serial-ports
```

## Contents

1. [Startup and connection](#1-startup-and-connection)
2. [Motion](#2-motion)
3. [Optical and nulling](#3-optical-and-nulling)
4. [Signal problems](#4-signal-problems)
5. [Electrical](#5-electrical)
6. [Hysteresis](#6-hysteresis)
7. [Run management](#7-run-management)
8. [GUI](#8-gui)
9. [Data and analysis](#9-data-and-analysis)
10. [Diagnostic recipes](#10-diagnostic-recipes)

---

## 1. Startup and connection

### "Lock-in connection failed"

The worker prints the resource, the exception, and a list of every VISA resource
currently visible.

| Cause | Fix |
| --- | --- |
| DSP7230 off or booting | Power on, wait, retry |
| Network unreachable | `ping 169.254.150.230`. The link-local address needs the PC's adapter on the same subnet with no conflicting DHCP lease |
| Cable or switch | Check the Ethernet link LEDs |
| Another program holds the socket | Close any other VISA client, including the standalone [`pockels/Lock_In_Mag_Phase_Track.py`](../../pockels/Lock_In_Mag_Phase_Track.py) viewer |
| Firewall | Allow the Python executable through Windows Firewall on the private network |

Do **not** work around it with `--allow-fake-lockin` for real data.

### "SMU auto-detect did not find an SMU4201"

| Cause | Fix |
| --- | --- |
| Not powered or not enumerated | Check Device Manager for a new COM port |
| Port held by another process | Close other terminals, or a previous crashed run |
| Identity changed | Pass `--smu-port COM13` explicitly |

### Arduino: "no boot banner seen"

The wrapper waits 3 s for the bootloader plus up to 10 s for the banner
`"Signal Matrix Ready"`, then continues with a warning.

| Cause | Fix |
| --- | --- |
| Slow CH340 clone bootloader | Usually harmless — it continues and works |
| Board wedged | Unplug and replug the Nano |
| Wrong port | `--arduino-port COM12` |
| Missing CH340 driver | Install it |

Verify by hand:

```bat
python pockels\arduino_switch_matrix.py --port COM12
> 46          REM route pixel 46
> status
> 0           REM all off
```

### "Fast-map switch mapping is stale"

The GUI asserts at import that pixel 46 → switch 5 and pixel 85 → switch 62.
Somebody has edited one of the three copies of the map
(`arduino_switch_matrix.PIXEL_TO_ELECTRICAL_SWITCH`,
`stage_calibration.PIXEL_TO_PIN`, and the firmware array in
[`firmware/switch_matrix/switch_matrix.ino`](../../firmware/switch_matrix/switch_matrix.ino))
without the others.

**Fix all three.** This guard exists because a stale map silently applies
voltage to the wrong pixel. See [Switch matrix](../experiment/switch-matrix.md).

### Stage / Kinesis errors

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Cannot find Thorlabs Kinesis` | not installed at `C:\Program Files\Thorlabs\Kinesis` | install Kinesis, or set `THORLABS_KINESIS_PATH` for the laser |
| .NET / `clr` import errors | running under WSL or Linux | run on Windows Python |
| Device not found by serial | KCube off, or wrong serial | check power; verify `STAGE_SERIAL_X` / `STAGE_SERIAL_Y` |
| Connects but will not move | motor not enabled, or sitting on a limit switch | re-home; `safe_move_to` nudges off the reverse limit automatically |

### Rotator errors on connect

| Symptom | Cause | Fix |
| --- | --- | --- |
| `SerialException` on COM4/5/8 | port in use, or renumbered | close other software; re-check `--list-serial-ports`; edit `PORT_QWP` / `PORT_HWP` / `PORT_ANL` in [`pockels/POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py) |
| `Address mismatch` | wrong bus address | check `ADDR_HWP` / `ADDR_QWP` / `ADDR_ANL` (1, 2, 2) |
| Timeouts on every query | wrong baud, bad cable, unpowered hub | 9600 8N1; check the hub |

---

## 2. Motion

### Rotator sensor / limit / range faults

Usually **self-healing**. `ElliptecRotator.set_angle()` in
[`pockels/elliptec_serial.py`](../../pockels/elliptec_serial.py) catches faults
whose message mentions `sensor`, `limit` or `range`, homes, re-tares, recomputes
the move from the new position, and retries. You will see
`-> fault (...), homing to recover...` in the terminal.

If it keeps happening on one unit:

- Check for mechanical obstruction or a snagged cable.
- Lower the velocity: `--rotator-velocity-pct 30`.
- Force the conservative path: `--conservative-rotator-moves`.

### "Angle verification failed" / run stops with `CriticalAngleError`

A verified move could not land within tolerance after a corrective move and the
bounded recovery retries. The run **stops on purpose** — a wrong angle silently
corrupts data.

| Cause | Fix |
| --- | --- |
| Mechanical binding | inspect the mount |
| Serial errors during readback | check the cable and hub; re-seat |
| Tolerance too tight for a fast move | raise `--rotator-verify-tol` to 1.0 — and record that you did |
| Boundary crossing | already handled automatically; if you disabled it, re-enable fast-move verification |

### Stage does not reach the target

| Symptom | Cause | Fix |
| --- | --- | --- |
| `WARN: target=… actual=… err=…µm` (> 50 µm) | limit switch, obstruction, or an over-travel jog that timed out | re-home the axis; check the sample mount |
| Refuses targets outside 0–25 mm | `ALLOW_OVERTRAVEL` disabled | it is `True` by default; the code jogs past the limit and suppresses the expected exception. **Never clamp the target** |
| Hunting or oscillating | velocity or acceleration too high for the load | it already drops to 0.5 mm/s for scanning; check for a loose mount |

### Alignment fails or lands on the wrong spot

| Symptom | Cause | Fix |
| --- | --- | --- |
| "no signal" / below `--min-signal-mv` | analyser not brightened, laser off, or badly off the pixel | check the **Laser** LED; confirm `--stage-brighten-offset 45`; check the camera view |
| Finds a peak that is not the gap | a neighbouring feature is brighter | use the camera/CV path; run a stage calibration for the chip; reduce `HILL_CLIMB_MAX_RANGE_UM` |
| Very slow | falling through to the exhaustive line scan every time | the start position is too far off — do a stage calibration, or supply `--stage-cal` |
| Different answer every time | the peak is genuinely flat, or the detector is noisy | increase `ALIGN_AVG_READS`; check laser stability |

More on the search strategy in [Motion control](../software/motion-control.md).

---

## 3. Optical and nulling

### Null leakage high everywhere (`p_null_mV` ≫ 14.5)

This is the most consequential failure mode — a bad null undermines the whole
measurement, because the readout is defined relative to it.

| Cause | Check | Fix |
| --- | --- | --- |
| Optical calibration stale | when was `sample_calibration.json` written? | re-run the **Guided Sample-In** calibration |
| Substrate table stale, or from a different sample | `run_config.json` → substrate path | rebuild with `Substrate = do` |
| QWP not compensating | is the fitted retardance near 90°? | recalibrate the QWP; check it is the right waveplate for 1550 nm |
| Sample moved or remounted | camera view | re-run the optical calibration |
| Beam clipped or misaligned | is the DC transmission low overall? | realign the beam line |
| Polariser or analyser contaminated | inspect | clean |

### Null high at a few pixels only

Expected. Local composition, thickness, strain or domain state changes the
static birefringence. `adaptive-renull` (the default) handles it, and the
`high_null_leakage` / `high_null_after_adaptive` / `high_null_continued` flags
tell you which pixels needed help. If it happens at *most* pixels, consider
`--renull-mode fast-renull` — slower, but it re-optimises everywhere.

### `calibration_null_hard_limit_exceeded`

A calibration-pixel null stayed above the hard limit. The pixel is still
measured, but with explicit untrusted flags, and it is **excluded** from trusted
seeds, learned readouts, peak selection, HWP fitting and physics analysis.

**Fix the root cause: choose a better calibration pixel.** A bad calibration
pixel poisons the seeds for the whole chip. See
[Operator Manual §2](operating.md#choosing-the-calibration-pixel).

### QWP retardance far from 90°

Blocks $r_\mathrm{eff}$ with `bto_qwp_retardance_invalid`. Either the QWP is not
a quarter-wave plate at 1550 nm, or the calibration sweep was mis-fitted.

> **Note** The expected visibility in the parallel-analyser QWP sweep is
> **1/3**, not 1. The relation is
> $\delta = 2\arccos\sqrt{I_\mathrm{min}/I_\mathrm{max}}$. Fitting it as if the
> visibility should be 1 produces a badly wrong retardance. See
> [Sénarmont readout](../physics/03-senarmont-readout.md).

---

## 4. Signal problems

### No signal at all

Walk the chain in order:

| ✓ | Check |
| --- | --- |
| ☐ | Laser LED lit in the GUI? Power setpoint applied? |
| ☐ | Does the scope show a sensible DC level when the analyser is brightened? |
| ☐ | Is the Arduino routed to the right channel? (`status` in the CLI) |
| ☐ | Is the SMU output on and at +40 V? (DC LED) |
| ☐ | Is function generator CH1 on during the read? (AC LED) |
| ☐ | Is CH2 (reference) on and is the lock-in locked? (`reference_locked` in `lockin_configuration.json`) |
| ☐ | Is the lock-in range sensible? 200 µV full scale for a µV signal |
| ☐ | Is the probe or wire bond to this pixel actually connected? (the SMU current tells you) |

### `plus_minus_same_sign` — the ±45° responses do not oppose

**You are measuring electrical pickup, not light.** The electro-optic signal
*must* change sign between the two slope points, because the Malus slope changes
sign; pickup does not.

| Fix | Detail |
| --- | --- |
| Improve shielding | route the detector cable away from the drive cable and the bias tee |
| Improve grounding | single-point ground; avoid loops between the scope, lock-in and generator |
| Check the null background | the null-point row measures the contaminating phasor directly — `s9_analyser_independent_fraction` quantifies it |
| Reduce the drive | try a lower Vpp and see whether the "signal" scales with drive but not with angle |

> **Note** The software deliberately calls this the **analyser-independent
> term**, not "pickup": genuine total-transmission modulation, laser amplitude
> modulation and detector terms also land there.

### `lockin_overload` / `lockin_range_exceeded`

| Cause | Fix |
| --- | --- |
| Signal exceeds 200 µV full scale | raise the range: `--lockin-sensitivity-index 17` (500 µV) |
| Detector saturated | reduce laser power |
| Input overload, not output | too much *total* signal at the input — usually a badly leaking null. Fix the null; a sensitivity change cannot correct an input overload |

Rows at ≥ 85 % of full scale are warned (`lockin_range_near_fullscale`); ≥ 98 %
or overload are invalid and excluded from analysis automatically.

### Noisy or irreproducible lock-in readings

| Cause | Fix |
| --- | --- |
| Settle too short | it is auto-raised to 2 × TC × filter order; check the terminal for the `[LOCKIN] Increasing settle` message |
| Samples correlated | the read delay is auto-raised to the same value; do not force it lower |
| Genuine low SNR | increase `--lockin-avg-readings`, or the time constant (`--lockin-tc-index 15` = 1 s) |
| Mechanical vibration | check the table floats; avoid working on the bench during a run |
| Air currents in the beam path | close the enclosure |
| Laser power drift | check the KLS setpoint; let it warm up |

### Reference unlocked

`reference_locked: false` in `lockin_configuration.json`, or a warning in the
terminal. The DSP7230's reported reference frequency disagrees with the
commanded 30 kHz.

| Cause | Fix |
| --- | --- |
| CH2 output off | it should be configured on at startup — check the generator front panel |
| Cable | check CH2 → lock-in REF IN |
| Wrong reference source | `--lockin-reference-source external-analog` for the 0.5 Vpp sine; `external-ttl` only for a TTL reference |
| Amplitude too small for the input | 0.5 Vpp is the designed value |

> **Warning** `--allow-unlocked-reference` exists but is a **diagnostic override
> only**. An unlocked reference means the phase — and therefore every sign in
> the dataset — is meaningless.

---

## 5. Electrical

### SMU compliance trips

| Cause | Fix |
| --- | --- |
| Genuine leakage or a shorted pixel | a real result — the row is excluded from metrics and flagged |
| Capacitive inrush | already mitigated by the 5 V ramp chunks and the 50 V/ms slew limit; do not increase the ramp step |
| Probe touching something | inspect |

Compliance-tripped points are excluded from loop metrics everywhere.

### "terminal voltage readback missing / out of tolerance"

The point is rejected **before** the AC drive turns on. That is good: it means
the device is not seeing the voltage you think it is.

| Cause | Fix |
| --- | --- |
| Open contact or lifted probe | re-land the probes |
| Switch matrix not routed | check the matrix state and the channel number |
| SMU telemetry unavailable | check the serial link; `MEASure:SECondary:LIVEdata?` must return |
| Genuine loading at high current | check the compliance setting |

### Function generator: execution error −80

The TGF3162 detected an output-voltage overload and **disabled its own output**
for safety. Check the load and the cabling on CH1 before retrying.

### A pixel appears dead

Distinguish the causes:

| Evidence | Interpretation |
| --- | --- |
| SMU current ≈ 0 at ±40 V | open circuit — bad contact or broken track |
| SMU current at compliance | short — damaged pixel |
| Current normal, no optical response | electrically fine, no (or cancelled) electro-optic response — could be genuine, or a badly multi-domain region |
| Low DC transmission too | an optical problem: alignment, scattering, or damage |

---

## 6. Hysteresis

### The loop looks like nothing (a straight line)

| Cause | Fix |
| --- | --- |
| $V_c$ outside ±40 V | classified `partial_loop_unresolved`. 40 V is a **hard** ceiling — you cannot raise it |
| Genuinely paraelectric-like | a real result: `linear_no_hysteresis` |
| Not poled, or not switching | try a domain reset first, and start from 0 V |
| Wrong operating point | the loop uses the chip-sweep peak conditions — check the map is good at that pixel |

### `invalid_projection` (quadrature fraction > 0.5)

The phasor does not live on a single axis, so the signed projection is not
meaningful.

| Cause | Fix |
| --- | --- |
| Pickup contamination | see [§4](#4-signal-problems) |
| Thermal drift during the sweep | shorten the loop or stabilise the lab |
| Phase reference wrong | check `phase_reference_source` and `phase_reference_axis_delta_deg` in the metrics JSON |

### The loop is not closed

`loop_closure_V` large. Drift, fatigue, or insufficient dwell. Run 2 cycles and
inspect `cycle_to_cycle`.

### Loops differ between runs

Almost always the **dwell**. Loop shape is rate-dependent. Check
`Poling_dwell_s` in the `#` provenance header of both CSVs. Keep one dwell per
campaign.

### Hysteresis is taking forever

≈ 39 s/point × 45 points ≈ 29 min per loop is the design. To go faster you must
give something up:

| Lever | Cost |
| --- | --- |
| Reduce the dwell | changes the loop shape — only valid if you change it for the *whole* campaign |
| Uniform coarser grid (*Uniform hyst. override*) | fewer points near the coercive region, where the physics is |
| Fewer pixels | the right answer most of the time |
| Do **not** enable the adaptive fine grid to save time | it is *slower* — it adds a reconnaissance loop before the real one |

---

## 7. Run management

### The run stopped by itself

Check, in order:

1. The **Run Notifications** panel and the terminal tail.
2. `transport_recovery.jsonl` — was there a hardware dropout?
3. `automation_progress.json` — how far did it get?

Common stop causes: `CriticalAngleError` (a verified rotator move failed), an
unrecoverable transport failure with a finite `--transport-recovery-attempts`,
or a settings validation error.

### An instrument disappeared mid-run

By design the worker does **not** die. It saves the partial pixel, shuts the
outputs down, reconnects following USB identity, verifies everything, and
continues with the **next** pixel. The interrupted pixel is not replayed —
resume later to fill it in.

If it sits in recovery forever, the device is physically absent: check power and
cables. `--transport-recovery-attempts N` makes it give up after N tries.

> **Note** If this happens repeatedly, run the one-time USB stability setup —
> selective suspend is the usual culprit. See
> [Installation §4](installation.md#4-one-time-windows-usb-stability-setup).

### Resume skips a pixel I wanted redone

With *Skip completed pixels on resume* ON, a pixel is skipped only when its chip
sweep **and** every requested hysteresis artifact verify as complete. To force a
redo, either untick the box, or delete that pixel's folder — keep a copy first.

### "No chip-map pixels and no follow-up pixels were selected"

*Pixels* is empty or `none`, and every follow-up list is empty too. Set one.

---

## 8. GUI

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Tkinter is not available` | Python build without Tk (common in minimal conda environments, and universal in WSL) | use the standard Windows Python, or run headless with `--cli` |
| Window opens but nothing updates | the worker never started, or is writing to a different folder | check the terminal panel; check `Run folder:` in Run Status |
| Chip map stays blank | no completed points yet, or the summary CSV is not being written | wait for the first HWP block to finish |
| Camera panel shows a placeholder | camera disabled, OpenCV missing, or the device is in use | tick *Use camera/live view*; `pip install opencv-python`; close other camera apps |
| A plot panel shows an error box | a drawing exception was caught and displayed rather than crashing the GUI | the message names the panel; the run is unaffected |
| Map looks mirrored versus my own plot | the GUI draws `display_col = 10 − col` to match the camera view | see [Data Schema §12](../reference/data-schema.md#12-loading-the-data-in-python) |
| Settings panel is missing fields | advanced settings hidden | tick *Show advanced settings* |

---

## 9. Data and analysis

| Symptom | Cause | Fix |
| --- | --- | --- |
| `r_eff` is blank | by design — a gate failed | read `r_eff_status` and `missing_inputs` |
| `voltage_linearity_r_squared` blank or `unverified` | only one Vpp in the run | run `--voltages 1,3,5,9`, or the AC sweep follow-up |
| `angular_fit_r_squared` low | genuinely noisy, too few valid points, or contamination | check how many rows are certified at that pixel |
| `make_hysteresis_maps.py` finds no metrics | no loops in the run, or metrics not yet computed | check `dc_hysteresis/sweep/` exists; run with `--reanalyse` |
| Metrics changed after re-running | you changed a threshold, or `--reanalyse` recomputed from raw | expected; thresholds are module constants in [`pockels/pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) |
| `pandas` chokes on `dc_hysteresis.csv` | the provenance header lines start with `#` | `pd.read_csv(path, comment="#")` |
| An older CSV has no X/Y columns | historical files stored magnitude and phase only | the analysis reconstructs X/Y automatically — it is fully retroactive |
| Numbers look too good, too clean | the run was simulated | check `"simulated"` in `lockin_configuration.json` |

More on what the pipeline does with each file:
[Data pipeline](../software/data-pipeline.md).

---

## 10. Diagnostic recipes

### Prove the optics are working (no electronics involved)

1. Set the analyser to the null → the scope should read a few mV.
2. Rotate the analyser +90° → the scope should read a large, bright value.
3. The ratio is your extinction. More than 25 dB is healthy.

### Prove the drive reaches the chip

1. Route a pixel with the Arduino.
2. Set the SMU to +40 V and read the current — a plausible small leakage current
   means the circuit is closed.
3. Turn CH1 on at 9 Vpp and, ideally, look at the electrode voltage with a
   probe. This is also how you measure `bto_device_vpp_scale`.

### Prove the signal is optical, not pickup

1. At one HWP, compare +45° and −45°: they must **oppose in sign**.
2. Sweep the HWP: a real electro-optic response varies as $2\theta_i$ (signed)
   and $4\theta_i$ (magnitude). Pickup does not care about the HWP.
3. Block the beam: a real optical signal goes to zero; pickup does not.

### Prove the response is linear

Run the fixed-peak AC sweep at `1,3,5,7,9` Vpp and check
`voltage_linearity_r_squared` ≥ 0.98.

### Check the whole software stack without hardware

```bash
cd /path/to/PockelsMap
for f in tests/test_*.py; do python "$f" >/dev/null 2>&1 && echo "PASS $f" || echo "FAIL $f"; done
```

All 22 test files should pass. They import no drivers, so this works on any
machine — including WSL and Linux.

You can also exercise the whole control flow with no instruments at all:

```bat
python pockels\pockels_fast_map_gui.py --cli --pixels 1 --chip-id DEBUG ^
    --phase-substrate skip --simulate-lockin --allow-fake-smu --allow-no-arduino --yes
```

> **Warning** Anything that comes out of that is fiction. It is a control-flow
> test, not a measurement.

### Talk to one instrument by hand

```bat
REM Switch matrix
python pockels\arduino_switch_matrix.py --port COM12

REM SMU I-V sweep (edit the constants at the top of the file first)
python pockels\smu4201_iv_sweep.py

REM Live lock-in magnitude/phase viewer
python pockels\Lock_In_Mag_Phase_Track.py --resource "TCPIP0::169.254.150.230::50001::SOCKET"
```

> **Warning** Close these before starting a run — they hold the instrument
> sessions, and the worker will then fail to connect.

### Verbose motion and scope logging

```bat
python pockels\pockels_fast_map_gui.py --cli --show-range-changes --show-wrap-moves …
```

Shows the routine `[RANGE]` autorange messages and the successful analyser
`[WRAP-MOVE]` messages that are normally suppressed.

---

## Still stuck?

Five files tell you what actually happened:

1. **`run_config.json`** — exactly what the run was asked to do, including the
   pixel list in measurement order and every threshold.
2. **`lockin_configuration.json`** — what the lock-in was actually set to, and
   whether it was simulated.
3. **`transport_recovery.jsonl`** — whether hardware dropped out, when, and what
   was reconnected.
4. **The per-pixel `fast_map.csv` `quality_flags` column** — what the software
   already thought was wrong. It is usually right.
5. **`git log`** on this repository — what changed in the code, and when.

Then: [Data Schema](../reference/data-schema.md) for what each column means, and
[Glossary](../reference/glossary.md) for any term or symbol you have not met.

---

<div align="center">

[← Operator manual](operating.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [CLI reference →](../reference/cli.md)

</div>
