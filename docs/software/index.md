# The Software

**What this section is for:** the code that turns the optical bench into an
instrument. This page gives you the shape of the system: the rules that
explain why it is written the way it is, what every module does, how they
depend on one another, and where to go next.

The measurement package lives in [`pockels/`](../../pockels), flat, with
sibling imports. The unit tests live in [`tests/`](../../tests). There is no
version split and no nested package: one directory, one import root, one
working directory.

---

## Four design principles

Almost every structural decision in this code follows from one of four rules.
If a piece of code looks over-engineered, it is usually because one of these
rules made it so.

| Principle | What it forces in the code |
| --- | --- |
| **Never lose acquired data.** | Every CSV is written incrementally, row by row, as the measurement happens. All post-processing runs inside `try/except`, so a plotting bug can never destroy a measurement. A pixel that fails part-way through is saved as **partial**, not discarded, and a resumed run picks it up from `automation_progress.json`. |
| **Never leave the hardware energised.** | Output shutdown lives in `finally` blocks and context managers, never in the happy path. `ensure_fast_map_outputs_off()` turns off *both* the function-generator drive channel and the SMU, **verifies** each one, and escalates to full hardware recovery if it cannot confirm. `routed_arduino_channel()` opens the switch matrix on exit including on exception. |
| **Fail closed on physics, fail open on telemetry.** | A missing SMU terminal-voltage readback aborts the measurement point *before* the AC drive turns on, because that reading is physics. A lock-in *configuration* readback that returns an empty string does **not** abort: it is a known DSP7230 firmware quirk, and killing a seven-hour run over a cosmetic query would be the worse failure. |
| **Physics maths must be testable without hardware.** | [`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py) and [`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) import no instrument drivers at all. They are covered by unit tests that run on any machine, with no PyVISA, no pythonnet and no display. |

> **Note**
> The fourth principle is what makes the test suite possible. The 23 test files
> in [`tests/`](../../tests) either import the pure-analysis modules directly or
> parse the large hardware modules with `ast` and check their constants and
> control flow as *source*, without ever executing an instrument call.

---

## Module map

### Entry points: the things you actually run

| Module | Lines | Role |
| --- | --- | --- |
| [`pockels_fast_map_gui.py`](../../pockels/pockels_fast_map_gui.py) | ~22 800 | The whole operator-facing system. The Tkinter GUI **and** the measurement worker live in this one file: running it opens the GUI, running it with `--cli` runs the measurement headlessly. |
| [`Pockels_Calibration_2026.py`](../../pockels/Pockels_Calibration_2026.py) | ~5 200 | The deep single-pixel campaign. Hosts `run_dc_hysteresis_sweep()`, `reset_domains_pulsed()`, the AC-Vpp sweep, the function-generator and SMU command layers, and a standalone `--hysteresis-only` mode. |
| [`stage_calibration.py`](../../pockels/stage_calibration.py) | ~5 100 | The interactive stage/pixel calibration tool. It is **also** the library that owns the XY stage, the oscilloscope detector, the camera live view, and every alignment algorithm. |
| [`sample_calibration.py`](../../pockels/sample_calibration.py) | ~870 | The guided three-step sample-in optical calibration, driven by the GUI as a child process. |
| [`make_hysteresis_maps.py`](../../pockels/make_hysteresis_maps.py) | ~430 | Chip heat maps (21 metric layers), the loop-type map and the loop gallery. |
| [`make_compositional_report.py`](../../pockels/make_compositional_report.py) | ~410 | Cross-metric analytics: correlations, composition joins, trends, clustering. |
| [`make_fast_map_extra_plots.py`](../../pockels/make_fast_map_extra_plots.py), [`make_peak_hwp_angle_map.py`](../../pockels/make_peak_hwp_angle_map.py) | ~710 / ~280 | Additional figure generation from a finished run. |
| [`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) | ~1 340 | Also a command-line tool: point it at a `dc_hysteresis.csv` and it produces loop metrics and plots. |
| [`arduino_switch_matrix.py`](../../pockels/arduino_switch_matrix.py) | ~690 | Also an interactive CLI that mimics the Arduino IDE serial monitor. |
| [`smu4201_iv_sweep.py`](../../pockels/smu4201_iv_sweep.py) | ~230 | Also a standalone I-V sweep utility. |

### Orchestration: the code that sequences a run

| Module | Lines | Role |
| --- | --- | --- |
| [`pockels_full_automation.py`](../../pockels/pockels_full_automation.py) | ~2 600 | Shared bring-up: instrument connection, substrate calibration, per-pixel alignment, Arduino routing, SMU safety ordering, teardown. `FakeLockin` and `FakeSMU` live here. |
| [`pockels_campaign.py`](../../pockels/pockels_campaign.py) | ~1 630 | Null-search primitives (`first_null_fast`, `fast_renull`) and the campaign scaffolding. |
| [`analyser_sweep_voltage_series.py`](../../pockels/analyser_sweep_voltage_series.py) | ~830 | The analyser-sweep inner loop, function-generator helpers, and `read_lockin_averaged()`. |
| [`POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py) | ~2 670 | The original chip-test script. The rest of the package imports its hardware constants (COM ports, scope address, detector calibration, stage serials), its `DetectorTekTBS` class, and its accurate rotator move helpers `safe_move_abs()` / `safe_move_abs_fast()`. |
| [`pockels_transport_recovery.py`](../../pockels/pockels_transport_recovery.py) | ~310 | The reconnect-and-verify state machine. **Imports no drivers**, so it is unit-testable. |
| [`pockels_lockin_ranging.py`](../../pockels/pockels_lockin_ranging.py) | ~250 | `PredictiveHystereticRangeController`, the optional hysteresis-only ranging predictor. Also driver-free. |
| [`main_control_classes.py`](../../pockels/main_control_classes.py) | ~650 | Legacy shared control classes, retained for reference only. Imported by nothing in `pockels/` or `tests/`; its detector conversion treats the PDA30B2 as a bare photodiode into 1 MΩ and is superseded by `volts_to_watts_scale()` in `POL_Chip_Test_Working_2026.py`. |
| [`_bootstrap.py`](../../pockels/_bootstrap.py) | 50 | Import and working-directory pinning (see below). |

### Hardware drivers: one module per instrument

| Module | Lines | Instrument |
| --- | --- | --- |
| [`elliptec_serial.py`](../../pockels/elliptec_serial.py) | ~465 | Thorlabs Elliptec ELL14 rotators (HWP, QWP, analyser) over a shared 9600 8N1 serial bus. |
| [`Lock_In_Mag_Phase_Track.py`](../../pockels/Lock_In_Mag_Phase_Track.py) | ~1 040 | Signal Recovery DSP7230 lock-in, plus a standalone live magnitude/phase viewer. |
| [`smu4201_iv_sweep.py`](../../pockels/smu4201_iv_sweep.py) | ~230 | Aim-TTi SMU4201 SCPI driver. |
| [`kls1550.py`](../../pockels/kls1550.py) | ~150 | Thorlabs KLS1550 1550 nm laser via the Kinesis .NET API. |
| [`stage_xy.py`](../../pockels/stage_xy.py) | ~140 | Two-axis KCube stepper wrapper (used by the older control classes). |
| [`arduino_switch_matrix.py`](../../pockels/arduino_switch_matrix.py) | ~690 | The 100-channel Arduino/PhotoMOS switching matrix. |
| [`serial_port_resolver.py`](../../pockels/serial_port_resolver.py) | ~340 | Identity-based COM-port resolution, so a renumbered USB port does not need a Device Manager edit. |

Instrument addresses, gains and serial numbers are catalogued in
[Instruments](../experiment/instruments.md); the switching matrix has its own
page, [The Switch Matrix](../experiment/switch-matrix.md).

### Pure analysis: no hardware imports, fully unit-tested

| Module | Lines | Role |
| --- | --- | --- |
| [`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py) | ~1 390 | Phasor statistics, Sénarmont normalisation, the DC Malus fit, the complex analyser fit, the joint operating-point solution, the signed angular fit, the voltage-linearity fit and the $r_\mathrm{eff}$ gating. |
| [`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) | ~1 340 | Signed-loop projection, the full loop metric set, `classify_loop()` and the stretched-exponential poling-kinetics fit. |
| [`pockels_angular_plots.py`](../../pockels/pockels_angular_plots.py) | ~260 | Polar and diagnostic angular plots built from a pixel summary. |

---

## How the modules depend on each other

```text
  pockels_fast_map_gui.py            (GUI + --cli measurement worker)
        │
        ├──▶ pockels_full_automation.py          (bring-up, per-pixel loop helpers)
        │          │
        │          ├──▶ Pockels_Calibration_2026.py   (funcgen / SMU / hysteresis engine)
        │          │          ├──▶ elliptec_serial.py
        │          │          ├──▶ Lock_In_Mag_Phase_Track.py
        │          │          ├──▶ smu4201_iv_sweep.py
        │          │          ├──▶ POL_Chip_Test_Working_2026.py
        │          │          ├──▶ pockels_campaign.py
        │          │          └──▶ pockels_lockin_ranging.py     (pure)
        │          │
        │          ├──▶ stage_calibration.py      (stage, scope detector, camera, alignment)
        │          │          └──▶ arduino_switch_matrix.py
        │          │
        │          ├──▶ analyser_sweep_voltage_series.py
        │          ├──▶ POL_Chip_Test_Working_2026.py
        │          └──▶ serial_port_resolver.py
        │
        ├──▶ pockels_measurement_analysis.py     (pure)
        ├──▶ pockels_hysteresis_analysis.py      (pure)
        ├──▶ pockels_angular_plots.py            (pure)
        └──▶ pockels_transport_recovery.py       (pure)

  make_hysteresis_maps.py ──▶ pockels_hysteresis_analysis.py
  make_compositional_report.py ──▶ make_hysteresis_maps.py, pockels_hysteresis_analysis.py
```

Everything flows downward, and the pure modules at the bottom have no upward
dependencies. That single property is what lets the whole test suite run on a
laptop with no instruments attached.

---

## `pockels/_bootstrap.py`

Every entry script starts with one line:

```python
import _bootstrap  # noqa: F401  # pin CWD to the repo root so data is shared
```

It does exactly two things, and **the order matters**:

1. **Pins `sys.path[0]` to `pockels/`.** When you run `python pockels/x.py`,
   Python already puts the script's directory first on the path. But Spyder's
   `runfile` executes the file *inside the running kernel* without adding the
   script directory, so sibling imports would then be resolved through the
   current working directory instead. Pinning the path makes
   `import stage_calibration` mean the same module in every launch mode.
2. **Changes the working directory to the repository root.** Every run output
   (`pockels_fast_map/`, `pockels_calibration/`, `stage_calibration/`,
   `calibration_results_*/`) and every calibration-discovery path is
   CWD-relative. Pinning the working directory means CLI runs, Spyder runs,
   double-clicked runs and GUI-spawned child workers all agree on where
   "here" is.

Net effect: **data always lands in one predictable place, and code always
resolves to this package.** The module is idempotent (Spyder's autoreload can
re-execute it freely) and it announces itself at most once per process.

The test suite has its own, much smaller shim,
[`tests/_bootstrap.py`](../../tests/_bootstrap.py). It puts `pockels/` on
`sys.path` and exposes `module_path(name)`, which returns the path of a module
inside the package so a test can read its source with `ast` **without importing
it**. That is how tests can assert things about the 22 800-line GUI module
without loading PyVISA or pythonnet.

---

## The rest of this section

| Page | What it covers |
| --- | --- |
| [Architecture](architecture.md) | The two-process model and why processes rather than threads; the three kinds of child process; the complete file-based IPC contract; the five phases of a run; the measurement decision tree that separates the calibration pixel from production pixels; the escalation policy; the exception hierarchy; and the transport-recovery state machine. |
| [Motion Control](motion-control.md) | How motors actually move. The Elliptec wire protocol and encoder arithmetic, the accurate and fast rotator paths, backlash and verification, the Kinesis stage stack and its over-travel quirk, and every stage-alignment search algorithm with its real constants. |
| [Instrument Control and Timing](instrument-control.md) | How one measurement is taken. The anatomy of a lock-in point, why phasors are averaged in X/Y, the fixed-range policy, function-generator gating, the SMU sequences and per-point electrical audit, switch-matrix routing, adaptive poling, and the complete wall-clock timing budget. |
| [Numerical Methods and Algorithms](algorithms.md) | The coding theory. Every non-trivial algorithm in the codebase: the problem it solves, the mathematics, why this method rather than another, its cost and convergence behaviour, the constants the code actually uses, and the original source. |
| [The Data Pipeline](data-pipeline.md) | What is computed automatically during a run versus manually afterwards, the output folder tree, which column is "the signal", the quality-flag system, the offline analysis scripts, and how to load the data in Python. |

**Related reading elsewhere in this site:** the optical theory the code
implements is in [The Null-Slope Sénarmont Readout](../physics/03-senarmont-readout.md)
and [Ferroelectric Switching](../physics/05-ferroelectrics.md); the bench
itself is in [Instruments](../experiment/instruments.md); to actually run
something, start at the [Quick Start](../guide/quickstart.md) and then
[Operating the Instrument](../guide/operating.md). Every flag is listed in the
[CLI Reference](../reference/cli.md), every column in the
[Data Schema](../reference/data-schema.md), and every piece of jargon in the
[Glossary](../reference/glossary.md).

---

<div align="center">

[← The switching matrix](../experiment/switch-matrix.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Architecture →](architecture.md)

</div>
