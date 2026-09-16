# Architecture

**What this page is for:** the runtime shape of the system. How the GUI and the
measurement talk to each other, what happens in what order when you press
*Start*, how the software decides what to measure at each pixel, and what it
does when something breaks. Read this before changing anything.

---

## Contents

1. [The two-process model](#1-the-two-process-model)
2. [The three kinds of child process](#2-the-three-kinds-of-child-process)
3. [File-based IPC](#3-file-based-ipc)
4. [Control flow of a run](#4-control-flow-of-a-run)
5. [The measurement decision tree](#5-the-measurement-decision-tree)
6. [Escalation and failure policy](#6-escalation-and-failure-policy)
7. [The exception hierarchy](#7-the-exception-hierarchy)
8. [The transport-recovery state machine](#8-the-transport-recovery-state-machine)
9. [Where to find things](#9-where-to-find-things)

---

## 1. The two-process model

[`pockels_fast_map_gui.py`](../../pockels/pockels_fast_map_gui.py) is two
programs in one file. Its `__main__` block reads:

```python
if "--cli" in sys.argv[1:]:
    cli_argv = [arg for arg in sys.argv[1:] if arg != "--cli"]
    raise SystemExit(main(cli_argv))
raise SystemExit(run_gui())
```

Launched plainly, it builds the Tkinter application. Launched with `--cli`, the
same file becomes the measurement worker. The GUI's job is to construct a
command line and then spawn *itself* with `--cli`.

```text
  ┌──────────────────────────────────┐            ┌────────────────────────────────────┐
  │  GUI process                     │   spawn    │  Worker process                    │
  │  FastMapGuiApp (Tkinter)         │ ─────────▶ │  main(argv)   (… --cli …)          │
  │                                  │            │                                    │
  │  • collects and validates config │            │  • owns ALL hardware sessions      │
  │  • builds the child argv         │   stdout   │  • runs the measurement            │
  │  • reads stdout → Terminal pane  │ ◀───────── │  • writes CSV/JSON incrementally   │
  │  • polls run files → live plots  │            │  • polls the IPC flag files        │
  │  • writes IPC flag files         │   files    │  • writes gui_state.json, …        │
  │  • owns NO hardware              │ ◀────────▶ │                                    │
  └──────────────────────────────────┘            └────────────────────────────────────┘
```

### Why processes and not threads

- **Tkinter must own the main thread.** A multi-hour blocking measurement in
  the same process would freeze the window: no Stop button, no plots, no log.
- **Crash isolation.** A driver segfault, an unhandled .NET exception from
  Kinesis, or a VISA error deep inside PyVISA kills the *worker*. The GUI
  survives, reports the exit code, and offers *Resume run*.
- **The worker is independently runnable.** Exactly the same code path runs
  headless from a terminal with `--cli`, so GUI behaviour and CLI behaviour
  cannot drift apart. It also means you can start a run over SSH and watch it
  later by pointing the GUI's *Resume run* at the folder, because the plots
  populate from the same files.

> **Note**
> The GUI process never opens an instrument session. If the GUI is showing a
> camera frame or a lock-in trace, it is reading a file the worker wrote, not
> talking to hardware. This is why two GUIs can watch the same run without
> fighting over a VISA resource.

---

## 2. The three kinds of child process

All three are launched with `subprocess.Popen`, with `stdout=subprocess.PIPE`,
`stderr=subprocess.STDOUT`, `text=True`, `bufsize=1`, `encoding="utf-8"` and
`errors="replace"` (so a stray non-UTF-8 byte from an instrument cannot kill
the reader thread), and with `PYTHONUNBUFFERED`, `PYTHONIOENCODING` and
`PYTHONUTF8` forced in the child environment. A daemon thread drains the pipe
line by line into the Terminal pane.

| Child | Launched by | Purpose | stdin |
| --- | --- | --- | --- |
| **Measurement worker** | *Start Measurement* | The run itself: `python -u pockels_fast_map_gui.py --cli --yes …`. | `DEVNULL` |
| **Sample calibration** | *Guided Sample-In …* | [`sample_calibration.py`](../../pockels/sample_calibration.py), the three-step optical wizard. | **`PIPE`**: this one asks questions, and the GUI answers them |
| **Follow-up queue** | *Historical Hyst Only*, *AC Vpp Sweep*, *ANL Sweep* | One job per pixel, executed strictly in sequence; the queue stops at the first non-zero exit code. | `DEVNULL` |

The child's executable is `sys.executable` and the script path is
`Path(__file__).resolve()`, so the GUI always launches the worker from the same
directory it is itself running from, never whatever happens to be on `PATH`.
`cwd` is set to that directory as well, and
[`_bootstrap`](../../pockels/_bootstrap.py) then re-pins the child's working
directory to the repository root, so run outputs land in the same place no
matter how the process was started.

---

## 3. File-based IPC

**All** GUI↔worker communication is through files inside the run directory.
No sockets, no shared memory, no pipes other than stdout.

This is deliberate. Files survive a crash on either side; they can be read,
diffed and archived by hand; they work identically whether the worker was
launched by the GUI or from a terminal; and they impose no ordering or liveness
requirement between two processes that may start and stop independently. A
socket would give lower latency for data that is updated a few times a second
at most, and would cost a connection lifecycle, a port, a protocol version and
a whole class of failure modes that files simply do not have.

| File | Direction | Purpose |
| --- | --- | --- |
| `.gui_stop_requested` | GUI → worker | Safe-stop flag. Polled by `gui_stop_checkpoint()` at every checkpoint; the worker finishes the current safe unit of work, saves, tears down and exits cleanly. |
| `.gui_skip_pixel_requested` | GUI → worker | Skip-current-pixel flag. Raises `SkipPixelRequested` at the next checkpoint. |
| `.gui_manual_probe_confirmed` | GUI → worker | "I have landed the probes, continue" in manual-probing mode. The worker blocks until it appears. |
| `gui_laser_command.json` | GUI → worker | Laser on/off and power setpoint changes *during* a run. The worker owns the KLS1550 session; the GUI can only ask. |
| `gui_state.json` | worker → GUI | The complete live state: phase string, current pixel / HWP / analyser, all three rotator angles, DC/AC/laser on-state, progress counters and the latest lock-in reading. |
| `gui_alignment.json` | worker → GUI | The live stage-alignment trace (every probe position and detector level) for the alignment plot. |
| `gui_live_camera.png` | worker → GUI | The latest camera frame, rewritten about every 0.15 s (`GUI_CAMERA_FRAME_INTERVAL_S`). |
| `automation_progress.json` | worker → both | Durable progress: per-pixel status, the accumulated records, the null seed table and the run config. This is the file *Resume* reads. |
| `fast_map_all_pixels.csv` | worker → GUI | The chip summary, one row per pixel. The GUI polls it to colour the chip map. |
| `pockels_pixels/pixel_NNN/fast_map.csv` | worker → GUI | The per-point record. The GUI tails it for the live lock-in panel. |
| `dc_hysteresis/dc_hysteresis_live.json` | worker → GUI | The cumulative hysteresis trace, rewritten **atomically** after every DC point, so the GUI can never read a half-written file. |
| `transport_recovery.jsonl` | worker → record | One JSON object per line, appended for every transport incident and every reconnect attempt. Its existence means the run survived a hardware dropout. |

The GUI's `_poll_files()` timer checks modification times and re-reads only
what changed, so a 140-column CSV that has not moved costs nothing.

> **Warning**
> The three flag files start with a dot and have no extension
> (`.gui_stop_requested`, `.gui_skip_pixel_requested`,
> `.gui_manual_probe_confirmed`). They are *presence* flags: their content is
> irrelevant. If a run refuses to start or immediately stops, check that a
> stale `.gui_stop_requested` is not sitting in the folder.

---

## 4. Control flow of a run

### Phase A: GUI validation, before anything is created

`_start_measurement()`:

1. Validate every field: Chip ID present; the calibration pixel is in range
   *and* inside the pixel selection; every number parses; the hysteresis limit
   is ≤ 40 V; and so on. Nothing is created while a field is invalid.
2. `_prepare_run_dir()` computes
   `pockels_fast_map/YYYYMMDD_HHMMSS_<chip>_<label>/` and refuses if it already
   exists (or resolves an existing folder, if this is a resume).
3. `_build_child_argv()` translates every GUI variable into an explicit CLI
   flag, including all the IPC file paths.
4. **Only now** `root_dir.mkdir(parents=True, exist_ok=False)`. The directory
   is created after every check has passed, so a rejected dialogue leaves no
   empty folders behind.
5. `subprocess.Popen`, start the stdout reader thread, enable *Stop* and
   *Skip*.

### Phase B: worker startup

`main(argv)`:

1. Parse and cross-validate the arguments, then derive the implied settings.
   For example, supplying exact raw peak angles (manual peak scout) forces
   `--no-rotator-home` so those coordinates cannot be re-tared out from under
   you; confirming the BTO geometry forces the `triplet` readout.
2. Bind the IPC file paths and install the stop/skip hooks.
3. `resolve_instrument_serial_ports()` does identity-based COM resolution, so a
   renumbered USB port is followed rather than guessed.
4. `configure_fast_globals()` pushes the settings into the shared module
   globals (`pockels.LOCKIN_SETTLE_S`, `auto.BRIGHTEN_OFFSET_DEG`, …) and
   **auto-raises** the lock-in settle time and sample spacing to
   $2 \times \mathrm{TC} \times \text{filter order}$ if the operator asked for
   less.
5. `make_fast_run_dir()`, load or create the resume state, load the calibrated
   stage pixel positions.
6. Resolve the pixel selection and the calibration pixel, build the work queue
   (the calibration pixel first), compute the HWP grid and the total point
   count.
7. **Connect the hardware, in this exact order:**

   ```text
   lock-in → XY stage → oscilloscope → camera live view → switch matrix →
   laser → function generator (drive channel OFF) → rotators →
   home/tare rotators → configure the fixed lock-in range →
   SMU (output OFF, armed at 0 V) → load the optical calibration
   ```

   The order is not arbitrary. The **lock-in comes first** because it is the
   instrument most likely to be unreachable (it is on Ethernet at a
   link-local address) and failing in second one is cheaper than failing in
   second ninety. The **function generator is brought up with its drive
   channel off**: only the CH2 reference is switched on, and it stays on for
   the whole session because the lock-in needs a continuous external
   reference. The **SMU is armed at 0 V with the output disabled**, and stays
   that way until the switch matrix has routed a pixel.
8. Write `run_config.json` and `lockin_configuration.json`. These two files are
   the provenance record for everything that follows.

### Phase C: substrate calibration (optional)

With `--phase-substrate do`: for every HWP angle in the grid, run a
Hooke-Jeeves descent over (QWP, analyser) to minimise the detector level.
Accept a null at or below **14.5 mV**; on failure, run an 81-probe rescue grid.
The result is saved as `substrate_calibration.json`.

With `load`, the table is read from disk; with `skip`, there is no seed table
and every pixel searches from scratch.

### Phase D: the per-pixel loop

For each pixel in the work queue:

```text
reset optics → brighten (analyser off-null, for a bright alignment peak)
  → stage move + align → dim back to the null → route the Arduino channel
  → SMU on, pole (adaptive dwell)
  → for each HWP angle:
        verified rotator move → null check → optional re-null
        → AC drive ON → triplet readout → AC drive OFF → save
  → optional follow-ups (DC hysteresis / AC-Vpp sweep / analyser sweep)
  → teardown: AC off, SMU off, switch matrix open
```

After **every HWP block** the per-pixel CSV, the per-pixel summary JSON and the
chip summary CSV are rewritten and progress is saved. Stop and skip are checked
at every checkpoint, including inside every sleep.

### Phase E: teardown

`cleanup_hardware()` turns off the AC drive, ramps the SMU down and disables
its output, opens the switch matrix, switches the laser off and closes every
session. It runs on the normal path, on a safe stop, **and** in the exception
handler: the same code, three ways in.

---

## 5. The measurement decision tree

Two distinct policies apply at a pixel, and confusing them is the single most
common source of "why did it do *that*?".

### The calibration pixel: always the expensive path

The first pixel in the queue is the calibration pixel, and it is measured the
slow, honest way **regardless of `--renull-mode`**. At *every* HWP angle it:

1. Performs a **forced sample-in 2-D null**: a coarse 3° grid followed by a
   fine 1° grid over (QWP, analyser), with the DC operating bias **on** and the
   AC drive **off**.
2. **Locks the QWP** at that measured null.
3. Turns the AC drive on and takes the **triplet**: a background reading at the
   local null, then exactly $+45^\circ$, then exactly $-45^\circ$.
4. At the strongest drive amplitude it adds a fixed **bright point at
   $+90^\circ$**, giving the balanced `0, +45, +90, −45` design. It then fits
   **two independent models to those same four points**:

   $$
   Z(\psi) = P + E_1\sin 2\psi + E_2\cos 2\psi \qquad \text{(complex, unrestricted AC)}
   $$
   $$
   D(\psi) = C_0 + C_c\cos 2\psi + C_s\sin 2\psi \qquad \text{(real DC Malus fringe)}
   $$

   projects the AC coefficients onto the **DC derivative**, and confirms the
   fitted **DC half-fringe** whenever the signal is resolved.

The result is the operating-point (S9) certificate and a trusted per-HWP null
branch that every later pixel inherits as a seed.

> **Warning: the operating angle always comes from the DC fringe, never from
> the fitted AC extremum.**
> The AC extremum is recorded as a diagnostic (`s9_dynamic_peak_offset_deg`)
> and is **never followed**. The asymmetry is deliberate: the DC fringe is
> produced by the light itself and its shape is guaranteed by Malus's law,
> whereas the AC response can be dominated by capacitive or radiated pickup
> that has no optical origin at all. If the software chased the AC peak, a
> pickup-dominated calibration pixel would hand a wrong operating angle to
> every other pixel on the chip, and the whole map would be quietly wrong in a
> way that no downstream check could detect.

The gates and their thresholds are given in
[The Null-Slope Sénarmont Readout](../physics/03-senarmont-readout.md).

### Production pixels: the fast path

At each HWP angle:

1. Move to the inherited seed null and read the leakage with the AC off.
2. Apply the `--renull-mode` policy:

   | Mode | Behaviour |
   | --- | --- |
   | `seed-only` | Accept the seed if it leaks less than 15 mV. Fastest, least robust. |
   | `adaptive-renull` *(default)* | Escalate to a six-probe 2-D parabolic re-null **only if** the seed leaks. |
   | `fast-renull` | Always re-optimise, whatever the seed reads. |
3. Turn the AC drive on and take the **same triplet** as the calibration pixel.
4. Use that triplet as a zero-cost certificate. Escalate to the balanced
   bright/confirmation pair **only** when a strong-signal triplet comes back
   inconclusive.

A null that is found is written back into the shared seed table, so later
pixels warm-start from the most recent good answer rather than from the
substrate calibration.

---

## 6. Escalation and failure policy

| Situation | Response |
| --- | --- |
| Seed null leaks | Local 2-D re-null (in `adaptive-renull` mode). |
| Null is finite but above the hard limit | Measure anyway and flag `high_null_*` / untrusted; **exclude** the row from trusted seeds, learned readouts, peak selection, HWP fitting and the physics analysis. Data is never thrown away; it is labelled. |
| The null procedure raises | Fail **this pixel**, save what exists as partial, continue with the next. |
| A verified rotator move fails | **Stop the run.** A wrong angle silently corrupts data in a way no later analysis can detect, so this is the one motion failure that is not survivable. |
| Transport loss (USB / VISA / serial) | Save the partial pixel, shut the outputs down, reconnect and verify every instrument, then continue with the **next** pixel. Never replay the interrupted one. |
| Lock-in overload, or ≥ 98 % of full scale | The row is marked invalid and is excluded from every fit. |
| SMU terminal-voltage readback missing, or more than 0.5 V from target | Abort the DC point **before the AC drive turns on**. |

---

## 7. The exception hierarchy

| Exception | Base | Meaning | Caught where |
| --- | --- | --- | --- |
| `SkipPixelRequested` | **`BaseException`** | The operator pressed *Skip Current Pixel*. | The per-pixel handler: saves partial data and moves on. |
| `CriticalHardwareTransportError` | `RuntimeError` | The instrument transport died (USB/VISA/serial). | The pixel handler, which hands control to `FastMapHardwareRecovery`. |
| `CriticalAngleError` | `RuntimeError` | A verified rotator move could not be certified. | Nothing catches it to continue, so the run stops. |
| `CriticalNullError` | `RuntimeError` | The null procedure failed. | Fails the pixel. |
| `CriticalCalibrationNullError` | `CriticalNullError` | The same, on the calibration pixel, carrying extra context. | Fails the pixel, with the calibration-specific message. |

> **Why `SkipPixelRequested` derives from `BaseException` and not `Exception`.**
> A skip request must propagate out of *any* code, including code wrapped in a
> broad `except Exception:`, and this codebase has many such wrappers, because
> "a plotting bug must not destroy a measurement" requires them. If skip were an
> ordinary `Exception`, one of those defensive handlers would silently swallow
> it, the operator would press the button and nothing would happen. Deriving
> from `BaseException` puts it in the same category as `KeyboardInterrupt`:
> deliberately un-swallowable. Every place that genuinely must clean up on a
> skip catches it explicitly and re-raises.

`raise_if_fatal_hardware_transport_error(exc, context)` is the funnel: it walks
the exception's `__cause__`/`__context__` chain looking for transport-failure
signatures and, when it finds one, re-raises as `CriticalHardwareTransportError`
with a message naming the operation that was interrupted. That is what converts
an arbitrary driver exception into a recoverable event.

---

## 8. The transport-recovery state machine

[`pockels_transport_recovery.py`](../../pockels/pockels_transport_recovery.py)
implements `FastMapHardwareRecovery`. It imports **no instrument drivers**,
because every driver it needs is handed to it as an already-imported module
object, which is precisely why it can be unit-tested against mocks.

```text
  startup
     └─▶ serial_port_identities(list_ports())        snapshot USB identity
                                                     (serial_number, location,
                                                      vid, pid) per COM port

  transport failure raised inside a pixel
     └─▶ save the partial pixel; mark it failed/partial and resumable
     └─▶ run():  loop
            ├─ checkpoint()            honour a pending Stop
            ├─ close()                 ↓ see below
            ├─ wait(delay)             5 → 10 → 20 → 40 → 60 s, then 60 s for ever
            ├─ reconnect()             ↓ see below
            ├─ success → report("recovered"); return the lock-in configuration
            └─ failure → report("attempt_failed"); close(); double the delay
                         (and raise HardwareRecoveryExhausted if an explicit
                          --transport-recovery-attempts limit was reached)
```

**`close()`: isolate first, then release.**

1. Attempt **electrical isolation before anything else**: function-generator
   drive channel OFF, SMU output OFF. A warning is reported if either fails,
   but both are attempted.
2. Close every VISA/serial owner (switch matrix, SMU, all three rotators,
   function generator, lock-in, scope detector), setting each slot in the
   hardware dictionary to `None` *before* calling `close()` on it, so a
   half-closed session can never be reused.
3. Stop polling and disconnect both Kinesis stage axes, with `Disconnect`
   attempted even when `StopPolling` throws.

> **Why every old owner is closed before any new one is opened.** A driver's
> `close()` may close a *shared* PyVISA `ResourceManager`. If a new session had
> already been opened against that manager, closing an old one would silently
> invalidate the new one, and the verification step would then pass against a
> dead handle.

**`reconnect()`: rebuild, then verify the whole path.**

1. Function generator first, followed immediately by a verified
   `OUTPUT OFF`, *before any routing or motion*.
2. SMU, connected with the output disabled, then `_verify_smu_off()` which
   queries `OUTPut:STATe?` and demands `0`/`OFF`.
3. Switch matrix, followed by `turn_all_off()`.

   > A reconnect resets the Arduino. The failed `E<n>` selection is **never**
   > replayed: ALL-OFF is established and acknowledged first, and a channel is
   > only selected again by the next pixel's normal routing.

   All three isolation paths are attempted even if one device stays offline,
   because a failed function-generator reconnect must not leave a now-reachable
   SMU driving bias. Only then is a combined `RuntimeError` raised if any of
   them failed.
4. Scope detector, then `_verify_scope()`: read a voltage and require it to be
   finite.
5. The three rotators, each reopened by USB identity, homed (unless the run
   disabled homing), polled for an `ok` status for up to 30 s, and checked for
   a home readback within 0.5° of zero. The run's angle coordinate system is
   then restored by writing back the saved `_offset`, including for manual raw
   calibration runs that deliberately never tare, and the velocity is re-set.
6. Lock-in, `apply_safe_startup()`, an identity readback that must be
   non-empty, and a full re-application of the fixed-range configuration.
7. Both Kinesis axes: rebuild the device list, create, initialise, home
   (unless disabled), and require a finite position readback.
8. **Finally, re-verify everything**: function generator OFF again, SMU off
   again, matrix all-off again, scope readable, all three rotator angles
   finite, lock-in identity non-empty. A late failure in step 7 can invalidate
   a session opened in step 1, so nothing is trusted until the whole path
   passes at once.

`recovery_serial_port()` is how a port is found again: it follows the
snapshotted `serial_number`, then `location`, requiring `vid` and `pid` to
match too, and **raises rather than guessing** if the identity is absent or
matches more than one adapter. Two identical USB-serial adapters are never
disambiguated by luck.

Every state transition is passed to a `report` callback, which appends one JSON
object per line to `transport_recovery.jsonl`.

---

## 9. Where to find things

| I want to change… | Look in |
| --- | --- |
| a default value shown in the GUI | `FastMapGuiApp._make_vars()` |
| a GUI widget or its tooltip | `FastMapGuiApp._build_controls()` |
| the GUI layout and panels | `FastMapGuiApp._build_ui()` |
| what the GUI passes to the worker | `FastMapGuiApp._build_child_argv()` |
| a CLI flag | `build_arg_parser()` in `pockels_fast_map_gui.py` |
| the per-pixel measurement sequence | the pixel loop inside `main()` |
| the triplet readout / S9 certificate | the S9 helpers around `annotate_s9_certificate()` |
| the null search | `null_descent()`, `null_fast_rsm()` in `pockels_full_automation.py`; `first_null_fast()`, `fast_renull()` in `pockels_campaign.py` |
| adaptive poling | `adaptive_poling_dwell()` in `pockels_fast_map_gui.py` |
| the hysteresis engine | `run_dc_hysteresis_sweep()` in `Pockels_Calibration_2026.py` |
| domain reset | `reset_domains_pulsed()` |
| lock-in configuration | `configure_lockin_for_fast_map()` |
| lock-in driver commands | `Lock_In_Mag_Phase_Track.DSP7230` |
| stage alignment algorithms | `hill_climb_align()`, `fast_peak_align()`, `golden_section_align()`, `auto_align()` in `stage_calibration.py` |
| the scope detector | `stage_calibration.ScopeDetector` |
| the rotator driver | `elliptec_serial.ElliptecRotator` |
| Arduino routing | `arduino_switch_matrix.ArduinoSwitchMatrix`, `pixel_to_channel()` |
| normalisation and $r_\mathrm{eff}$ gating | `pockels_measurement_analysis.py` |
| loop metrics and classification | `pockels_hysteresis_analysis.py` |
| CSV column lists | `FAST_MAP_FIELDS`, `RAW_FIELDS`, `PEAK_LOCK_TABLE_FIELDS`, `CHIP_SUMMARY_FIELDS`, near the top of `pockels_fast_map_gui.py` |
| hardware constants (ports, detector gain, stage serials) | `POL_Chip_Test_Working_2026.py` and `stage_calibration.py` |
| voltage and current limits | the config block of `Pockels_Calibration_2026.py` |

**Navigating a 22 800-line file.** `pockels_fast_map_gui.py` is laid out
top-to-bottom as: constants and CSV schemas → angle and frame conversion
helpers → GUI-state and IPC helpers → rotator move and verification helpers →
lock-in configuration → null and S9 routines → the per-pixel measurement engine
→ follow-up drivers → chip summary writers → `build_arg_parser()` → `main()` →
the Tkinter classes → `run_gui()`. Search for a `def` name; do not scroll.

---

**Next:** [Motion Control](motion-control.md) for how the motors actually move,
or [Instrument Control and Timing](instrument-control.md) for how a single
measurement point is taken. If something has gone wrong right now, go to
[Troubleshooting](../guide/troubleshooting.md).

---

<div align="center">

[← The software](index.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Motion control →](motion-control.md)

</div>
