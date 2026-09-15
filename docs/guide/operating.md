# Operator Manual

The complete standard operating procedure for the **BTO GO!** fast-map system,
plus a reference entry for every control in the GUI. This is the page you keep
open while a campaign runs.

**Prerequisite:** work through [Quick Start](quickstart.md) at least once before
using this document as a reference.

## Contents

1. [Daily startup checklist](#1-daily-startup-checklist)
2. [The calibration chain: what to run and when](#2-the-calibration-chain-what-to-run-and-when)
3. [The measurement settings, field by field](#3-the-measurement-settings-field-by-field)
4. [Advanced settings, field by field](#4-advanced-settings-field-by-field)
5. [Running a chip map](#5-running-a-chip-map)
6. [Follow-up measurements](#6-follow-up-measurements)
7. [Monitoring a run](#7-monitoring-a-run)
8. [Stopping, skipping and resuming](#8-stopping-skipping-and-resuming)
9. [Running headless (CLI)](#9-running-headless-cli)
10. [Special modes](#10-special-modes)
11. [End-of-day shutdown](#11-end-of-day-shutdown)
12. [Campaign design guidance](#12-campaign-design-guidance)

---

## 1. Daily startup checklist

| ✓ | Step |
| --- | --- |
| ☐ | 1550 nm goggles on. Beam enclosure closed. |
| ☐ | Scope, function generator, SMU4201 and DSP7230 lock-in powered and warmed up (lock-in: ≥ 10 min). |
| ☐ | KCube stage controllers (X **26006987**, Y **26007025**) and the laser KCube powered. |
| ☐ | Elliptec rotator hubs powered. |
| ☐ | Arduino Nano switch matrix connected. |
| ☐ | PDA30B2 gain = **10 dB**, load = **Hi-Z**. |
| ☐ | Function generator: CH2 (the lock-in reference) will be driven to 0.5 Vpp @ 30 kHz by software, so just make sure it is connected to the lock-in REF IN. |
| ☐ | Lock-in reachable on the network: `ping 169.254.150.230`. |
| ☐ | `python pockels\pockels_fast_map_gui.py --cli --list-serial-ports` shows every expected device. |
| ☐ | Note the lab temperature in your notebook. Curie temperature and coercive field both move with it. |

---

## 2. The calibration chain: what to run and when

The system is **self-referencing**: every readout is defined relative to *that
pixel's own optical null*, so the arbitrary encoder zero of each rotator and the
pixel-to-pixel static birefringence cancel out. What you must establish is the
chain of references below.

| Stage | Produces | Run it when… | Typical cost |
| --- | --- | --- | --- |
| **A. Guided sample-in optical calibration**<br>*(GUI button, or [`pockels/sample_calibration.py`](../../pockels/sample_calibration.py))* | `sample_calibration.json`: analyser extinction/parallel angles, QWP fast-axis zero $\gamma_0$ and retardance, first sample-in null `(q_null, a_null, p_null)` | optics realigned, sample changed, waveplate swapped, or the null has drifted badly | ~20 min, needs you at the bench |
| **B. Substrate HWP null-seed table**<br>*(`Substrate = do` during a run)* | `substrate_calibration.json`: `(q_null, a_null, p_null)` for **every** HWP angle in the grid | after A, or when you change the HWP grid | ~20 min, unattended |
| **C. Stage pixel calibration**<br>*([`pockels/stage_calibration.py`](../../pockels/stage_calibration.py), optional)* | `pixel_positions.json`: the motor coordinates of each pixel's transmission peak | once per chip mounting; optional, because the map re-aligns each pixel anyway | ~1 h for 100 pixels |
| **D. Calibration pixel** *(automatic, first pixel of every run)* | per-HWP trusted null branch, plus the operating-point ("S9") certificate that later pixels inherit | every run, and it is automatic | ~8 min |

### When to reuse rather than redo

- **Always `load` the substrate table** unless the optics changed. Press *Find
  Latest Substrate Null Cal*.
- **Always `latest` for Optical cal** unless you have just run a new guided
  calibration, in which case press *Find Latest Sample Optical Cal*.
- **Stage calibration is optional.** The default *Auto-align each pixel* is ON
  and finds the local transmission peak from the nominal grid position, so a
  separate stage pass is only worth it if you want alignment diagnostics or the
  chip is badly skewed. See [Motion control](../software/motion-control.md).

### Choosing the calibration pixel

The **Calibration pixel** is the single most important choice you make. It must:

- be **inside your Pixels selection** (the software re-inserts it if missing),
- **null well**, meaning a low `p_null_mV`,
- give a **clear Pockels response**, strong enough that the operating-point
  certificate can be fitted.

A dead or pinned pixel as your calibration pixel poisons the seeds for the whole
chip: every later pixel inherits its null branch and its learned readout table.
Look at a previous run's `fast_map_all_pixels.csv`, sort by `max_lockin_mag_V`,
and pick a strong pixel with an empty `quality_flags` column.

> **Warning** If a calibration-pixel null exceeds the hard limit you will see
> `calibration_null_hard_limit_exceeded`. The pixel is still measured, but it is
> excluded from trusted seeds, learned readouts, peak selection, HWP fitting and
> the physics analysis. Do not push through it; pick a better pixel.

---

## 3. The measurement settings, field by field

These are the always-visible controls, in the order the GUI presents them.

### Measurement

| Field | Default | Meaning |
| --- | --- | --- |
| **Chip ID** | *(blank, required)* | Identifier for the chip. Goes into the run folder name (`YYYYMMDD_HHMMSS_<chip-id>_<label>`), into `run_config.json`, and into the `chip_id` column of the chip summary. A run **cannot start** without it. |
| **Run label (optional)** | blank | Free text appended after the Chip ID in the folder name. |
| **Pixels** | `1-6,11-16,21-26,31-37,41-48,51-100` (83 pixels) | Which pixels get the chip map. Accepts `all`, a single number, ranges and lists. `none` runs follow-ups only. |
| **HWP grid preset** | *Current calibrated default (9 points)* | Quick selector for the incident-polarisation grid; see below. |
| **Manual probing (no Arduino)** | off | Stage aligns to each pixel, then **pauses** so you can land probe needles by hand. Disables Arduino routing entirely. |
| **Grid clicks select** | `Chip map` | Which pixel list your clicks on the 10 × 10 map edit: the chip map, or the DC / AC / ANL follow-up lists. |

#### The HWP grid presets

| Preset | Grid |
| --- | --- |
| **Current calibrated default (9 points)** | 9 points centred on incident polarisation $\theta_i = 81.8688°$ (lab frame) = HWP raw 7.8951°, spaced 22.5° in $\theta_i$ (11.25° of physical HWP rotation), covering a full 180° of $\theta_i$. This centre is the manually verified high-response position from 2026-07-23. |
| **15 May 2026 span (9 interpolated points)** | Nine points spread across the HWP span used by run `fast_map_20260515_092806`, expressed in today's calibrated lab convention. |
| **15 May 2026 comparison (12 points)** | The exact 12 physical HWP positions of that run, for direct comparison. |
| **Custom / loaded settings** | Set automatically as soon as you edit any advanced HWP field, or when resume defaults are loaded. |

> **Why 9 points?** The signed electro-optic response goes as $\cos 2\theta_i$
> and its magnitude as $\cos 4\theta_i$. Nine points over 180° over-samples
> both, so the fit is well conditioned and the four-lobed magnitude pattern is
> unambiguous. See [Sénarmont readout](../physics/03-senarmont-readout.md) and
> [the angular-harmonics figure](../../assets/figures/angular_harmonics.png).

### Resume controls

| Button | Effect |
| --- | --- |
| **Resume Latest Run** | Points the run at the newest `pockels_fast_map/` folder and loads its settings. |
| **Choose Historical Run** | Same, but you pick the folder. |
| **Unmeasured Only** | Rewrites *Pixels* to only those pixels that do not yet have complete artifacts in the resumed run. |
| **Load Resume Defaults From Folder** (advanced) | Imports the settings stored in a run's `run_config.json` into the GUI. |

### Calibration buttons

| Button | Effect |
| --- | --- |
| **Find Latest Substrate Null Cal** | Sets `Substrate = load` and fills the path with the newest `substrate_calibration.json`. |
| **Find Latest Sample Optical Cal** | Fills *Optical cal* with the newest `sample_calibration.json`. |
| **Guided Sample-In Axis/Extinction Calibration** | Launches the 3-step wizard (§2A). |
| **Reload GDS Image** | Re-reads the chip layout reference image shown beside the map. |

### HWP Readout Calibration

| Field | Default | Meaning |
| --- | --- | --- |
| **Calibration pixel** | `1` | The pixel that performs the full per-HWP null calibration and readout certification. Must be in *Pixels*. |
| **Full-calibration count** | `1` | How many pixels do the expensive full calibration. Use 1. |
| **Legacy max probes** | `31` | Only used by the legacy peak-search modes; ignored by the default `locked-qwp`/`triplet` path. |
| **Readout tracking** | `locked-qwp` | How later HWP angles are handled. `locked-qwp` keeps the QWP on the compensating null and only uses the exact ±45° analyser slopes. `confirm` / `refine` / `hybrid` / `global` are legacy 2-D peak searches kept for diagnostics. |
| **Learned readout** | `triplet` | What production pixels measure per HWP. `triplet` = local AC-on background + both ±45° slopes (3 reads). `zero-anchor` drops the opposite slope. `peak-only` is a legacy speed diagnostic. **Use `triplet`.** |
| **Certify peak lock-in** | off | Extra QWP/ANL ± probes after each peak to certify it. Costs time; diagnostics only. |
| **Peak probe step [deg]** | `5.0` | Step for those certification probes. |
| **Peak fail margin [µV]** | `1.0` | How much a neighbouring probe may beat the centre before certification fails. |

### Follow-up pixel lists

| Field | Default | Meaning |
| --- | --- | --- |
| **DC pixels** | same as *Pixels* | Pixels that get a DC hysteresis loop. |
| **AC pixels** | blank | Pixels that get a fixed-peak AC amplitude sweep (linearity check). |
| **ANL pixels** | blank | Pixels that get a full analyser-angle sweep (Malus-model check). |
| **Run ANL sweep after map** | off | Automatically run the ANL sweeps when the chip map finishes. |
| **ANL sweep peak HWP only** | on | Sweep only at each pixel's strongest HWP rather than all nine. |

| Button | Effect |
| --- | --- |
| **Historical Hyst Only** | Run hysteresis on pixels of a *previous* run, using that run's saved peak conditions. Nothing is re-mapped. |
| **AC Vpp Sweep** / **ANL Sweep** | Launch those follow-ups on their own, against a chosen source run. |

### Run controls

| Button | Effect |
| --- | --- |
| **Start Measurement** | Validates every setting, creates the run folder, and spawns the worker process. |
| **Request Safe Stop** | Asks the worker to stop at the next safe checkpoint, with a full electrical teardown. |
| **Skip Current Pixel** | Abandons the current pixel (saved as partial) and continues with the next. |
| **Open Run Folder** | Opens the active run directory in Explorer. |
| **Clear Terminal** / **Dark mode** / **Show advanced settings** | Cosmetic / disclosure. |

---

## 4. Advanced settings, field by field

Tick **Show advanced settings** to reveal these. The defaults are production
values. Change them only with a reason, and record the reason.

### Advanced Run Inputs

| Field | Default | Meaning |
| --- | --- | --- |
| **Resume run** | blank | Folder of an existing run to continue instead of creating a new one. |
| **Skip completed pixels on resume** | on | A pixel is skipped only when its chip sweep **and** every requested hysteresis artifact verify as complete. |
| **Stage cal** | blank | Optional `pixel_positions.json` or run folder with calibrated stage coordinates. |
| **Optical cal** | `latest` | The optical calibration JSON used as the initial QWP/analyser seed. `latest`, `none`, or a path. |

### Advanced Substrate And Nulling

| Field | Default | Meaning |
| --- | --- | --- |
| **Substrate** | `do` | `do` = build a fresh per-HWP null-seed table this run. `load` = reuse an existing one. `skip` = no seed table. |
| **Substrate cal** | blank | Path to the table to load; blank auto-finds the newest. |
| **Renull mode** | `adaptive-renull` | Policy for pixels **after** the calibration pixel. `seed-only` = move to the seed and verify (fastest). `adaptive-renull` = re-optimise QWP/ANL only when the seed leaks. `fast-renull` = re-optimise at every pixel and HWP (slowest, most robust). The calibration pixel *always* does a full 2-D null regardless. |

### Advanced Map Grid

| Field | Default | Meaning |
| --- | --- | --- |
| **HWP frame** | `lab` | Whether the angles below are incident polarisation $\theta_i$ (lab) or raw HWP motor degrees. |
| **HWP sweep** | `centered-180` | `centered-180` treats *HWP start/centre* as the centre and scans ±90° of $\theta_i$ (±45° of raw HWP). `start-stop` uses start/stop/step literally. |
| **HWP start/centre, stop, step** | `81.8688`, `165`, `15` | Grid definition for `start-stop` mode; only *start* matters in `centered-180`. |
| **HWP sweep step [deg]** | `22.5` | $\theta_i$ spacing in `centered-180`. The motor step is half this. |
| **HWP points** | `9` | Odd number of points in `centered-180`. |
| **AC voltages [Vpp]** | `9.0` | Comma-separated drive amplitudes for the map. Use `1,3,5,9` for an explicit linearity check (costs 3 to 4× the time). |
| **Manual raw peak scout** + the three **Manual …raw [deg]** fields | off | Override: measure one exact raw HWP/QWP/ANL triple and use it to seed the peak branch. Forces `--no-rotator-home` so the coordinates are not re-tared. |

### Advanced Follow-Up Sweeps

| Field | Default | Meaning |
| --- | --- | --- |
| **Follow-up AC Vpp [Vpp]** | `1,3,5,7,9` | Amplitudes for the fixed-peak AC linearity sweep. |
| **Hysteresis dwell [s]** | `30` | DC-only poling hold at **each** voltage step, before the AC probe turns on. **Loop shape is rate-dependent, so keep this fixed for a whole campaign.** |
| **Hysteresis limit [+/−V]** | `40` | Symmetric endpoint: $+V \to -V \to +V$. May be reduced; anything above 40 V is rejected. |
| **Hysteresis AC [Vpp]** | `4.0` | Small-signal probe amplitude. Gated ON only for each lock-in window and OFF for every ramp and dwell, so it barely perturbs the coercive region. |
| **Uniform hyst. override [V]** | blank | Blank = the standard 45-point centre-dense grid (levels 40, 30, 25, 20, 15, 12.5, 10, 7.5, 5, 2.5, 1.25, 0 V, mirrored and returned). A positive number forces uniform spacing instead. |
| **Hysteresis cycles** | `1` | Full down+up loops. Use 2 only when you need wake-up or repeatability. |
| **Adaptive hysteresis fine grid** | off | Runs a coarse reconnaissance loop first, finds $V_c^\pm$, then centres a dense grid on them. Overrides both other grid choices and takes much longer. |
| **Dynamic lock-in range during hysteresis** | off | Predictive 5 to 200 µV ranging *for hysteresis only*. Widens immediately, narrows only after repeated evidence, and changes range while the AC probe is off. Restores the fixed range afterwards. |
| **AC sweep DC hold / dwell** | `40 V` / `60 s` | Conditions for the fixed-peak AC sweep. |
| **ANL start / stop / step / DC hold / dwell / AC Vpp** | `15`, `195`, `10`, `40 V`, `5 s`, `1,3,5,7,9` | The analyser diagnostic sweep: 19 analyser angles over a full 180° Malus period. |
| **DC hysteresis starts at 0 V** | off | Start from 0 V (virgin curve) instead of from $+V_\mathrm{max}$. Only meaningful after a domain reset. |
| **Reset domains before DC hysteresis** | off | Run the bipolar depoling train first. |

### Advanced Timing And Safety

| Field | Default | Meaning |
| --- | --- | --- |
| **Poling voltage [V]** | `40` | DC bias held throughout each pixel's measurement. |
| **Poling dwell [s]** | `180` | With adaptive poling on (the default) this is the **cap**, not the fixed time. |
| **SMU compliance [mA]** | `1` | Current limit. Protects the pixel. |
| **Lock-in settle [s]** | `1.5` → auto-raised to `2.0` | Wait after a change before sampling. Automatically raised to $2 \times \mathrm{TC} \times \text{filter order}$. |
| **Lock-in readings** | `4` | Phasor samples averaged per point (minimum 2). |
| **Read delay [s]** | `1.5` → auto-raised to `2.0` | Spacing between those samples, so they are approximately independent. |
| **DSP7230 TC index** | `14` (= 500 ms) | Lock-in time constant. Longer = quieter but slower. Index must be ≥ 8 (5 ms). |
| **Filter slope [dB/oct]** | `12` | 6, 12, 18 or 24. Sets the filter order used in the settle calculation. |
| **Fixed range index** | `16` (= 200 µV RMS full scale) | The lock-in range is **fixed** for the whole map: automatic ranging is disabled so that every point shares one calibration. Rows at ≥ 85 % of full scale are warned; ≥ 98 % or overload are invalid. |
| **Reference source** | `external-analog` | The 0.5 Vpp CH2 sine into the lock-in reference input. |
| **Reference phase [deg]** | `0` | Cosmetic only, used for the displayed signed value; the analysis re-derives the phase axis from the data. |
| **Allow unlocked lock-in reference** | off | Diagnostic override. Normally a zero or mismatched `FRQ` readback stops the run. |
| **QWP readout offset [deg]** | `0` | `0` keeps the Sénarmont null-compensation geometry. `45` selects the separate quadrature-bias protocol. Do not change casually. |
| **CH1/CH2 freq [Hz]** | `30000` | Modulation frequency for both drive and reference. |
| **Funcgen settle [s]** | `0.2` | Pause after an amplitude or output change. |
| **Laser power [mW] / serial** | `7.0` / auto | KLS1550 setpoint. **Apply Laser Power** re-applies it live. |
| **Min signal [mV]** | `50` | Detector level below which the stage alignment retries. |
| **Null max [mV]** | `14.5` | Detector level that counts as an acceptable null. |
| **Null margin [mV]** | `1.5` | Continuation margin above *Null max*. Marginal and high nulls continue **with quality flags** but are never saved as trusted seeds. |
| **Stage brighten [deg]** | `45` | Analyser offset from the null used only to brighten the beam for stage alignment. |
| **Rotator tol [deg]** | `0.5` | Readback tolerance before a fast move gets one corrective retry. |
| **Rotator velocity [%]** | `50` | Elliptec speed for direct moves (clamped to 25 to 100). |

### BTO Effective Coefficient

These feed the **absolute** $|r_\mathrm{eff}|$ calculation. Leave them blank and
the software reports the normalised rotation only, which is the correct
observable for comparing pixels. Fill them in **only** when you have genuinely
measured each one.

| Field | Meaning |
| --- | --- |
| **Wavelength [nm]** | 1550. |
| **BTO thickness [nm]** (+ std) | Film thickness $t$, the optical interaction length. |
| **Electrode gap [µm]** (+ std) | Measured gap $g$. |
| **Field correction alpha** (+ std) | The FEM electrostatic factor in $E = \alpha V/g$. **Device-specific.** Not an optical overlap factor, and not transferable from a paper. |
| **BTO refractive index** (+ std) | Default 2.1. |
| **Device/source Vpp** (+ std) | Measured volts across the electrodes ÷ programmed generator Vpp, at the modulation frequency, with the device connected. |
| **Detector AC/DC gain** (+ std) | Lock-in-channel V/W at $f_\mathrm{mod}$ ÷ scope DC-channel V/W. Enter 1 only after verifying they are identical. |
| **Confirm Sénarmont null-slope geometry** | Master enable for $r_\mathrm{eff}$. |
| **Confirm zero-offset sine drive** | Required because the RMS conversion assumes $V_\mathrm{rms} = V_\mathrm{pp}/2\sqrt{2}$. |

### Advanced Hardware

| Checkbox | Default | Meaning |
| --- | --- | --- |
| **Home stage at startup** | on | Home the XY stage before the run. |
| **Use camera/live view** | on | The worker owns the camera and exports frames to the GUI. |
| **Home/tare rotators** | on | Home each rotator and set its software zero. Turn off only when you are supplying exact raw angles. |
| **Auto-align each pixel** | on | Hill-climb onto each pixel's transmission peak before measuring. |
| **Fast rotator moves** | on | Direct `set_angle` moves instead of the slow chunked boundary-safe path. |
| **Verify fast rotator moves** | on | Read back after each fast move and correct once if outside tolerance. |
| **Require real lock-in / SMU / Arduino** | on | Refuse to fall back to simulated instruments. **Keep all three on for real data.** |
| **Use KLS1550 laser / Laser on at startup** | on / on | Laser control. |
| **Simulate lock-in** | off | Immediately use `FakeLockin`. Motion and timing tests only. |

---

## 5. Running a chip map

1. **Shakedown first.** One pixel, confirm the outputs; see
   [Quick Start §5](quickstart.md#5-run-one-pixel-the-shakedown).
2. Set **Chip ID**, **Pixels**, **Calibration pixel**, **DC pixels**.
3. Set **Substrate = `load`** and fill the path (unless this is a first run).
4. Press **Start Measurement**. Confirm any prompts.
5. Watch the first pixel all the way through. If it is clean, you can leave it.

### What happens per pixel

```text
 1.  Optics reset:  AC OFF.  HWP → first grid angle.  QWP/ANL → stored seed null.
 2.  Brighten:      ANL → a_null + 45°   (so the photodiode sees a bright peak)
 3.  Stage:         move to the pixel, hill-climb to the local transmission peak
                    (fallback chain: hill-climb → fast-peak → golden-section → line scan)
 4.  Dim:           ANL → a_null
 5.  Route:         Arduino switch matrix selects this pixel's electrode pair (exclusive)
 6.  Pole:          SMU ramps 0 → +40 V in 5 V chunks and holds.
                    Adaptive poling watches the lock-in and stops at the plateau
                    (≥ 60 s floor, 180 s cap, ≥ 2 µV signal required).
                    DC stays ON for the whole pixel.
 7.  For each HWP angle (9 by default):
        a. verified HWP move (readback + one corrective retry, tol 0.5°)
        b. AC OFF → read the null leakage
        c. if the null leaks: local QWP+ANL re-null (adaptive-renull mode)
        d. AC ON  → TRIPLET readout:
               • lock-in at the local null   (the analyser-independent background)
               • lock-in at exactly +45°     (maximum positive slope)
               • lock-in at exactly −45°     (maximum negative slope)
        e. AC OFF
        f. save; update the chip summary
 8.  Optional in-run follow-ups (hysteresis / AC sweep / analyser sweep)
 9.  Teardown:      AC OFF → SMU OFF → matrix OFF
```

### Electrical safety ordering (never changes)

```text
Stage alignment:       AC OFF, SMU OFF, matrix routed
Null check:            AC OFF, SMU ON
Slope measurements:    AC ON,  SMU ON
Between HWP blocks:    AC OFF, SMU ON
Between pixels:        AC OFF, SMU OFF, matrix OFF
```

The lock-in **reference** channel (CH2) stays on continuously; only the **drive**
channel (CH1) is gated. Details of the switching hardware are in
[Switch matrix](../experiment/switch-matrix.md).

### Time budget

| Activity | Time |
| --- | --- |
| Calibration pixel | ≈ 8 min |
| Production pixel (9 HWP × triplet × 1 Vpp) | ≈ 4.5 to 5 min |
| 83-pixel map | ≈ 6 to 7 h |
| One standard hysteresis loop (45 points, 30 s dwell) | ≈ 29 min |
| Hysteresis on all 100 pixels | ≈ 49 h |
| Substrate null table | ≈ 20 min |

See [the timing breakdown figure](../../assets/figures/timing_breakdown.png) for
where the wall-clock time actually goes.

---

## 6. Follow-up measurements

### 6.1 DC hysteresis

The headline follow-up. It puts the optics at the pixel's best condition and
sweeps the DC bias $+40 \to -40 \to +40$ V while reading the AC electro-optic
response at each step.

Per DC point, in this exact order:

1. Function generator CH1 **OFF**.
2. SMU ramps to the next level in 5 V chunks; 0.3 s preset.
3. Full DC-only poling dwell (default 30 s). **AC stays off the whole time**.
4. Snapshot: SMU current, programmed level, **measured terminal voltage**,
   compliance flag. A missing readback or a voltage error greater than 0.5 V
   aborts the point *before* AC can turn on.
5. CH1 **ON** (verified) → lock-in settle (≥ 5 × TC) → scope DC power →
   phasor-averaged lock-in read.
6. CH1 **OFF** before the next step.

Launch it three equivalent ways, all with identical parameters:

| Route | How |
| --- | --- |
| **In-run** | Put pixels in **DC pixels** before starting the map. |
| **GUI follow-up queue** | **Historical Hyst Only** against a finished run. |
| **Standalone CLI** | `python pockels\Pockels_Calibration_2026.py --hysteresis-only` with the `--hyst-*` flags. |

### 6.2 AC amplitude sweep

Holds the optics at the pixel's peak condition and steps the drive amplitude
through `1,3,5,7,9` Vpp. The response must be **linear** in $V_\mathrm{rms}$;
the analysis gate is $R^2 \ge 0.98$. A nonlinear result means you are seeing a
quadratic or electrostrictive contribution, saturation, or an artefact.

### 6.3 Analyser sweep

Rotates the analyser through a full 180° Malus period (default 15° → 195° in 10°
steps) and records both the DC transmission and the lock-in response. This is
the *slow, honest* version of the fast triplet: it lets you fit the complete
$Z(\psi) = P + E_1\sin 2\psi + E_2\cos 2\psi$ model and check that the fast ±45°
estimate agrees. Run it on a handful of representative pixels to validate the
map.

---

## 7. Monitoring a run

### Run Status panel

- **Operation:** the current phase (connecting, aligning, poling, measuring…).
- **Details:** the current pixel / HWP / analyser side / Vpp.
- **Progress bars:** current pixel, and whole chip.
- **LEDs:** *High voltage DC*, *AC voltage*, *Laser*. These mirror the real
  commanded state: if the DC LED is lit, the SMU output is on.

### Optical Train panel

A live schematic of the beam line showing each rotator's commanded and read-back
angle, which optic is currently moving, the sample, and the detector. Click the
laser box to toggle emission.

### Live Chip Map and the View selector

The **View:** dropdown switches the 10 × 10 map between:

| Layer | Shows |
| --- | --- |
| Peak lock-in \|R\| (µV) | Raw peak magnitude per pixel (live, updates per point) |
| Peak signed response (µV) | Signed response, diverging blue-white-red about zero |
| **Hysteresis sweep: lock-in \|R\| vs Vdc** | The live loop curve for one pixel instead of the map |
| Hysteresis: loop type | Categorical classification map |
| Vc+, Vc−, loop width, imprint, S_rem±, switchable, frozen, squareness, loop area, leakage | Per-metric heatmaps, streamed in as each loop finishes |

With the hysteresis curve selected, **Pixel: Auto** follows whichever pixel is
sweeping; `001` to `100` pins a specific pixel's live or latest saved curve.
Branch colours: virgin = purple, pre-saturation = orange, down = red, up = green; later
cycles dashed; compliance trips marked with red crosses.

> **Display orientation:** the map is drawn with `display_col = 10 − stage_col`,
> i.e. mirrored left-right relative to the internal pixel numbering, so that it
> matches the chip as you see it on the camera. Clicks are converted back
> automatically. Keep this in mind when comparing a heat-map position to a pixel
> number; see [the pixel grid figure](../../assets/figures/pixel_grid.png).

### Side panels

- **Stage Alignment Live**: the intensity-versus-position trace of the running
  alignment search.
- **HWP/Analyser Lock-In**: bar chart of the lock-in magnitude at each HWP and
  analyser side for the current pixel.
- **Live Oscilloscope**: the DC detector level, with history.
- **Live Camera**: the chip surface through the alignment camera, with a
  crosshair.

### Terminal and Run Notifications

The **Terminal** is the raw worker stdout. The **Run Notifications** panel
extracts anything that looks like a warning or an error so you do not have to
scroll. Clear it with the *Clear* button.

---

## 8. Stopping, skipping and resuming

### Request Safe Stop

Writes a stop file that the worker polls at every checkpoint. The worker
finishes the current operation, turns AC off, ramps the SMU down, opens the
matrix, writes the pixel's data, and exits. **This is the correct way to stop.**

### Skip Current Pixel

Latches a skip request. The current pixel is saved as *partial*, the hardware is
left safe, and the run continues with the next pixel. Useful when one pixel is
dead and is burning time in alignment retries.

### Resume

Every run writes `automation_progress.json` continuously. To resume:

1. **Resume Latest Run** (or **Choose Historical Run**).
2. Optionally **Unmeasured Only** to trim *Pixels* to what is still missing.
3. **Start Measurement**.

With *Skip completed pixels on resume* ON, a pixel is skipped only when its chip
sweep **and** every requested hysteresis artifact verify as complete; a
half-finished loop is redone, not silently accepted.

### Automatic hardware recovery

If an instrument drops off the bus mid-run (USB reset, VISA session lost,
`WriteFile` access denied, serial write timeout), the worker does **not** die:

1. The interrupted pixel is saved as failed/partial with its existing data.
2. All outputs are shut down and the matrix isolated.
3. Sessions are closed and reopened, following USB **identity** (serial number /
   location) rather than guessing COM numbers.
4. Everything is verified: matrix ALL-OFF acknowledgement, SMU OFF readback,
   function generator acceptance, scope telemetry, rotator angles, lock-in
   communication, stage position.
5. The run continues with the **next** pixel. The interrupted one is not
   replayed automatically; resume later to fill it in.

Retries back off 5 → 10 → 20 → 40 → 60 s and then stay at 60 s until the
hardware returns or you press Stop. Every incident is appended to
`transport_recovery.jsonl` in the run folder. See
[Instrument control](../software/instrument-control.md).

---

## 9. Running headless (CLI)

The same file is both GUI and worker:

```bat
python pockels\pockels_fast_map_gui.py                 REM GUI
python pockels\pockels_fast_map_gui.py --cli <flags>   REM headless worker
```

A production-equivalent command line:

```bat
python pockels\pockels_fast_map_gui.py --cli ^
  --chip-id BTNO_0087 ^
  --pixels 1-6,11-16,21-26,31-37,41-48,51-100 ^
  --calibration-pixel 46 ^
  --hwp-points 9 ^
  --voltages 9 ^
  --phase-substrate load --substrate-cal <path\to\substrate_calibration.json> ^
  --renull-mode adaptive-renull ^
  --learned-readout-mode triplet ^
  --poling-voltage 40 --poling-dwell 180 ^
  --lockin-tc-index 14 --lockin-sensitivity-index 16 ^
  --post-dc-hysteresis-pixels 46,85 ^
  --require-lockin --require-smu --yes
```

Every GUI field has a CLI equivalent; the complete list is in the
[CLI Reference](../reference/cli.md). Useful diagnostics:

```bat
python pockels\pockels_fast_map_gui.py --cli --list-serial-ports
python pockels\pockels_fast_map_gui.py --cli --list-serial-ports --show-bluetooth-ports
python pockels\pockels_fast_map_gui.py --cli --show-range-changes --show-wrap-moves
```

If you run the file in Spyder with no arguments it opens an interactive setup
wizard in the console before touching any hardware.

---

## 10. Special modes

### Manual probing (no Arduino)

Tick **Manual probing**. The stage aligns each pixel, then the GUI shows a
**"Probed - Continue"** panel and waits for you to land the probe needles.
Arduino routing is disabled entirely (`--no-arduino`). Everything else (poling,
nulling, the HWP sweep) proceeds normally.

### Simulated instruments

`Simulate lock-in`, `--allow-fake-lockin`, `--allow-fake-smu` and
`--allow-no-arduino` let the software run without those instruments. They exist
**only** for motion and timing tests. `FakeLockin` synthesises a plausible
response from the analyser angle; any "data" it produces is fiction. Real runs
must keep *Require real lock-in / SMU / Arduino* ticked.

### Historical follow-ups

**Historical Hyst Only** lets you add hysteresis to a run that finished months
ago. The source run's peak conditions are read from its saved summaries, the
original metadata is backed up before anything new is written, and the loops
land inside the original run folder.

---

## 11. End-of-day shutdown

1. **Request Safe Stop** and wait for the worker to exit cleanly.
2. Confirm on the instrument front panels: SMU output **OFF**, function
   generator CH1 **OFF**.
3. Turn the laser off: click the laser box in the *Optical Train* panel, or
   close the GUI, whose cleanup switches it off.
4. Close the GUI.
5. Copy or back up the run folder. Run folders are deliberately **git-ignored**;
   they never belong in the repository and nothing in git will bring them back.
6. Note in your logbook: chip ID, run folder name, anything unusual, and the lab
   temperature.

---

## 12. Campaign design guidance

For compositional studies (the usual purpose of this chip) the measurement
order and the choice of observable matter as much as the settings do.

1. **Randomise pixel order.** Enter a shuffled pixel list. The order is recorded
   in `run_config.json`. This stops slow drift (laser power, temperature,
   alignment) from masquerading as a composition trend.
2. **Log temperature.** Composition shifts $T_c$; so does the building's HVAC.
   Nothing records it automatically.
3. **Use the normalised rotation, never raw µV**, as the compositional
   observable. Raw lock-in volts depend on laser power, coupling, detector gain
   and null quality; `rotation_slope_rad_per_Vrms` divides all of that out.
4. **Convert coercive voltages to fields only with measured geometry**, using
   $E_c = \alpha V_c / g$, supplied via `--gap-um` and `--alpha`. Never guess
   $\alpha$: with `--alpha 1.0` you are quoting a plain parallel-plate estimate,
   not the real field.
5. **Take replicates.** Re-measure 2 to 3 pixels twice in the same run; that
   spread is your noise floor.
6. **Keep one dwell and one voltage grid for the whole campaign.** Hysteresis
   loop shape is rate-dependent; mixing dwells makes loops incomparable. If you
   want the rate dependence, measure it deliberately as its own experiment.
7. **Time the first accepted pixel before committing the queue.** Multiply it
   out and check the number is one you can live with.

---

## See also

- [Troubleshooting](troubleshooting.md), for when any of the above misbehaves.
- [Data Schema](../reference/data-schema.md), every file and column the run
  writes.
- [Architecture](../software/architecture.md) and
  [Data pipeline](../software/data-pipeline.md), for what the worker is actually
  doing.
- [Ferroelectrics](../physics/05-ferroelectrics.md), how to read a loop.

---

<div align="center">

[← Quick start](quickstart.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Troubleshooting →](troubleshooting.md)

</div>
