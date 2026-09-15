# CLI Reference

Every command-line flag, generated from the argument parsers in
[`pockels/pockels_fast_map_gui.py`](../../pockels/pockels_fast_map_gui.py) and
[`pockels/Pockels_Calibration_2026.py`](../../pockels/Pockels_Calibration_2026.py).

Every GUI setting has a flag here, and the GUI builds exactly these arguments
when it spawns the measurement worker, so a run started from the GUI and one
started from the terminal follow the identical code path. If you want to know
what the GUI will do, read `run_config.json` after a run: it records the
resolved value of everything below.

> **Note**
> Defaults quoted here are the values in the code. Some are adjusted at
> startup: the lock-in settle time and sample spacing are raised to
> $2 \times \mathrm{TC} \times \mathrm{filter\ order}$ (2.0 s at the default
> TC index 14 and 12 dB/oct), and a few flags imply others. For example,
> confirming the Sénarmont geometry forces the triplet readout, and supplying
> manual raw peak angles forces `--no-rotator-home`.

## Contents

1. [The measurement worker](#the-measurement-worker)
2. [The hysteresis and sweep engine](#the-hysteresis-and-sweep-engine)
3. [Analysis scripts](#analysis-scripts)
4. [Worked command lines](#worked-command-lines)

---

## The measurement worker

```bash
python pockels/pockels_fast_map_gui.py            # the GUI
python pockels/pockels_fast_map_gui.py --cli ...  # the same worker, headless
```

| Flag | Default | Choices | Description |
| --- | --- | --- | --- |
| `--chip-id` | *(none)* |  | Chip identifier stored in run metadata and new-run folder/file names |
| `--run-name` | *(none)* |  | Optional run label appended to the output folder |
| `--resume-run` | *(none)* |  | Resume an existing pockels_fast_map run folder |
| `--stage-cal` | *(none)* |  | Existing stage pixel_positions.json or run folder |
| `--optical-cal` | `latest` |  | Optical calibration JSON, 'latest', or 'none' |
| `--pixels` | `1-6,11-16,21-26,31-37,41-48,51-100` |  | Chip-map pixel list/ranges, e.g. '1-10,15,42', all, or none [default `1-6,11-16,21-26,31-37,41-48,51-100`] |
| `--skip-complete` | off |  | Skip completed pixels on resume |
| `--transport-recovery-attempts` | 0 |  | Reconnect attempts after transport loss; 0 keeps retrying until hardware returns or Stop is pressed [default 0] |
| `--transport-recovery-delay` | 5.0 |  | Initial reconnect delay in seconds; doubles up to 60 s [default 5] |
| `--no-realign-stage` | on |  | Use loaded calibrated stage coordinates without per-pixel realignment |
| `--hwp-input-frame` | `lab` | `lab`, `raw` | Interpret --hwp-start/stop/step as lab-frame theta_i or raw HWP degrees |
| `--hwp-sweep-mode` | `centered-180` | `centered-180`, `start-stop` | centered-180 uses --hwp-start as the centre. In lab frame it measures theta_i centre +/-90 deg; in raw frame it measures HWP raw centre +/-45 deg, which is still 180 deg of theta_i. start-stop uses --hwp-start/stop/step directly |
| `--hwp-points` | 9 |  | Odd point count for --hwp-sweep-mode centered-180 [default 9] |
| `--hwp-center-step` | *(none)* |  | Incident theta_i step for centered-180 mode. When --hwp-input-frame raw is used, the raw HWP motor step is half this value. |
| `--hwp-start` | 81.8688 (theta_i lab) |  | Start angle, or centre for centered-180 mode [default theta_i 81.8688 (theta_i lab) deg lab = HWP raw 7.8951 deg] |
| `--hwp-stop` | 165.0 |  |  |
| `--hwp-step` | 15.0 |  |  |
| `--manual-peak-hwp` | *(none)* |  | Optional exact raw HWP angle for a manual lock-in peak scout |
| `--manual-peak-qwp` | *(none)* |  | Optional exact raw QWP angle for a manual lock-in peak scout |
| `--manual-peak-anl` | *(none)* |  | Optional exact raw analyser angle for a manual lock-in peak scout |
| `--manual-peak-only` | off |  | Only measure the manual raw peak point(s), skipping the normal null/slope sweep |
| `--manual-peak-calibration-pixels` | 1 |  | Number of selected pixels that run the manual raw/anchor branch certification. Later pixels use the calibrated null/readout branch without extra manual-anchor reads. |
| `--calibration-pixel` | *(none)* |  | Physical chip pixel used first for per-HWP null calibration and full EO/readout calibration. It must be in --pixels. When omitted, the first selected pixel is used for backward compatibility. |
| `--auto-peak-calibration-pixels` | 1 |  | Number of pixels, starting with --calibration-pixel, that establish the full per-HWP sample null and normalized readout table [default 1]. The robust locked-QWP/triplet path adds a compact complex EO + DC Malus operating-point certificate, not a raw-magnitude peak search. |
| `--auto-peak-max-coarse-probes` | 31 |  | Maximum QWP/analyser seed probes for the first-HWP automatic peak search. Use 0 for the old exhaustive 143-probe coarse grid. |
| `--auto-peak-track-mode` | `locked-qwp` | `locked-qwp`, `confirm`, `refine`, `global`, `hybrid` | How later HWP angles are handled after the first automatic peak. locked-qwp keeps the QWP on the null-compensated branch and only refines the analyser near the theoretical +/-45 slope point; confirm measures the predicted QWP/ANL point once; refine runs a 2D QWP/ANL local maximizer around the prediction; global runs a full periodic QWP/ANL grid plus refinement at every HWP on the calibration pixel; hybrid runs the bounded coarse search once, then prediction plus 2D refinement for later HWP angles. |
| `--peak-refine-mode` | `two-point` | `two-point`, `fit`, `greedy` | Legacy peak-search analyser strategy; the standard locked-QWP/triplet workflow uses its separate adaptive S9 certificate and ignores this option. 'two-point' measures only analyser +45/-45; 'fit' solves the exact P + A*sin(2*psi+phi) model over the null/+45/-45 points plus two extra probes (~6 lock-in points, analytic peak, pickup phasor and R2 gate, greedy fallback on any gate failure); 'greedy' is the legacy hill-climb (~16 points). |
| `--learned-readout-mode` | `triplet` | `peak-only`, `zero-anchor`, `triplet` | How pixels after the calibration pixel use learned HWP peak angles. triplet [default] measures the local AC background and both opposite quadrature slopes for normalized phase/pickup rejection; zero-anchor omits the opposite slope; peak-only is a legacy speed diagnostic. |
| `--peak-diagnostic` | off |  | After each calibration peak readout, probe QWP/ANL +/- step and repeat the center to certify the lock-in peak for that HWP. |
| `--peak-diagnostic-step` | 5.0 |  | QWP/ANL step in degrees for peak certification probes [default 5.0] |
| `--peak-diagnostic-min-margin-uv` | 1.0 |  | Allowed amount by which a neighboring diagnostic probe may exceed the center before failing certification [default 1.0 uV] |
| `--post-analyser-sweep-pixels` | *(blank)* |  | Pixel list that should run analyser-angle sweeps before pixel cleanup. |
| `--post-analyser-sweep-vpp-values` | *(blank)* |  | Comma-separated AC Vpp values for post-fast-map analyser sweeps. |
| `--post-analyser-sweep-start` | 15.0 |  | Start analyser raw angle for post-fast-map analyser sweeps. |
| `--post-analyser-sweep-stop` | 195.0 |  | Stop analyser raw angle for post-fast-map analyser sweeps. |
| `--post-analyser-sweep-step` | 10.0 |  | Analyser raw-angle step for post-fast-map analyser sweeps. |
| `--post-analyser-sweep-dc-hold` | 40.0 |  | SMU DC hold voltage during post-fast-map analyser sweeps. |
| `--post-analyser-sweep-dwell` | 5.0 |  | Extra DC dwell before the first analyser sweep at each HWP. |
| `--post-analyser-sweep-all-hwp` | on |  | Run post-fast-map analyser sweeps at every saved HWP point instead of only the peak HWP. |
| `--post-ac-vpp-sweep-pixels` | *(blank)* |  | Pixel list that should run fixed-peak AC Vpp sweeps before pixel cleanup. |
| `--post-ac-vpp-sweep-values` | `1,3,5,7,9` |  | Comma-separated AC Vpp values for post-fast-map fixed-peak AC sweeps. |
| `--post-ac-vpp-sweep-dc-hold` | 40.0 |  | SMU DC hold voltage during post-fast-map AC Vpp sweeps. |
| `--post-ac-vpp-sweep-dwell` | 60.0 |  | DC/AC hold time before each post-fast-map AC Vpp measurement. |
| `--post-dc-hysteresis-pixels` | *(blank)* |  | Pixel list that should run fixed-peak DC hysteresis before pixel cleanup. |
| `--post-dc-hysteresis-dwell` | 30.0 |  | Per-step DC-only poling dwell for post-fast-map DC hysteresis sweeps [default 30.0 s]. Loop shape is rate-dependent - keep the dwell fixed within a campaign. |
| `--post-dc-hysteresis-reset-domains` | off |  | Run domain reset before each post-fast-map DC hysteresis sweep. |
| `--post-dc-hysteresis-start-from-zero` | False |  | Start post-fast-map DC hysteresis from 0 V. |
| `--post-dc-hysteresis-start-from-vmax` | on |  | Start post-fast-map DC hysteresis from +Vmax. |
| `--post-dc-hysteresis-cycles` | 1 |  | Full down+up loop cycles per pixel [default 1]. |
| `--post-dc-hysteresis-vmax` | 40.0 |  | Symmetric DC hysteresis endpoint in volts. May be reduced but cannot exceed 40.0 V [default 40.0 V]. |
| `--post-dc-hysteresis-voltage-step` | None (centre-dense grid) |  | Optional uniform DC voltage-spacing override. Unset uses the centre-dense profile; at the default +/-40 V limit this is 45 points with 1.25/2.5 V resolution near 0 V. |
| `--post-dc-hysteresis-ac-vpp` | 4.0 |  | AC probe amplitude (Vpp), gated on only for each post-poling lock-in measurement window [default 4.0 Vpp; <=0 keeps the fast-map peak Vpp]. |
| `--post-dc-hysteresis-dynamic-lockin-range` | False |  | Enable predictive hysteretic 5-200 uV RMS DSP7230 ranging for DC hysteresis only. Planned changes occur while the AC probe is off during the existing DC dwell. |
| `--post-dc-hysteresis-fixed-lockin-range` | on |  | Keep the selected lock-in sensitivity fixed during DC hysteresis. |
| `--post-dc-hysteresis-min-dwell` | 0.5 |  | Dwell floor for map-mode loops [default 0.5 s]. |
| `--post-dc-hysteresis-adaptive-window` | False |  | Coarse recon loop first, then centre the fine grid on the detected coercive voltages. When enabled, this overrides the standard grid and any uniform-step override. |
| `--post-dc-hysteresis-fixed-window` | on |  | Disable adaptive reconnaissance and use the standard centre-dense grid, or the configured uniform override. |
| `--post-dc-hysteresis-quasi-static` | False |  | Legacy physics mode: 30 s dwell floor, 1 cycle, peak Vpp dither, standard centre-dense voltage grid. |
| `--voltages` | `9.0` |  | Comma-separated AC Vpp list [default 9.0; use 1,3,5,9 for linearity] |
| `--anl-start` | 15.0 |  |  |
| `--anl-stop` | 195.0 |  |  |
| `--poling-voltage` | 40.0 |  |  |
| `--poling-dwell` | 180.0 |  | Per-pixel DC poling hold before fast-map reads [default 180 s; with --adaptive-poling this is the CAP, not the fixed time] |
| `--adaptive-poling` | True |  | v2: monitor the lock-in during poling and stop at plateau [default on; --poling-dwell becomes the cap]. |
| `--fixed-poling-dwell` | on |  | Disable adaptive poling; always sleep the full --poling-dwell. |
| `--adaptive-poling-min-s` | 60.0 |  | Minimum poling time before an early stop is allowed [60 s]. |
| `--adaptive-poling-poll-s` | 10.0 |  | Monitor sampling interval during poling [10 s]. |
| `--adaptive-poling-stable-pct` | 2.0 |  | Relative \|M\| change per interval counted as quiet [2 %%]. |
| `--adaptive-poling-stable-count` | 3 |  | Consecutive quiet intervals required to stop [3]. |
| `--adaptive-poling-min-signal-uv` | 2.0 |  | Minimum \|M\| (uV) for early stop; weaker pixels always get the full dwell [2 uV]. |
| `--adaptive-poling-monitor-vpp` | 0.0 |  | AC dither during poling monitor; <=0 uses the lowest --voltages value. |
| `--smu-port` | `auto` |  |  |
| `--smu-compliance` | 0.001 (1 mA) |  | SMU4201 source-voltage current compliance in amperes [default 0.001 A = 1 mA] |
| `--smu-ramp-step` | 5.0 |  |  |
| `--smu-ramp-dwell` | 0.2 |  |  |
| `--require-smu` | True |  | Stop if the SMU4201 cannot be opened |
| `--allow-fake-smu` | on |  | Allow FakeSMU fallback if the SMU4201 cannot be opened |
| `--lockin-settle` | 1.5 (raised to 2.0) |  |  |
| `--lockin-avg-readings` | 4 |  |  |
| `--lockin-read-delay` | 1.5 (raised to 2.0) |  |  |
| `--lockin-tc-index` | 14 (500 ms) |  | DSP7230 time-constant index [default 14 = 500 ms] |
| `--lockin-filter-slope-db` | 12 | `6`, `12`, `18`, `24` | DSP7230 output filter slope in dB/oct [default 12] |
| `--lockin-sensitivity-index` | 16 (200 uV FS) | `range(3, 28)` | Fixed DSP7230 voltage sensitivity index; the fast-map workflow never changes it automatically [default 16 = 200 uV RMS full scale] |
| `--lockin-reference-source` | `external-analog` | `external-analog`, `external-ttl`, `internal` | DSP7230 reference input [default external analog from function-generator CH2] |
| `--lockin-reference-tolerance-hz` | 1.0 |  | Minimum allowed DSP7230 reference-frequency error; 1 percent is also allowed |
| `--allow-unlocked-reference` | off |  | Continue despite a zero or mismatched DSP7230 FRQ readback |
| `--lockin-reference-phase` | 0.0 |  | Reference phase for signed mag*cos(phase-ref) calculation |
| `--qwp-readout-offset` | 0.0 |  | QWP offset from the AC-off null used for lock-in readout. 0 keeps the Abel null-compensation geometry; 45 deg is the separate quadrature-bias protocol. |
| `--require-lockin` | True |  | Stop if the DSP7230 lock-in cannot be opened |
| `--allow-fake-lockin` | on |  | Explicitly allow FakeLockin fallback if the DSP7230 cannot be opened |
| `--simulate-lockin` | off |  | Use FakeLockin immediately |
| `--funcgen-freq` | 30000 |  |  |
| `--funcgen-settle` | 0.2 |  |  |
| `--bto-wavelength-nm` | 1550.0 |  |  |
| `--bto-film-thickness-nm` | *(none)* |  |  |
| `--bto-film-thickness-std-nm` | *(none)* |  |  |
| `--bto-electrode-gap-um` | *(none)* |  |  |
| `--bto-electrode-gap-std-um` | *(none)* |  |  |
| `--bto-field-correction` | *(none)* |  | alpha in E = alpha*V_device/g; obtain for this electrode/beam geometry from FEM |
| `--bto-field-correction-std` | *(none)* |  |  |
| `--bto-refractive-index` | 2.1 |  |  |
| `--bto-refractive-index-std` | *(none)* |  |  |
| `--bto-device-vpp-scale` | *(none)* |  | measured device Vpp / programmed function-generator Vpp |
| `--bto-device-vpp-scale-std` | *(none)* |  |  |
| `--bto-detector-ac-gain-over-dc-gain` | *(none)* |  | lock-in channel V/W at modulation frequency divided by scope DC channel V/W |
| `--bto-detector-ac-gain-over-dc-gain-std` | *(none)* |  |  |
| `--bto-geometry-confirmed` | off |  | Confirm the null-slope Senarmont geometry and enable \|r_eff\| calculation |
| `--bto-sine-drive-confirmed` | off |  | Confirm CH1 is a zero-offset sine so Vpp/(2*sqrt(2)) is valid |
| `--no-laser` | on |  | Disable KLS1550 laser control |
| `--laser-power-mw` | 7.0 |  | KLS1550 laser absolute power setpoint in mW |
| `--laser-serial` | *(none)* |  | Optional KLS1550 serial; default auto-detect |
| `--laser-off-start` | on |  | Connect the KLS1550 but leave emission off at startup |
| `--arduino-port` | `auto` |  |  |
| `--arduino-baud` | 9600 |  |  |
| `--arduino-settle` | 0.2 |  |  |
| `--no-arduino` | off |  |  |
| `--allow-no-arduino` | off |  |  |
| `--manual-probing` | off |  | Pause after each pixel alignment for manual probe placement; disables Arduino routing |
| `--list-serial-ports` | off |  | List detected lab COM ports and exit |
| `--show-bluetooth-ports` | off |  | Include Bluetooth serial links in --list-serial-ports output |
| `--show-range-changes` | off |  | Show routine oscilloscope [RANGE] autorange messages |
| `--show-wrap-moves` | off |  | Show successful analyser [WRAP-MOVE] messages |
| `--min-signal-mv` | 50.0 |  |  |
| `--null-check-max-mv` | 14.5 |  | Substrate seed/adaptive null acceptance threshold in detector mV |
| `--null-certify-margin-mv` | 1.5 |  | Final detector-read continuation margin above --null-check-max-mv; marginal and high finite nulls continue with quality flags, while high nulls are not saved as trusted seeds/readouts [default 1.50] |
| `--stage-brighten-offset` | 45.0 |  | Analyzer offset from null used only for stage alignment brightness |
| `--conservative-rotator-moves` | on |  | Use slower boundary-safe chunked moves for analyzer slope points |
| `--renull-mode` | `adaptive-renull` | `seed-only`, `adaptive-renull`, `fast-renull` | seed-only uses the substrate HWP null table; adaptive-renull [default] re-nulls QWP/ANL only if the seed null check is high; fast-renull optimizes QWP/ANL at every pixel/HWP |
| `--no-verify-fast-rotator-moves` | on |  | Disable readback/correct-once verification for fast direct rotator moves |
| `--rotator-verify-tol` | 0.5 |  | Angle error tolerance in degrees before a fast move gets one corrective retry |
| `--rotator-velocity-pct` | 50 |  | Elliptec rotator velocity percent for continuous direct moves [default 50] |
| `--skip-stage-home` | off |  |  |
| `--no-camera` | off |  |  |
| `--no-rotator-home` | off |  |  |
| `--yes` | off |  | Skip prompts |
| `--phase-substrate` | `do` | `load`, `do`, `skip` | Load/do/skip substrate HWP null seeds [default do] |
| `--substrate-cal` | *(none)* |  | substrate_calibration.json or run folder |
| `--substrate-cal-pixel` | *(none)* |  | Advanced override for the physical pixel used if --phase-substrate do. By default --calibration-pixel is used; 0 keeps the current position. |
| `--substrate-null-tol` | 0.05 |  | Substrate calibration descent tolerance in degrees when doing Phase 1 |
| `--substrate-null-max-evals` | 220 |  | Maximum descent probes per HWP during substrate calibration |

---

## The hysteresis and sweep engine

```bash
python pockels/Pockels_Calibration_2026.py --hysteresis-only ...
python pockels/Pockels_Calibration_2026.py --ac-vpp-sweep-only ...
python pockels/Pockels_Calibration_2026.py --analyser-sweep-only ...
python pockels/Pockels_Calibration_2026.py --reset-domains
```

This is the script the GUI's follow-up queue spawns, so a queued loop and an
in-run loop are driven by identical parameters.

| Flag | Default | Choices | Description |
| --- | --- | --- | --- |
| `--yes` | off |  | Run non-interactively; skip Enter prompts and use CLI metadata defaults. |
| `--run-name` | *(none)* |  | Run name for non-interactive follow-up modes. |
| `--pixel-id` | *(none)* |  | Pixel/position label for non-interactive follow-up modes. |
| `--notes` | *(blank)* |  | Notes stored in run_info.txt for follow-up modes. |
| `--smu-compliance` | 0.001 (1 mA) |  | SMU4201 source-voltage current compliance in amperes [default 0.001 A = 1 mA]. |
| `--fixed-lockin-sensitivity-index` | *(none)* | `range(3, 28)` | Keep the DSP7230 on this voltage sensitivity for the entire worker run; overloads are reported without changing range (14 = 50 uV RMS full scale). |
| `--cal` | *(none)* |  | Path to calibration JSON (skips interactive calibration prompt) |
| `--hysteresis-only` | off |  | Skip Phase A (full sweep) and Phase B (peak find). Move motors to a known peak condition, drive the funcgen to the peak Vpp, and run only the DC hysteresis sweep. |
| `--ac-vpp-sweep-only` | off |  | Move motors to a known peak condition and run only a fixed-geometry AC Vpp linearity sweep. |
| `--analyser-sweep-only` / `--analyzer-sweep-only` | off |  | Move motors to a known peak condition, pole/hold the pixel, and sweep only the analyser angle while logging scope signal, lock-in magnitude, and lock-in phase. |
| `--peak-json` | *(none)* |  | Path to a dc_hysteresis_peak_conditions.json from a previous run. Required for --hysteresis-only unless --peak-* args are given. |
| `--peak-theta` | *(none)* |  | theta_i (HWP angle, deg) for hysteresis-only mode |
| `--peak-anl` | *(none)* |  | Analyzer angle (deg) for hysteresis-only mode |
| `--peak-qnull` | *(none)* |  | QWP null angle (deg) for hysteresis-only mode |
| `--peak-vpp` | *(none)* |  | V_AC (Vpp) for hysteresis-only mode |
| `--dwell` | *(none)* |  | Poling dwell (s) at each DC step [default 30.0] |
| `--ac-vpp-values` | `1,3,7,9` |  | Comma-separated AC amplitudes for --ac-vpp-sweep-only. |
| `--ac-dc-hold` | 40.0 |  | SMU DC hold voltage during --ac-vpp-sweep-only [default 40.0 V]. |
| `--ac-pre-measure-dwell` | 60.0 |  | DC/AC hold time before each fixed-peak AC Vpp measurement [default 60.0 s]. |
| `--anl-sweep-start` | 15.0 |  | Start analyser angle for --analyser-sweep-only [default 15.0 deg]. |
| `--anl-sweep-stop` | 195.0 |  | Stop analyser angle for --analyser-sweep-only [default 195.0 deg]. |
| `--anl-sweep-step` | 10.0 |  | Analyser angle step for --analyser-sweep-only [default 10 deg]. |
| `--anl-dc-hold` | 40.0 |  | SMU DC hold voltage during --analyser-sweep-only [default 40.0 V]. |
| `--anl-poling-dwell` | 30.0 |  | Extra DC hold time before the analyser sweep [default 30.0 s]. |
| `--anl-vpp` | *(none)* |  | Optional AC Vpp override for --analyser-sweep-only. If omitted, the saved peak Vpp is used. |
| `--anl-vpp-values` | *(none)* |  | Comma-separated AC Vpp values for --analyser-sweep-only. Used for per-HWP fast-map analyser sweeps unless --anl-vpp is supplied. |
| `--reset-domains` | off |  | AC-depole the BTO domains via SMU PULSe shape before the measurement. Combine with --hysteresis-only to depole then sweep, or use alone to depole and exit. |
| `--reset-vmax` | *(none)* |  | Depole peak amplitude V [default 40.0] |
| `--reset-vmin` | *(none)* |  | Depole final amplitude V (where envelope freezes) [default 0.05] |
| `--reset-amp-steps` | *(none)* |  | Depole amplitude steps [default 30] |
| `--reset-cycles-per-amp` | *(none)* |  | Depole cycles per amplitude [default 200] |
| `--reset-pulse-ms` | *(none)* |  | Depole pulse width (ms, sets both FIRSt and SECond) [default 5.0] |
| `--reset-decay` | *(none)* | `linear`, `exponential` | Depole envelope shape [default `exponential`] |
| `--hyst-cycles` | *(none)* |  | v2: full down+up loop cycles for DC hysteresis [default 1 = legacy; metrics use the last cycle]. |
| `--hyst-min-dwell` | *(none)* |  | v2: dwell floor (s) for DC hysteresis; unset keeps the legacy 30.0 s floor. |
| `--hyst-ac-vpp` | 4.0 |  | v2: AC probe (Vpp), gated on only after each DC-only poling dwell for the lock-in measurement; default 4.0 Vpp keeps the probe small vs the coercive window. |
| `--hyst-adaptive-window` | False |  | v2: coarse recon loop first, fine grid centred on the detected coercive voltages. |
| `--hyst-vmax` | 40.0 |  | Symmetric DC hysteresis endpoint in volts. May be reduced per run but cannot exceed 40.0 V [default 40.0 V]. |
| `--hyst-voltage-step` | *(none)* |  | Optional uniform DC hysteresis voltage-step override (V). Unset uses the centre-dense profile (45 points at the default +/-40 V); supplying a value overrides adaptive/fine-grid spacing. |
| `--hyst-tc-index` | *(none)* |  | v2: explicitly program the DSP7230 time-constant index for the loop (14 = 500 ms) instead of trusting front-panel state. |
| `--hyst-sensitivity-index` | *(none)* |  | Explicit starting DSP7230 voltage sensitivity index for the loop (14 = 50 uV RMS full scale); also the post-loop restore range when dynamic ranging is enabled. |
| `--hyst-dynamic-lockin-range` | False |  | Enable predictive hysteretic DSP7230 ranging during DC hysteresis only (5-200 uV RMS full scale). Normal changes occur with the AC probe off during the existing DC dwell. |
| `--hyst-fixed-lockin-range` | on |  | Keep the requested DSP7230 sensitivity fixed during hysteresis. |
| `--hyst-lockin-settle` | *(none)* |  | Per-point lock-in settle time (s); automatically raised to at least 5x the programmed time constant. |
| `--hyst-lockin-readings` | *(none)* |  | Number of lock-in readings averaged at each DC point. |
| `--hyst-lockin-read-delay` | *(none)* |  | Delay (s) between lock-in readings at each DC point. |
| `--hysteresis-live-json` | *(none)* |  | Optional GUI progress JSON path. The cumulative lock-in magnitude versus Vdc trace is replaced atomically after every hysteresis point. |
| `--start-from-zero` | *(none)* |  | Force hysteresis trajectory to start from 0 V (virgin curve) regardless of reset state. |
| `--start-from-vmax` | *(none)* |  | Force hysteresis trajectory to start from +V_max (saturated butterfly). |

---

## Analysis scripts

None of these touch hardware; they run anywhere.

| Script | Arguments |
| --- | --- |
| [`pockels/pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) | `paths` (files or glob patterns), `--sat-fraction` (0.8), `--gap-um`, `--alpha` (1.0) |
| [`pockels/make_hysteresis_maps.py`](../../pockels/make_hysteresis_maps.py) | `run_dir` (default: newest under `pockels_fast_map/`), `--out`, `--gap-um`, `--alpha` (1.0), `--reanalyse`, `--no-heal` |
| [`pockels/make_compositional_report.py`](../../pockels/make_compositional_report.py) | `run_dir`, `--composition <csv>` (`pixel,composition[,label]`), `--out`, `--clusters` (0 = auto), `--reanalyse`, `--no-heal` |
| [`pockels/make_fast_map_extra_plots.py`](../../pockels/make_fast_map_extra_plots.py) | `run_dir`, `--out-dir` |
| [`pockels/make_peak_hwp_angle_map.py`](../../pockels/make_peak_hwp_angle_map.py) | `run_dir` |
| [`pockels/smu4201_iv_sweep.py`](../../pockels/smu4201_iv_sweep.py) | none; edit the constants at the top of the file |
| [`pockels/arduino_switch_matrix.py`](../../pockels/arduino_switch_matrix.py) | `--port`, `--baud`, plus an interactive serial-monitor mode |

---

## Worked command lines

**Production chip map**

```bash
python pockels/pockels_fast_map_gui.py --cli \
  --chip-id BTNO_0087 \
  --pixels 1-6,11-16,21-26,31-37,41-48,51-100 \
  --calibration-pixel 46 \
  --hwp-points 9 \
  --voltages 9 \
  --phase-substrate load --substrate-cal <path/to/substrate_calibration.json> \
  --renull-mode adaptive-renull \
  --learned-readout-mode triplet \
  --poling-voltage 40 --poling-dwell 180 \
  --lockin-tc-index 14 --lockin-sensitivity-index 16 \
  --post-dc-hysteresis-pixels 46,85 \
  --require-lockin --require-smu --yes
```

**Single-pixel shakedown.** Always do this before committing a campaign.

```bash
python pockels/pockels_fast_map_gui.py --cli \
  --chip-id BTNO_0087 --run-name shakedown \
  --pixels 1 --calibration-pixel 1 \
  --phase-substrate load --substrate-cal <path> \
  --require-lockin --require-smu --yes
```

**AC-linearity diagnostic.** This is the only way to satisfy the
$R^2 \ge 0.98$ linearity gate, which needs three or more drive levels.

```bash
python pockels/pockels_fast_map_gui.py --cli --pixels 46 --voltages 1,3,5,9 ...
```

**Standalone hysteresis on a previously mapped pixel**

```bash
python pockels/Pockels_Calibration_2026.py --hysteresis-only \
  --peak-json <run>/pockels_pixels/pixel_046/peak_response.json \
  --hyst-cycles 1 --hyst-vmax 40 --hyst-ac-vpp 4 \
  --hyst-tc-index 14 --hyst-sensitivity-index 16 \
  --dwell 30 --yes
```

**Diagnostics**

```bash
python pockels/pockels_fast_map_gui.py --cli --list-serial-ports
python pockels/pockels_fast_map_gui.py --cli --list-serial-ports --show-bluetooth-ports
python pockels/pockels_fast_map_gui.py --cli --show-range-changes --show-wrap-moves ...
```

**Motion and timing test with no instruments.** It produces fiction, never data.

```bash
python pockels/pockels_fast_map_gui.py --cli --pixels 1 --chip-id DEBUG \
  --phase-substrate skip --simulate-lockin --allow-fake-smu --allow-no-arduino --yes
```

---

<div align="center">

[← Troubleshooting](../guide/troubleshooting.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Data schema →](data-schema.md)

</div>
