# The Data Pipeline

**What this page is for:** what happens to a measurement after it is taken.
Which analysis runs automatically while the instrument is still working, which
you run afterwards by hand, where everything lands on disk, and which column is
actually "the signal".

The full column dictionary lives in the
[Data Schema](../reference/data-schema.md), and this page does not duplicate it.
What follows is the *shape* of the pipeline and the handful of decisions you
need to know before you touch the numbers.

---

## Contents

1. [Automatic versus manual](#1-automatic-versus-manual)
2. [The automatic per-pixel analysis chain](#2-the-automatic-per-pixel-analysis-chain)
3. [The output folder tree](#3-the-output-folder-tree)
4. [The main data products, and which column is the signal](#4-the-main-data-products-and-which-column-is-the-signal)
5. [The quality-flag system](#5-the-quality-flag-system)
6. [Offline analysis scripts](#6-offline-analysis-scripts)
7. [Loading the data in Python](#7-loading-the-data-in-python)
8. [Why the analysis is retroactive](#8-why-the-analysis-is-retroactive)

---

## 1. Automatic versus manual

```text
ACQUISITION                           AUTOMATIC  (during the run, per pixel)
  fast_map.csv  ─────────────────────▶ analyse_fast_map_rows()
                                         ├─ geometry gates on every row
                                         ├─ Malus normalisation → rotation δ
                                         ├─ voltage-linearity fit
                                         ├─ signed 2θ complex angular fit
                                         │    + separate 4θ magnitude fit
                                         ├─ r_eff gating
                                         └─ fast_map_summary.json
                                            + fast_map_angular_response.csv
                                            + angular polar/diagnostic PNG & PDF
                                         └─▶ fast_map_all_pixels.csv  (chip summary)

  dc_hysteresis.csv ─────────────────▶ analyse_loop_file()
                                         ├─ phasor → signed S(V), residual Q(V)
                                         ├─ the full metric set, per cycle
                                         ├─ classify_loop()
                                         └─ dc_hysteresis_metrics.json
                                            + dc_hysteresis_loops.png

  poling_kinetics.csv ───────────────▶ fit_poling_kinetics()  →  τ, β

                                      MANUAL  (after the run, on the folder)
  run folder ────────────────────────▶ make_hysteresis_maps.py
                                         → 21 heat maps, type map, loop gallery
                                    ─▶ make_compositional_report.py
                                         → correlations, trends, clusters
                                    ─▶ make_fast_map_extra_plots.py
                                    ─▶ make_peak_hwp_angle_map.py
                                    ─▶ pockels_hysteresis_analysis.py <csv>
                                         → re-analyse with different thresholds
```

Everything marked *automatic* has already run by the time you open the folder.
You do not have to do anything to get a per-pixel summary, an angular polar
plot or a full set of loop metrics; they are written as the run proceeds.

Everything marked *manual* is chip-level or cross-pixel: it needs the whole run
to exist before it can say anything, so there is no point running it early.

> **Note**
> The automatic analysis is wrapped in `try/except` at every stage. A plotting
> failure records `angular_plot_error` in the summary and moves on; the
> numerical fit stays available for later replotting. Derived output must never
> cost you completed hardware data.

---

## 2. The automatic per-pixel analysis chain

Implemented in
[`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py),
which imports no hardware.

1. **Group the rows** by (HWP angle, drive amplitude).
2. **Derive a rotation observation** per group, but only from rows that pass
   every geometry gate:

   | Gate | Limit | Constant |
   | --- | --- | --- |
   | QWP offset from its null | ≤ 3° | `MAX_QWP_OFFSET_FROM_NULL_DEG` |
   | Analyser quadrature error (distance from exactly $\pm 45^\circ$) | ≤ 15° | `MAX_ANALYSER_QUADRATURE_ERROR_DEG` |
   | Null leakage fraction | ≤ 0.05 | `MAX_NULL_LEAKAGE_FRACTION` |
   | Slope side | one of `plus45`, `minus45`, `learned_anchor`, `learned_opposite` | `VALID_SLOPE_SIDES` |
   | Quality flags | must contain none of the disqualifying set | `DISQUALIFYING_ROW_FLAGS` |

3. **Normalise.** The lock-in net phasor is divided by the *locally measured*
   Malus slope, which removes laser power, fibre coupling, focus quality,
   detector gain, null depth and analyser placement error in one step. What is
   left is a dimensionless polarisation rotation $\delta$. The derivation is in
   [The Null-Slope Sénarmont Readout](../physics/03-senarmont-readout.md).
4. **Fit rotation versus voltage**, per HWP angle, against
   $V_\mathrm{rms} = V_\mathrm{pp}/(2\sqrt{2})$, with a free intercept when
   three or more levels exist.
5. **Fit the angular response** across HWP angles, using only
   geometry-certified fits, with the **signed complex $2\theta$ model**
   (`DEFAULT_ANGULAR_HARMONIC = 2`). Both quadratures are fitted simultaneously
   so the electro-optic sign reversal survives. The *magnitude* is then fitted
   separately with a $4\theta$ model for the four-lobed polar plot.

   > The signed linear EO response reverses sign every 90° of incident
   > polarisation, so it is a $2\theta_i$ quantity whose **magnitude** is
   > four-lobed. Fitting the signed complex response directly at harmonic 4
   > destroys the sign change, and was the cause of misleadingly low angular
   > $R^2$ values in early runs.

6. **Select the peak.** If the angular fit is valid ($R^2 \geq 0.80$), take the
   certified measured HWP nearest the fitted peak
   (`signed_2theta_fit_certified_measured_hwp`); otherwise fall back to the
   largest certified measured response
   (`certified_normalized_measured_max_fallback`). The mode is always recorded
   in `peak_selection_mode`, so you can always tell which happened.
7. **Gate $r_\mathrm{eff}$.** The absolute coefficient is emitted only when
   every input and every gate is satisfied; otherwise `r_eff_status` records
   exactly which gate failed and only the normalised rotation is reported.

---

## 3. The output folder tree

`_bootstrap` pins the working directory to the repository root, so every run
lands in a predictable place regardless of how the process was launched.

| Folder | Contents |
| --- | --- |
| `pockels_fast_map/` | Fast-map runs, the main output |
| `pockels_calibration/` | Deep-campaign and standalone hysteresis runs |
| `calibration_results_withSample/` | Guided sample-in optical calibrations |
| `calibration_results_3step/` | Three-step optical calibrations |
| `stage_calibration/` | Stage pixel calibrations, `pixel_stage_calib.json` |
| `pockels_campaign/` | Campaign runs, `first_null_reference.json` |
| `analyser_sweep_voltage_series/` | Standalone analyser sweeps |
| `smu4201_sweeps/` | Standalone I-V sweeps |

> **Warning**
> These folders hold measurement data, not source. Keep them out of version
> control and back them up separately: a chip map is six to seven hours of
> instrument time that cannot be regenerated from the repository.

<details>
<summary>Inside one fast-map run directory</summary>

```text
pockels_fast_map/20260915_143012_BTNO_0087_shakedown/
│
├── run_config.json                     every setting the run was given
├── lockin_configuration.json           the exact lock-in setup applied
├── loaded_optical_calibration.json     the optical calibration that was loaded
├── substrate_calibration.json          the per-HWP null seed table (if built here)
├── null_seed_table.json                the live / learned seed table
├── automation_progress.json            resume state
├── transport_recovery.jsonl            one line per transport incident (if any)
├── gui_state.json                      live GUI state (also useful post-hoc)
├── gui_alignment.json                  live alignment trace
├── gui_live_camera.png                 last camera frame
├── fast_map_all_pixels.csv             ← THE CHIP SUMMARY (start here)
├── <run-name>_fast_map_all_pixels.csv  date/chip-bearing alias of the above
│
├── stage_alignment/
│   ├── pixel_positions.json / .csv     arrived + calibrated positions
│   ├── images/                         arrival + aligned camera frames
│   └── alignment_logs/                 per-pixel search logs and diagnostics
│
├── pockels_pixels/
│   └── pixel_046/
│       ├── pixel_run_info.json
│       ├── fast_map.csv                ← THE PER-POINT RECORD (~140 columns)
│       ├── fast_map_raw_samples.csv    every individual lock-in sample
│       ├── fast_map_null_reference.csv null-point rows only
│       ├── fast_map_summary.json       best conditions, fits, gate status
│       ├── peak_response.json          the single best point
│       ├── peak_lock_table.json / .csv learned per-HWP readout table
│       ├── null_seed_by_hwp.json       nulls found at this pixel
│       ├── fast_map_angular_response.csv   normalised δ and Γ versus θᵢ
│       ├── fast_map_angular_polar.png/.pdf the four-lobed polar plot
│       ├── fast_map_angular_diagnostics.png/.pdf
│       ├── poling_kinetics.csv         the τ, β fit input
│       └── dc_hysteresis/
│           ├── dc_hysteresis_live.json     cumulative atomic live trace
│           └── sweep/
│               ├── dc_hysteresis.csv          ← THE LOOP DATA
│               ├── dc_hysteresis_raw_samples.csv
│               ├── dc_hysteresis_conditions.json
│               ├── dc_hysteresis_metrics.json ← THE LOOP METRICS
│               ├── dc_hysteresis.png          3-panel acquisition plot
│               └── dc_hysteresis_loops.png    4-panel signed-loop plot
│
├── hysteresis_maps/                    (make_hysteresis_maps.py)
└── compositional_report/               (make_compositional_report.py)
```

</details>

---

## 4. The main data products, and which column is the signal

| File | Granularity | Open it when |
| --- | --- | --- |
| `fast_map_all_pixels.csv` | one row per pixel | **Always start here.** Which pixels completed, how they rank, which are flagged. |
| `pockels_pixels/pixel_NNN/fast_map.csv` | one row per (HWP, analyser position, Vpp) | You need the individual measurements, the angles actually achieved, or the S9 certificate fields. |
| `fast_map_raw_samples.csv` | one row per lock-in sample | You are chasing noise, drift within an averaging window, or an outlier. |
| `fast_map_summary.json` | one object per pixel | You want the fits rather than the points: angular fit, rotation-versus-voltage fit, $r_\mathrm{eff}$ block, gate status. |
| `dc_hysteresis.csv` | one row per DC point | The loop itself. **Read it with `comment="#"`.** |
| `dc_hysteresis_metrics.json` | one object per loop | The coercive voltages, imprint, squareness and classification, all already computed. |
| `poling_kinetics.csv` | one row per poll during poling | The $\tau$, $\beta$ switching-kinetics fit. |
| `run_config.json` | one object per run | Provenance. When a plot looks odd six months later, this says exactly how it was taken. |

### Which column is "the signal"?

> **For physics, use `lockin_net_x_V` and `lockin_net_y_V`.**
> These are the **background-subtracted complex phasor**: the measured phasor at
> the analyser slope point minus the phasor measured at the local null in the
> same HWP block. The subtraction removes the analyser-independent term, the
> part of the lock-in reading that is present even where there is no optical
> slope, and which is therefore not light. What remains is the electro-optic
> response, with its sign.
>
> For a quick look, `lockin_net_mag_V` is fine. `lockin_signed_V` is a **display
> convenience**, magnitude × cos(phase − reference), and the analysis
> re-derives its own phase reference rather than trusting it.
>
> `lockin_mag_V` is the raw, un-subtracted magnitude. Use it only as a rough
> screen, never as a physical quantity.

### Which column to rank pixels by?

- **Quick screen:** `max_abs_signed_response_V` in the chip summary, the
  largest background-subtracted signed response. Good for *where is the
  signal*.
- **Physical comparison:** `rotation_slope_rad_per_Vrms`, the normalised
  rotation per RMS volt. **This is the number to plot against composition.**

They can disagree, and when they do the rotation slope is right. A pixel with a
shallow null or poor fibre coupling can show a small raw response while having
a perfectly good electro-optic coefficient; the raw response absorbs laser
power, coupling and null depth, and the normalised rotation does not.

For hysteresis, the loop is built from `LockIn_X_V` and `LockIn_Y_V`, projected
onto the saturation-tail phase axis; see §7.

---

## 5. The quality-flag system

`quality_flags` is a `;`-separated set carried on both the per-point rows and
the chip summary. It is **additive**: nothing is deleted because it is flagged.
The measurement is kept and labelled, and the labels decide what the fits are
allowed to use.

Three of those flags are special:

```python
DISQUALIFYING_ROW_FLAGS = {
    "lockin_overload",
    "lockin_range_exceeded",
    "s9_operating_point_failed",
}
```

A row carrying **any** of these is excluded automatically from every fit in
`pockels_measurement_analysis.py`: the normalisation, the voltage-linearity
fit, the angular fit and the peak selection all skip it, without you having to
filter anything yourself. The reasoning:

| Flag | Why the row cannot be used |
| --- | --- |
| `lockin_overload` | The demodulator was saturated. The number is not a measurement of anything. |
| `lockin_range_exceeded` | ≥ 98 % of full scale, so the reading is at the edge of the range and may be clipped or non-linear. |
| `s9_operating_point_failed` | The operating point itself did not certify, so the Malus slope this row is normalised by is not trustworthy. A number divided by an unknown is an unknown. |

Other flags you will meet often:

| Flag | Meaning | Severity |
| --- | --- | --- |
| `s9_operating_point_certified` | the operating point passed all gates | good |
| `s9_low_signal_geometry_only` | the geometry is right, the pixel is simply weak | informational |
| `high_null_leakage`, `high_null_after_adaptive`, `high_null_continued` | the null is worse than the threshold | caution |
| `plus_minus_same_sign` | the $\pm 45^\circ$ responses do **not** oppose | serious; probably pickup, not light |
| `plus_minus_asymmetric` | the two sides differ by more than 50 % | caution |
| `lockin_range_near_fullscale` | ≥ 85 % of full scale | caution |
| `bto_linearity_unverified`, `bto_null_leakage_too_high`, `bto_qwp_retardance_invalid` | an absolute-calibration gate failed | blocks $r_\mathrm{eff}$ |

The complete list, with every flag the software can emit, is in the
[Data Schema](../reference/data-schema.md).

---

## 6. Offline analysis scripts

All of these run from the repository root against a finished run folder.

### Loop metrics: re-analyse one or many CSVs

```bash
python pockels/pockels_hysteresis_analysis.py <path>/dc_hysteresis.csv
python pockels/pockels_hysteresis_analysis.py "<run>/pockels_pixels/*/dc_hysteresis/sweep/dc_hysteresis.csv"
python pockels/pockels_hysteresis_analysis.py <csv> --sat-fraction 0.8
python pockels/pockels_hysteresis_analysis.py <csv> --gap-um 7 --alpha 0.94
```

| Flag | Effect |
| --- | --- |
| `--sat-fraction` | Defines the saturation tail: $|V| \geq f\,V_\mathrm{max}$ (default 0.8). |
| `--gap-um` | The measured electrode gap; enables coercive-**field** output, $E = \alpha V/g$. |
| `--alpha` | FEM field-correction factor (default 1.0 = a plain parallel-plate estimate). |

> **Warning**
> Never guess $\alpha$. With the default `--alpha 1.0` you are quoting a plain
> parallel-plate estimate, not the real in-plane field, and $\alpha$ is
> specific to *your* electrode geometry. Borrowing a value from a paper with
> different electrodes produces a number that looks like a coercive field and
> is not one.

### Chip-level maps

```bash
python pockels/make_hysteresis_maps.py <run_dir>
python pockels/make_hysteresis_maps.py <run_dir> --gap-um 7 --alpha 0.94
python pockels/make_hysteresis_maps.py <run_dir> --reanalyse
python pockels/make_hysteresis_maps.py <run_dir> --out <dir> --no-heal
```

| Flag | Effect |
| --- | --- |
| `run_dir` | The run; omit it to use the newest folder under `pockels_fast_map/`. |
| `--out` | Output directory (default `<run_dir>/hysteresis_maps`). |
| `--gap-um`, `--alpha` | As above; adds coercive-field maps in kV/cm. |
| `--reanalyse` | Recompute every loop from its raw CSV. |
| `--no-heal` | Do **not** compute missing metrics from raw CSVs. |

Produces `hysteresis_metrics_all_pixels.csv`, 21 annotated 10 × 10 heat maps
(diverging quantities on a blue-white-red scale about zero), the categorical
`map_hysteresis_type.png`, and `map_loop_gallery.png`, which draws every pixel's
loop at its chip position, border-coloured by type. That last figure is usually
the single most informative thing you can put in a talk.

### Compositional report

```bash
python pockels/make_compositional_report.py <run_dir> --composition composition.csv
python pockels/make_compositional_report.py <run_dir> --clusters 4
```

`composition.csv` needs `pixel,composition[,label]` columns. Without it, trends
run against pixel number and the report says so. `--out`, `--reanalyse` and
`--no-heal` behave as above. The report joins loop metrics, poling kinetics
($\tau$, $\beta$) and electro-optic anisotropy into one table and renders a
Spearman correlation matrix, merit scatters, per-metric trends with $\rho$ and
$p$, a k-means loop-shape cluster map, and $\tau$ / anisotropy chip maps.

### Additional figures

```bash
python pockels/make_fast_map_extra_plots.py <run_dir> [--out-dir <dir>]
python pockels/make_peak_hwp_angle_map.py <run_dir> [<run_dir> ...]
```

---

## 7. Loading the data in Python

```python
import json
from pathlib import Path

import numpy as np
import pandas as pd

run = Path("pockels_fast_map/20260915_143012_BTNO_0087_shakedown")

# ── Chip-level screen ────────────────────────────────────────────────
chip = pd.read_csv(run / "fast_map_all_pixels.csv")
good = chip[chip.status.eq("complete") & chip.quality_flags.fillna("").eq("")]
print(good.nlargest(10, "max_abs_signed_response_V")[
      ["pixel", "best_theta_i_deg", "max_abs_signed_response_V",
       "rotation_slope_rad_per_Vrms", "r_eff_status"]])

# ── One pixel's points ───────────────────────────────────────────────
pts = pd.read_csv(run / "pockels_pixels/pixel_046/fast_map.csv")
ok = pts[~pts.quality_flags.fillna("").str.contains(
    "lockin_overload|lockin_range_exceeded|s9_operating_point_failed")]

# The physics phasor is the background-subtracted one:
net = ok.lockin_net_x_V + 1j * ok.lockin_net_y_V

# ── A hysteresis loop, rebuilt exactly as the analysis does ──────────
h = pd.read_csv(
    run / "pockels_pixels/pixel_046/dc_hysteresis/sweep/dc_hysteresis.csv",
    comment="#",                      # <-- REQUIRED: see the note below
)
h = h[~h.compliance_tripped.astype(str).str.lower().eq("true")]

tail = h[h.V_dc_V.abs() >= 0.8 * h.V_dc_V.abs().max()]      # saturation tails
phi = np.angle((tail.LockIn_X_V + 1j * tail.LockIn_Y_V).sum())

S = h.LockIn_X_V * np.cos(phi) + h.LockIn_Y_V * np.sin(phi)   # the signed loop
Q = -h.LockIn_X_V * np.sin(phi) + h.LockIn_Y_V * np.cos(phi)  # the residual

# Sanity check: if max|Q| / max|S| > 0.5 the projection is invalid.
print("quadrature fraction:", abs(Q).max() / abs(S).max())

# ── Or just read the metrics the software already computed ───────────
m = json.loads((run / "pockels_pixels/pixel_046/dc_hysteresis/sweep"
                    / "dc_hysteresis_metrics.json").read_text())
print(m["classification"]["primary"],
      m["metrics"]["loop_width_V"],
      m["metrics"]["imprint_V"])
```

> **Note: `comment="#"` when reading `dc_hysteresis.csv`.**
> The file begins with a block of `#`-prefixed provenance lines recording the
> run label, $\theta_i$, the AC probe amplitude, the analyser peak angle, the
> QWP/analyser nulls, the calibrated EO phase axis, the voltage-grid profile and
> the exact level list, the trajectory, the dwell, every lock-in setting, the
> range mode, the overload counts and the SMU readback semantics and tolerance.
> Without `comment="#"` pandas will treat the first of those lines as the
> header and the parse will fail confusingly. That header is the reason a loop
> file is self-describing years later, so do not strip it.

> **Note: display versus internal column indices.**
> `col` in the CSVs is the **0-based internal stage column**
> (`row = (pixel-1)//10`, `col = (pixel-1)%10`). The GUI heat map draws
> `display_row = row + 1` and `display_col = 10 − col`, so display columns run
> 1…10 mirrored left-to-right. If you reproduce the chip map in your own
> plotting code and it looks mirrored against the GUI, that is why.

---

## 8. Why the analysis is retroactive

Both pure-analysis modules are written so that **old files still parse**, and
that is a deliberate constraint, not an accident.

- `load_dc_hysteresis_csv()` reads the `#` header into a `meta` dictionary and
  the body into numeric arrays, skipping malformed rows rather than aborting.
- **X and Y are reconstructed from magnitude and phase when the Cartesian
  columns are absent**, and vice versa. Older acquisitions that recorded only
  $(|M|, \phi)$ therefore analyse identically to new ones.
- Missing columns fall back to sensible defaults: `cycle` is inferred from the
  branch names; `SMU_I_A` and `compliance_tripped` become NaN and 0; overload
  columns become zeros.
- Every classification threshold is a module-level constant in
  [`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py)
  and is overridable per call, so a whole archive can be re-classified under a
  new definition without re-measuring anything. Neither pure module imports a
  driver, so all of this runs on a laptop, in a notebook, or in CI.

The practical consequence is worth stating plainly: **you can improve the
analysis after the fact and apply it to every run you have ever taken.** That
is why the raw phasor samples, the provenance header and the flagged-but-kept
rows all exist. The acquisition's job is to record enough that a better
analysis is possible later; it is not to be the last word on the numbers.

---

**Related:** the physical meaning of the loop metrics is in
[Ferroelectric Switching](../physics/05-ferroelectrics.md); the normalisation
chain and the $r_\mathrm{eff}$ gates are in
[The Null-Slope Sénarmont Readout](../physics/03-senarmont-readout.md); every
column name is defined in the [Data Schema](../reference/data-schema.md), and
every term in the [Glossary](../reference/glossary.md).

---

<div align="center">

[← Instrument control](instrument-control.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Operating guide →](../guide/index.md)

</div>
