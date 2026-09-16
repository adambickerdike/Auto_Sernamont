# Data Schema

Every file the system writes, and what every column means. Use this page when
you are reading a run in Python, Excel or Origin and need to know which column
to trust.

The long column lists are inside collapsible `<details>` blocks so the page
stays navigable; click any of them to expand.

## Contents

1. [Where data lives](#1-where-data-lives)
2. [Run folder tree](#2-run-folder-tree)
3. [Run-level files](#3-run-level-files)
4. [`fast_map_all_pixels.csv`: the chip summary](#4-fast_map_all_pixelscsv-the-chip-summary)
5. [`fast_map.csv`: the per-point record](#5-fast_mapcsv-the-per-point-record)
6. [`fast_map_summary.json`](#6-fast_map_summaryjson)
7. [Hysteresis files](#7-hysteresis-files)
8. [`dc_hysteresis_metrics.json`: the loop metrics](#8-dc_hysteresis_metricsjson-the-loop-metrics)
9. [Other per-pixel files](#9-other-per-pixel-files)
10. [Calibration files](#10-calibration-files)
11. [Quality flags](#11-quality-flags)
12. [Loading the data in Python](#12-loading-the-data-in-python)

---

## 1. Where data lives

All output paths are relative to the **repository root**.
[`pockels/_bootstrap.py`](../../pockels/_bootstrap.py) pins the working
directory there before anything else happens, so every launch mode (CLI,
Spyder `runfile`, double-click, GUI child worker) writes to the same place and
discovers the same calibrations.

| Folder | Contents |
| --- | --- |
| `pockels_fast_map/` | Fast-map runs, the main output |
| `pockels_calibration/` | Deep-campaign and standalone hysteresis runs |
| `calibration_results_withSample/` | Guided sample-in optical calibrations |
| `calibration_results_3step/` | 3-step optical calibrations |
| `stage_calibration/` | Stage pixel calibrations, `pixel_stage_calib.json` |
| `pockels_campaign/` | Campaign runs, `first_null_reference.json` |
| `analyser_sweep_voltage_series/` | Standalone analyser sweeps |
| `smu4201_sweeps/` | Standalone I-V sweeps |

> **Warning** **All of these are git-ignored.** Measurement data does not belong
> in the repository, and nothing in version control will bring it back. Back up
> each run folder separately, the day it is taken.

---

## 2. Run folder tree

```text
pockels_fast_map/20260915_143012_BTNO_0087_shakedown/
│
├── run_config.json                    every setting the run was given
├── lockin_configuration.json          the exact lock-in setup applied
├── loaded_optical_calibration.json    the optical calibration that was loaded
├── substrate_calibration.json         the per-HWP null seed table (if built here)
├── null_seed_table.json               the live/learned seed table
├── automation_progress.json           resume state
├── transport_recovery.jsonl           one line per transport incident (if any)
├── gui_state.json                     live GUI state (also useful post-hoc)
├── gui_alignment.json                 live alignment trace
├── gui_live_camera.png                last camera frame
├── fast_map_all_pixels.csv            ← THE CHIP SUMMARY (start here)
├── <run-name>_fast_map_all_pixels.csv  date/chip-bearing alias of the above
│
├── stage_alignment/
│   ├── pixel_positions.json / .csv    arrived + calibrated positions
│   ├── images/                        arrival + aligned camera frames
│   └── alignment_logs/                per-pixel search logs and diagnostics
│
├── pockels_pixels/
│   └── pixel_046/
│       ├── pixel_run_info.json
│       ├── fast_map.csv               ← THE PER-POINT RECORD (~140 columns)
│       ├── fast_map_raw_samples.csv   every individual lock-in sample
│       ├── fast_map_null_reference.csv null-point rows only
│       ├── fast_map_peak_diagnostics.csv  (if --peak-diagnostic)
│       ├── fast_map_summary.json      best conditions, fits, gate status
│       ├── peak_response.json         the single best point
│       ├── peak_lock_table.json/.csv  learned per-HWP readout table
│       ├── null_seed_by_hwp.json      nulls found at this pixel
│       ├── hwp_XXX.Xdeg_null_info.json per-HWP null detail
│       ├── fast_map_angular_response.csv   normalised δ and Γ vs θᵢ
│       ├── fast_map_angular_polar.png/.pdf the four-lobed polar plot
│       ├── fast_map_angular_diagnostics.png/.pdf
│       ├── poling_kinetics.csv        the τ, β fit input
│       └── dc_hysteresis/
│           ├── dc_hysteresis_live.json    cumulative atomic live trace
│           ├── recon_coarse/              (only in adaptive-window mode)
│           └── sweep/
│               ├── dc_hysteresis.csv          ← THE LOOP DATA
│               ├── dc_hysteresis_raw_samples.csv
│               ├── dc_hysteresis_conditions.json
│               ├── dc_hysteresis_metrics.json ← THE LOOP METRICS
│               ├── dc_hysteresis.png          3-panel acquisition plot
│               └── dc_hysteresis_loops.png    4-panel signed-loop plot
│
├── hysteresis_maps/                   (produced by make_hysteresis_maps.py)
│   ├── hysteresis_metrics_all_pixels.csv
│   ├── map_<metric>.png               21 annotated 10×10 heatmaps
│   ├── map_hysteresis_type.png        categorical loop-type map
│   └── map_loop_gallery.png           every pixel's loop at its chip position
│
└── compositional_report/              (produced by make_compositional_report.py)
    ├── compositional_metrics.csv      the joined mega-table
    ├── correlation_matrix.png         Spearman ρ across all metrics
    ├── merit_scatter.png, trends_*.png, cluster_map.png, tau_map.png …
```

---

## 3. Run-level files

### `run_config.json`

The complete record of what the run was asked to do: script name, mode, run
directory, `chip_id`, `run_name`, the selected pixel list **in the order the
pixels were measured**, calibration paths, the HWP grid (frame, mode, centre,
step, point count, and the resulting angle list in both raw and lab frames), AC
voltages, poling settings, null thresholds, lock-in settings, follow-up settings
and the `bto_physics` block.

> **Note** This is your provenance record. When a plot looks odd six months
> later, this file tells you exactly how it was taken.

### `lockin_configuration.json`

What was actually applied to the DSP7230, and what was deliberately skipped.

<details>
<summary><b>Key table</b></summary>

| Key | Meaning |
| --- | --- |
| `time_constant_index_requested` / `_s_requested` | TC index and seconds |
| `filter_slope_db_per_oct_requested` / `_index_requested` | filter slope |
| `sensitivity_index_requested` / `_fullscale_v_requested` | fixed range |
| `sensitivity_mode` | always `fixed` for the map |
| `automatic_ac_gain_enabled` | always `false` |
| `overload_policy` | `report_and_keep_fixed_range` |
| `settle_s_used`, `sample_spacing_s_used`, `averaged_phasor_samples` | the timing after auto-raising |
| `reference_source_requested` / `_index_requested` | external analog = 2 |
| `expected_reference_frequency_hz` | 30 000 |
| `configuration_mode` | `fixed_range_write_only` |
| `readback_queries_skipped` | `["TC.", "SLOPE", "IE", "SEN", "FRQ."]` |
| `reference_locked` | `true` / `false` / `null` |
| `simulated` | `true` if a fake lock-in was used; **always check this** |

</details>

### `automation_progress.json`

Resume state: the pixel list with per-pixel status, the accumulated records, the
null seed table, and the config. Written after every pixel.

### `transport_recovery.jsonl`

One JSON object per line, appended whenever a transport incident or a reconnect
attempt occurs. Its presence means the run survived a hardware dropout, so
inspect the affected pixels. See
[Operator Manual §8](../guide/operating.md#automatic-hardware-recovery).

---

## 4. `fast_map_all_pixels.csv`: the chip summary

One row per pixel. **This is the file to open first.** The full column list is
`CHIP_SUMMARY_FIELDS` in
[`pockels/pockels_fast_map_gui.py`](../../pockels/pockels_fast_map_gui.py).

<details open>
<summary><b>Columns</b></summary>

| Column | Meaning |
| --- | --- |
| `chip_id`, `pixel`, `row`, `col` | identity and grid position. `row` and `col` are **0-based internal** indices (`row = (pixel-1)//10`, `col = (pixel-1)%10`), not the GUI display column |
| `status` | `complete`, `partial`, `failed`, `skipped` |
| `best_hwp_deg`, `best_theta_i_deg`, `best_theta_i_legacy_deg` | the strongest incident polarisation: raw HWP, calibrated lab $\theta_i$, legacy 2 × HWP $\theta_i$ |
| `best_slope_side` | `plus45` / `minus45` |
| `best_anl_deg` (+ `_lab_deg`, `_theta_ana_deg`) | analyser angle at the best point, in raw / lab / analyser-frame degrees |
| `best_qwp_deg`, `best_qwp_readout_deg` (+ frames) | QWP null and readout angles |
| `best_a_null_deg` (+ frames) | the analyser null the best point was referenced to |
| `best_vpp` | drive amplitude at the best point |
| `max_abs_signed_response_V` | **the main ranking metric**: largest \|signed response\| |
| `max_signed_response_V` | the same, with its sign |
| `max_lockin_mag_V` | largest raw magnitude; use only as a rough screen |
| `best_mag_*` | the same family of conditions, selected by raw magnitude instead |
| `pair_response_V`, `pair_response_mag_V`, `pair_response_phase_deg` | the $(+45 - -45)/2$ pair combination, pickup-rejected |
| `delta_prime_proxy_V_per_Vpp` | response per Vpp, a quick throughput-naive comparator |
| `best_rotation_hwp_deg`, `best_rotation_theta_i_deg` | the HWP of the best **normalised** point |
| `rotation_slope_rad_per_Vrms` (+ `_sem_`) | **the physics observable**: rotation per RMS volt |
| `rotation_phase_deg` | phase of that rotation |
| `angular_fit_status`, `angular_fit_r_squared` | the signed $2\theta$ complex angular fit |
| `peak_selection_mode` | how the best point was chosen (fit, or largest certified) |
| `normalized_peak_certified` | whether the selected peak passed geometry certification |
| `voltage_linearity_r_squared` | the $\ge 0.98$ gate |
| `r_eff_abs_pm_per_V` (+ `_sem_`) | absolute coefficient, **usually blank by design** |
| `max_null_leakage_fraction` | worst null leakage as a fraction; gate is ≤ 0.05 |
| `r_eff_status` | why `r_eff` was or was not emitted |
| `n_points`, `n_trusted_points` | acquired versus usable point counts |
| `quality_flags` | `;`-separated flags; see [§11](#11-quality-flags) |
| `output_dir` | the pixel folder |

</details>

> **Rank pixels by `max_abs_signed_response_V` for a quick screen, but compare
> them physically with `rotation_slope_rad_per_Vrms`.** The raw response absorbs
> laser power, coupling and null depth; the rotation slope does not.

---

## 5. `fast_map.csv`: the per-point record

One row per (pixel, HWP, analyser position, Vpp), about 140 columns. The full
list is `FAST_MAP_FIELDS`.

<details>
<summary><b>Identity and geometry</b></summary>

| Column | Meaning |
| --- | --- |
| `pixel`, `row`, `col` | grid position |
| `hwp_deg`, `hwp_actual_deg`, `hwp_error_deg` | HWP target, readback, error |
| `theta_i_deg` | calibrated lab-frame incident polarisation |
| `theta_i_legacy_deg` | legacy 2 × HWP convention, for comparison with old runs |
| `hwp_actual_theta_i_deg` | $\theta_i$ implied by the *readback* |

</details>

<details>
<summary><b>Nulls</b></summary>

| Column | Meaning |
| --- | --- |
| `seed_q_null_deg`, `seed_a_null_deg`, `seed_p_null_W`, `seed_p_null_mV` | the inherited seed null and its leakage |
| `q_null_deg`, `a_null_deg`, `p_null_W`, `p_null_mV` | the null actually used |
| `qwp_axis_lab_deg`, `qwp_theta_wav_deg`, `a_null_lab_deg`, `a_null_theta_ana_deg` | the same angles in calibrated frames |
| `qwp_actual_deg`, `qwp_error_deg`, `anl_null_actual_deg`, `anl_null_error_deg` | readbacks and errors |
| `qwp_readout_deg` (+ frames, actual, error) | the QWP position used for the readout |
| `qwp_readout_offset_from_null_deg` | 0 for the standard Sénarmont geometry |
| `null_method` | how the null was obtained (`seed`, `adaptive`, `calibration_full_2d_coarse_fine`, …) |
| `null_n_evals`, `null_escalated` | search cost, and whether it escalated |
| `null_mV_before_renull`, `null_mV_after_renull` | leakage before and after a re-null |
| `q_delta_deg`, `a_delta_deg` | local correction relative to the seed |

</details>

<details>
<summary><b>Analyser position</b></summary>

| Column | Meaning |
| --- | --- |
| `slope_side` | `plus45`, `minus45`, `learned_anchor`, `learned_opposite`, or a background/bright label |
| `anl_target_deg` (+ frames) | commanded analyser angle |
| `anl_offset_from_null_deg` | $\psi$, the quantity the physics depends on |
| `anl_actual_deg`, `anl_error_deg` | readback and error |
| `chosen_side`, `peak_source`, `peak_track_mode`, `learned_readout_mode` | how this point was selected |

</details>

<details>
<summary><b>Operating-point (S9) certificate</b></summary>

All prefixed `s9_`, and written identically to every row of the HWP block. The
derivation and the thresholds are in
[Sénarmont readout](../physics/03-senarmont-readout.md).

| Column | Meaning |
| --- | --- |
| `s9_status` | `certified_fit`, `certified_triplet`, `geometry_only_low_signal`, `needs_fit`, `failed` |
| `s9_method`, `s9_reason`, `s9_n_fit_points` | how, why, how many points |
| `s9_complex_r_squared`, `s9_dc_r_squared` | fit qualities (gate 0.95 each) |
| `s9_dynamic_peak_offset_deg`, `s9_peak_shift_deg` | AC extremum versus DC quadrature (diagnostic; gate 5°) |
| `s9_dc_null_offset_deg` | fitted DC null versus the measured null (gate 5°) |
| `s9_null_leakage_fraction` | gate ≤ 0.05 |
| `s9_midfringe_fraction`, `s9_midfringe_error` | DC balance at the half-fringe (gate 0.075) |
| `s9_confirm_error_fraction` | confirmation-point agreement (gate 0.25) |
| `s9_fit_amplitude_V` | fitted EO amplitude |
| `s9_analyser_independent_fraction` / `s9_pickup_fraction` | the analyser-independent term $\lvert P\rvert$ as a fraction, **not** assumed to be pickup |
| `s9_derivative_residual_fraction` | how well the AC coefficients align with the DC derivative (gate 0.20) |
| `s9_temporal_rank_fraction` | whether $E_1$ and $E_2$ share one temporal phase (gate 0.15) |
| `s9_equivalent_rotation_mag_rad_rms`, `s9_equivalent_rotation_phase_deg` | the certified rotation |
| `s9_operating_psi_deg` | the fitted DC half-fringe operating angle |
| `s9_fit_signal_to_residual` | fit SNR |
| `s9_phase_flip_score` | do ±45° oppose? (quick gate ≥ 0.50) |
| `s9_side_symmetry_ratio` | are the two sides comparable? (quick gate ≥ 0.50) |
| `s9_dc_balance_error` | DC symmetry of the two slope points (quick gate ≤ 0.10) |

</details>

<details open>
<summary><b>The measurement itself</b></summary>

| Column | Meaning |
| --- | --- |
| `vpp` | AC drive amplitude |
| `power_W`, `detector_V` | scope DC reading at this analyser position |
| `detector_null_reference_V` | the DC level at the null, the second point of the Malus slope |
| `lockin_mag_V`, `lockin_phase_deg`, `lockin_x_V`, `lockin_y_V` | the raw averaged phasor |
| `lockin_*_std_V`, `lockin_*_sem_V`, `lockin_xy_cov_sem_V2` | dispersion and correlated uncertainty |
| `lockin_bg_*` | the background (null-point) phasor for this HWP block |
| `lockin_net_*` | **background-subtracted** phasor; *use these for physics* |
| `lockin_signed_V` (+ std, sem) | magnitude × cos(phase − reference); a cosmetic display projection |
| `sensitivity_fullscale_V`, `lockin_fullscale_fraction` | the range in force, and how much of it was used |
| `n_good_samples` | samples that survived filtering |
| `timestamp`, `elapsed_s` | when |
| `quality` | overall grade for the point |
| `quality_flags` | `;`-separated flags; see [§11](#11-quality-flags) |

</details>

> **Which column is "the signal"?**
> For physics, use **`lockin_net_x_V` and `lockin_net_y_V`**, the
> background-subtracted complex phasor. For a quick look,
> `lockin_net_mag_V`. `lockin_signed_V` is a display convenience only: the
> analysis re-derives its own phase reference from the data.

### `fast_map_raw_samples.csv`

Every individual lock-in sample before averaging: `pixel`, `hwp_deg`,
`theta_i_deg`, `slope_side`, `vpp`, `sample_index`, `sample_timestamp`,
`lockin_mag_V`, `lockin_phase_deg`, `lockin_x_V`, `lockin_y_V`,
`lockin_signed_V`. Use it to check noise, drift within a window, or an outlier.

---

## 6. `fast_map_summary.json`

| Key | Meaning |
| --- | --- |
| `best_*` | the same best-condition family as the chip summary |
| `best_hwp_fit` | sub-grid best HWP from a $\cos(4\cdot\mathrm{HWP})$ least-squares fit over the grid, with amplitude and $R^2$ (legacy diagnostic) |
| `angular_response_fit` | the production signed **$2\theta$** complex fit: complex coefficients, complex and magnitude $R^2$, continuous peak angle, dense theory curve |
| `angular_fit_status`, `angular_fit_r_squared` | fit outcome (selection falls back below $R^2 = 0.80$) |
| `angular_products` | paths of the generated angular CSV/PNG/PDF |
| `best_rotation_fit` | the rotation-versus-voltage fit: slope, SEM, intercept, $R^2$, voltage count |
| `r_eff` | the coefficient block: `status`, `reason`, value, SEM, `missing_inputs`, `model`, `voltage_convention`, `detector_convention`, `interpretation` |
| `normalized_peak_certified`, `peak_selection_mode` | how the operating point was chosen |
| `n_points`, `n_trusted_points`, `quality_flags` | counts and flags |

---

## 7. Hysteresis files

### `dc_hysteresis.csv`

Leading **`#` comment lines** record the complete provenance: run label,
$\theta_i$, AC Vpp, analyser peak angle, QWP/analyser nulls, the calibrated EO
phase axis and DC fringe levels copied from the chip sweep, `DC_HYST_VMAX`, the
grid profile and the **exact voltage level list**, the trajectory, the cycle
count, the dwell, all lock-in settings, the range mode and any dynamic-range
parameters, range-change and overload counts, SMU readback semantics and
tolerance, the compliance value, and the number of trips.

> **Note** Read it with `pd.read_csv(path, comment="#")`, otherwise pandas
> chokes on the header.

<details open>
<summary><b>Columns, one row per DC point</b></summary>

| Column | Meaning |
| --- | --- |
| `idx`, `t_s` | point index and elapsed time |
| `branch` | `virgin`, `sat`, `down1`, `up1`, `down2`, … |
| `cycle` | cycle number |
| `V_dc_V` | the DC set point |
| `SMU_V_set_V` | what was programmed |
| `SMU_V_measured_V` / `SMU_V_read_V` | the **measured terminal voltage** |
| `SMU_I_A` | DC-only current, measured with AC off → leakage |
| `compliance_tripped` | `True` → the row is excluded from metrics |
| `P_dc_W` | scope DC optical power |
| `LockIn_Mag_V`, `LockIn_Phase_deg` | the phasor |
| `LockIn_X_V`, `LockIn_Y_V` | Cartesian components; **the loop is built from these** |
| `LockIn_Mag_Std_V`, `LockIn_Phase_Std_deg`, `N_ok` | dispersion and sample count |
| `LockIn_Sens_V`, `LockIn_Sens_Index` | the range in force |
| `AC_Vpp`, `AC_measurement_window_s` | probe amplitude and how long it was on |
| `LockIn_Range_Mode` and the `LockIn_Range_*` family | predictive-ranging audit: prediction, noise prior, upper bound, requested index, change reason, whether it changed, whether a rescue re-read occurred |
| `LockIn_XY_Noise_Sigma_V` | estimated noise |
| `LockIn_Overload_Byte(_Observed)`, `LockIn_Status_Byte(_Observed)`, `LockIn_Output_Overload_Observed/_Final`, `LockIn_Input_Overload` | full overload audit |

</details>

### `dc_hysteresis_raw_samples.csv`

Every individual sample: `idx`, `V_dc_V`, `branch`, `range_attempt`, `sample_j`,
`sample_t_s`, magnitude, phase, sensitivity index and volts,
`Discarded_For_Range_Rescue`, and the overload/status bytes. Discarded emergency
windows are retained here and explicitly tagged.

### `dc_hysteresis_live.json`

The cumulative trace the GUI reads, rewritten **atomically** after every DC
point so a partially written file is never observed.

### Plots

- `dc_hysteresis.png`, a 3-panel acquisition view: magnitude, phase and current
  versus voltage.
- `dc_hysteresis_loops.png`, a 4-panel analysis view: the signed loop $S(V)$, the
  butterfly $|R|(V)$, the leakage $I(V)$, and the DC optical power.

---

## 8. `dc_hysteresis_metrics.json`: the loop metrics

```json
{
  "meta": {"source_csv": "...", "sat_fraction": 0.8, "n_points": 45,
           "n_cycles_analysed": 1, "n_tripped_excluded": 0,
           "n_lockin_overload_excluded": 0,
           "phi_ref_deg": ..., "phase_reference_source": "...",
           "phase_reference_axis_delta_deg": ...},
  "metrics": { ...headline values, from the LAST cycle... },
  "per_cycle": [ {...}, {...} ],
  "classification": {"primary": "...", "modifiers": [...],
                     "short_code": "...", "reasons": {...}},
  "cycle_to_cycle": {"loop_width_change_V": ..., "imprint_change_V": ...}
}
```

<details open>
<summary><b>The metric set</b></summary>

| Key | Definition | Physical meaning |
| --- | --- | --- |
| `v_c_plus_V`, `v_c_minus_V` | interpolated zero crossings of $S$ on the ascending / descending branch | coercive voltages |
| `loop_width_V` | $V_c^+ - V_c^-$ | hysteresis width |
| `imprint_V` | $(V_c^+ + V_c^-)/2$ | built-in internal bias |
| `s_rem_pos_V`, `s_rem_neg_V` | $S$ at $V = 0$ per branch | remanent EO response |
| `s_sat_pos_V`, `s_sat_neg_V` | mean $S$ over each saturation tail | saturated response |
| `squareness_pos`, `squareness_neg` | $S_\mathrm{rem}/S_\mathrm{sat}$ | loop squareness |
| `sat_asymmetry` | $(\lvert S_\mathrm{sat}^+\rvert - \lvert S_\mathrm{sat}^-\rvert)/\mathrm{mean}$ | electrode / interface asymmetry |
| `switchable_V` | $(S_\mathrm{sat}^+ - S_\mathrm{sat}^-)/2$ | switchable response |
| `switchable_corrected_V` | the same after removing the fitted linear term | pure hysteron amplitude |
| `frozen_V` | $(S_\mathrm{sat}^+ + S_\mathrm{sat}^-)/2$ | non-switchable + common mode |
| `quad_eo_slope_V_per_V` | linear fit of $S$ in the saturation tails | field-induced quadratic-EO / electrostrictive response |
| `quad_eo_ratio` | $\lvert\mathrm{slope}\rvert V_\mathrm{max}/\lvert\mathrm{switchable\_corrected}\rvert$ | paraelectric fraction / phase-boundary proximity |
| `loop_area_V2` | $\lvert\oint S\,dV\rvert$ | dissipation proxy |
| `loop_closure_V` | $\lvert S(\mathrm{end}) - S(\mathrm{start})\rvert$ at $+V_\mathrm{max}$ | drift / repeatability |
| `switching_slope_down`, `switching_slope_up` | $\lvert dS/dV\rvert$ peak position `peak_V_per_V`, height, `fwhm_V`, `mean_V`, `sigma_V`, `skewness` | the switching-field distribution |
| `switching_sigma_V`, `switching_skewness` | branch means of the above | disorder width and asymmetry |
| `nucleation_asymmetry` | $(h_\mathrm{up}-h_\mathrm{dn})/\mathrm{mean}$ of the $dS/dV$ peak heights | branch nucleation asymmetry |
| `butterfly_min_over_sat(_down/_up)` | $\min\lvert R\rvert/\lvert R\rvert_\mathrm{sat}$ | switching completeness (≈ 0 = clean 180° cancellation) |
| `transition_width_25_75_V` (+ `_down`, `_up`) | span between the 25 % and 75 % crossings | switching abruptness ($\approx 1.10\,w$ for a tanh branch) |
| `phase_intermediate_fraction_down/up` | fraction of coercive-window points 45 to 135° off axis | gradual rotation versus abrupt flip |
| `tanh_fit_down`, `tanh_fit_up` | $S = a + bV + S_s\tanh((V-V_c)/w)$ with errors and $R^2$ | model $V_c$, width, and the reversible linear term |
| `leakage` | `conductance_S`, `i_at_pos_sat_A`, `i_at_neg_sat_A`, `i_max_abs_A`, `offset_A` | conduction / defects |
| `quadrature_fraction` | $\max\lvert Q\rvert/\max\lvert S\rvert$ | projection validity (> 0.5 → invalid) |

</details>

### Loop-type taxonomy

<details open>
<summary><b>Primary types and criteria</b></summary>

| Primary type | Criterion (simplified) | Material reading |
| --- | --- | --- |
| `ferroelectric_square` | crossings on both branches, width ≥ 2 V, mean squareness ≥ 0.7 | uniform, well-switching |
| `ferroelectric_slanted` | squareness 0.3 to 0.7 | broad coercive-field distribution |
| `ferroelectric_rounded` | squareness < 0.3 | strong disorder / graded switching |
| `pinched` | the opening profile has ≥ 2 humps with a dip < 0.5× the smaller hump, and relative opening ≥ 0.15 | defect pinning / internal-bias pairs / antiferroelectric-like |
| `linear_no_hysteresis` | relative opening < 0.15, or width < 2 V | paraelectric-like reversible response |
| `frozen_response` | amplitude present but switchable < 1 µV | clamped / non-switchable |
| `partial_loop_unresolved` | hysteretic, but $V_c$ outside $\pm V_\mathrm{max}$ | sweep range insufficient |
| `no_response` | $\max\lvert S\rvert < 1$ µV | dead pad / no EO response |
| `invalid_projection` | quadrature fraction > 0.5 | contaminated measurement |

</details>

**Modifiers** (marked with `*` on the maps):

| Modifier | Criterion |
| --- | --- |
| `imprinted` | $\lvert\mathrm{imprint}\rvert > \max(3\ \mathrm{V},\ 0.5 \times \text{half-width})$ |
| `partially_frozen` | $\lvert\mathrm{frozen}/\mathrm{switchable}\rvert > 1$ |
| `leaky` | $G > 10$ nS |
| `drifting_loop` | closure / $\lvert\mathrm{switchable}\rvert > 0.3$ |
| `saturation_asymmetric` | $\lvert\mathrm{sat\_asymmetry}\rvert > 0.3$ |

All thresholds are module constants in
[`pockels/pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py)
and are overridable per call. The classification gallery is in
[the loop-taxonomy figure](../../assets/figures/loop_taxonomy.png); what each
shape means physically is in
[Ferroelectrics](../physics/05-ferroelectrics.md).

---

## 9. Other per-pixel files

| File | Contents |
| --- | --- |
| `poling_kinetics.csv` | `t_s`, `lockin_mag_V` sampled every 10 s during the poling dwell. `fit_poling_kinetics()` returns `tau_s`, `beta`, `m0_V`, `m_inf_V`, `r_squared` |
| `peak_response.json` | the single best measured point, with all its conditions |
| `peak_lock_table.json` / `.csv` | the learned per-HWP readout table: nulls, readout angles, chosen side, the metric used, the S9 fields, quality |
| `null_seed_by_hwp.json` | nulls found at this pixel, keyed by HWP |
| `hwp_XXX.Xdeg_null_info.json` | per-HWP null search detail: method, evaluations, escalation, final leakage |
| `fast_map_angular_response.csv` | normalised rotation $\delta$ and retardance $\Gamma$ versus calibrated $\theta_i$, with uncertainties; the input to the polar plot |
| `pixel_run_info.json` | per-pixel run context: stage position, alignment result, routing, timing |

---

## 10. Calibration files

### `substrate_calibration.json`

The per-HWP null seed table:

```json
{"hwp_deg": [...], "entries": [
   {"hwp_deg": 7.8951, "q_null": ..., "a_null": ...,
    "p_null_W": ..., "p_null_mV": ..., "n_evals": ..., "method": "..."},
   ...]}
```

Load it with `--phase-substrate load --substrate-cal <path>`.

### `sample_calibration.json`

From the guided 3-step calibration: analyser extinction and parallel angles
(`a_ref_min`, `a_ref_max`), QWP fast-axis zero `gamma0` and fitted retardance,
the sample-in null `(q_null, a_null, p_null)`, the HWP reference, the fit curves,
and the health-check results.

### `polarisation_lab_mapping_current.json`

The lab-frame conversion: the raw setpoints that define the lab reference axes
(`HWP_incident_y`, `QWP_axis_y`, `ANL_transmit_y`), the sign conventions (`s_H`,
`s_Q`, `s_A`), a `trusted` flag with a reason, and provenance. Used by
`lab_theta_i_from_hwp()`.

> **Warning** If `trusted` is false, $r_\mathrm{eff}$ is blocked. This file is a
> calibration *convention*, not measurement data, so unlike the run folders it
> is committed to the repository:
> [`pockels/polarisation_lab_mapping_current.json`](../../pockels/polarisation_lab_mapping_current.json).

### `pixel_positions.json` / `.csv`

From [`pockels/stage_calibration.py`](../../pockels/stage_calibration.py): per
pixel, the nominal / arrived / calibrated motor positions, the arrival and
calibrated detector voltages and powers, the scope V/div used, the position
errors in µm, the alignment method and evaluation count, image filenames, and
the `voltage_on_*` columns if voltage was applied at the calibrated peak.

---

## 11. Quality flags

`quality_flags` is a `;`-separated set. The important ones:

<details open>
<summary><b>Complete flag table</b></summary>

| Flag | Meaning | Severity |
| --- | --- | --- |
| `s9_operating_point_certified` | the operating point passed all gates | ✅ good |
| `s9_operating_point_failed` | it did not | ⚠️ do not trust the physics on this row |
| `s9_low_signal_geometry_only` | the geometry is right, the pixel is simply weak | ℹ️ informational |
| `high_null_leakage`, `high_null_after_adaptive`, `high_null_continued` | the null is worse than the threshold | ⚠️ |
| `calibration_null_hard_limit_exceeded` | a calibration-pixel null exceeded the hard limit, so it is excluded from trusted seeds | ⚠️ |
| `calibration_null_marginal_continued` | within the continuation margin | ℹ️ |
| `untrusted_high_null_fallback` | the readout fell back because the null was untrusted | ⚠️ |
| `plus_minus_same_sign` | the ±45° responses do **not** oppose | 🔴 probably pickup, not light |
| `plus_minus_asymmetric` | the two sides differ by more than 50 % | ⚠️ |
| `lockin_overload` | the lock-in overloaded | 🔴 row invalid |
| `lockin_range_exceeded` | ≥ 98 % of full scale | 🔴 row invalid |
| `lockin_range_near_fullscale` | ≥ 85 % of full scale | ⚠️ |
| `low_or_negative_power` | detector reading implausible | ⚠️ |
| `null_check_nan` | the null read returned a non-finite value | ⚠️ |
| `bto_linearity_unverified`, `bto_voltage_response_nonlinear` | fewer than 3 voltages, or linearity failed | ⚠️ blocks `r_eff` |
| `bto_null_leakage_too_high` | leakage > 5 % | ⚠️ blocks `r_eff` |
| `bto_qwp_retardance_invalid` | QWP retardance outside 90° ± 10° | ⚠️ blocks `r_eff` |
| `normalized_hwp_has_no_certified_motor_row` | no certified row at the selected HWP | ⚠️ |
| `manual_peak_scout_excluded_from_auto_peak`, `manual_anchor_included_in_peak_selection` | manual-anchor bookkeeping | ℹ️ |
| `peak_diagnostic_failed`, `_incomplete`, `_neighbor_within_margin`, `_center_repeat_drift` | peak-certification outcomes | ⚠️ |

</details>

The analysis module additionally treats `lockin_overload`,
`lockin_range_exceeded` and `s9_operating_point_failed` as **disqualifying**
(`DISQUALIFYING_ROW_FLAGS`): such rows are excluded from fits automatically.

---

## 12. Loading the data in Python

```python
import pandas as pd, json
from pathlib import Path

run = Path("pockels_fast_map/20260915_143012_BTNO_0087_shakedown")

# --- Chip-level screen -------------------------------------------------
chip = pd.read_csv(run / "fast_map_all_pixels.csv")
good = chip[chip.status.eq("complete") & chip.quality_flags.fillna("").eq("")]
print(good.nlargest(10, "max_abs_signed_response_V")[
      ["pixel", "best_theta_i_deg", "max_abs_signed_response_V",
       "rotation_slope_rad_per_Vrms", "r_eff_status"]])

# --- One pixel's points ------------------------------------------------
pts = pd.read_csv(run / "pockels_pixels/pixel_046/fast_map.csv")
ok  = pts[~pts.quality_flags.fillna("").str.contains(
          "lockin_overload|lockin_range_exceeded|s9_operating_point_failed")]

# The physics phasor is the background-subtracted one:
net = ok.lockin_net_x_V + 1j * ok.lockin_net_y_V

# --- Hysteresis loop, rebuilt the same way the analysis does -----------
import numpy as np
h = pd.read_csv(run / "pockels_pixels/pixel_046/dc_hysteresis/sweep/dc_hysteresis.csv",
                comment="#")
h = h[~h.compliance_tripped.astype(str).str.lower().eq("true")]
tail = h[h.V_dc_V.abs() >= 0.8 * h.V_dc_V.abs().max()]
phi  = np.angle((tail.LockIn_X_V + 1j * tail.LockIn_Y_V).sum())
S    = h.LockIn_X_V * np.cos(phi) + h.LockIn_Y_V * np.sin(phi)   # signed loop
Q    = -h.LockIn_X_V * np.sin(phi) + h.LockIn_Y_V * np.cos(phi)  # residual

# --- Or just read the metrics the software already computed ------------
m = json.loads((run / "pockels_pixels/pixel_046/dc_hysteresis/sweep"
                    / "dc_hysteresis_metrics.json").read_text())
print(m["classification"]["primary"], m["metrics"]["loop_width_V"],
      m["metrics"]["imprint_V"])
```

> **Note** Use `comment="#"` when reading `dc_hysteresis.csv`, because the
> provenance header lines start with `#`.

> **Display versus internal column.** `col` is the 0-based internal stage
> column. The GUI heat map draws `display_row = row + 1` and
> `display_col = 10 − col`, so display columns run 1…10, mirrored left to right
> to match the camera view. If you reproduce the map in your own plotting code
> and it looks mirrored against the GUI, that is why. See
> [the pixel grid figure](../../assets/figures/pixel_grid.png).

---

## See also

- [Glossary](glossary.md): every symbol and term used above.
- [CLI Reference](cli.md): the flags that set these values.
- [Data pipeline](../software/data-pipeline.md): how each file is produced.
- [Operator Manual](../guide/operating.md): the settings behind the columns.

---

<div align="center">

[← CLI reference](cli.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Glossary →](glossary.md)

</div>
