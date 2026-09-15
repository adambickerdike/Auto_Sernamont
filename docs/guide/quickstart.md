# Quick Start: your first measurement

This page takes somebody who has never touched this rig from a cold lab to one
complete, trustworthy measured pixel in about an hour. Follow it literally, in
order. Nothing here requires you to understand the physics yet; that is
[Physics](../physics/index.md).

Before you start, make sure the machine is set up:
[Installation and Setup](installation.md).

> **Rule 0: run the software from Windows Python, not WSL or Linux.** The XY
> stage and the laser go through Thorlabs' Kinesis **.NET** assemblies, which
> exist only on Windows. The analysis-only modules and the whole test suite run
> anywhere.

---

## 1. Before you touch anything

### 1.1 Laser safety

The source is a **1550 nm** fibre-coupled diode laser (Thorlabs KLS1550) at up
to ~7 mW. 1550 nm is **invisible**, so you cannot see the beam and your blink
reflex will not protect you.

- Wear the 1550 nm laser goggles that live with the setup.
- Never put your eye at bench level near the beam line.
- The laser can be switched off from the GUI: click the **Laser** box in the
  *Optical Train* panel. Do that before reaching into the beam path.
- The beam is enclosed for most of its path. Keep it that way.

### 1.2 Electrical safety

The SMU applies up to **±40 V DC** across on-chip electrodes separated by a
~7 µm gap. That is not a shock hazard to you, but it *will* destroy a pixel if
mishandled. The software enforces three things:

- a hard ceiling of **±40 V** (`DC_HYST_VMAX`; anything higher is rejected
  outright, on all four validation paths),
- a **1 mA** current compliance limit on the SMU,
- an interlock ordering that guarantees the Arduino switch matrix has selected a
  pixel *before* any voltage is enabled, and that every output is off before the
  matrix changes channel.

Never hand-edit those limits to "just try something".

### 1.3 Power-on order

The layout of the bench, and what is connected to what, is in
[the setup schematic](../../assets/setup_schematic.png) and in
[Instruments](../experiment/instruments.md).

1. Turn on the **oscilloscope** (Tektronix TBS), the **function generator**
   (Aim-TTi TGF3162), the **SMU4201**, and the **DSP7230 lock-in**. Let the
   lock-in warm up for at least ten minutes.
2. Power the **Thorlabs KCube** controllers (X stage `26006987`, Y stage
   `26007025`, laser KCube) and the **Elliptec rotator** hubs.
3. Plug in / power the **Arduino Nano** switch matrix.
4. Confirm the **photodiode (Thorlabs PDA30B2)** is powered, its gain switch is
   on **10 dB**, and its load is **Hi-Z**. The software's volt → watt conversion
   assumes exactly this (0.875 A/W responsivity at 1550 nm into a 4.75 × 10³ V/A
   transimpedance).
5. Log into the measurement PC.

> **Note** If you have never run `tools\pockels_usb_stability_setup.ps1` on this
> PC, do it now, from an elevated PowerShell, and reboot. It disables USB
> selective suspend, the single most common cause of an instrument vanishing
> mid-run. See [Installation §4](installation.md#4-one-time-windows-usb-stability-setup).

---

## 2. Start the software

Open a terminal (Anaconda Prompt or PowerShell) in the repository and run:

```bat
python pockels\pockels_fast_map_gui.py
```

The **BTO GO!** window opens. Nothing has been connected yet; the GUI is
only a launcher and a monitor. **No hardware is touched, and no run folder is
created, until you press *Start Measurement*.**

> You can also launch it from Spyder with `runfile(...)`, or run it headless
> with `python pockels\pockels_fast_map_gui.py --cli <flags>`. See
> [Operator Manual §9](operating.md#9-running-headless-cli).

### What you are looking at

```text
┌────────────────────┬──────────────────────────────────────────────────────┐
│                    │  Run Status      (progress, LEDs: DC / AC / Laser)   │
│   Settings panel   ├──────────────────────────────────────────────────────┤
│   (scrollable)     │  Optical Train   (live schematic of the beam line)   │
│                    ├─────────────────────────┬────────────────────────────┤
│   Chip ID          │                         │ Stage Alignment │ Lock-In  │
│   Pixels           │     Live Chip Map       ├─────────────────┼──────────┤
│   HWP grid preset  │     (10 × 10 grid)      │ Oscilloscope    │ Camera   │
│   …                │                         │                 │          │
│   [Start]          ├─────────────────────────┴────────────────────────────┤
│   [Stop] [Skip]    │  Terminal                    │  Run Notifications    │
└────────────────────┴──────────────────────────────┴───────────────────────┘
```

- **Settings panel** (left, scrollable): everything the run is told to do.
  Tick *Show advanced settings* to reveal the rest.
- **Run Status**: the current operation, progress bars, and three LEDs that
  mirror the real commanded state of the high-voltage DC, the AC drive and the
  laser.
- **Optical Train**: a live schematic of the beam line with each rotator's
  commanded and read-back angle.
- **Live Chip Map**: the 10 × 10 pixel grid, with a *View:* dropdown that
  switches between the response layers and the live hysteresis curve.
- **Terminal / Run Notifications**: raw worker output, and an extracted feed of
  anything that looked like a warning or an error.

Hover the **`?`** marker beside any setting for its tooltip. Every field has
one, and the tooltips are the authoritative short description.

---

## 3. Check the serial ports (2 minutes)

Windows renumbers USB COM ports when you move a cable. Confirm what is where
before you commit to a run:

```bat
python pockels\pockels_fast_map_gui.py --cli --list-serial-ports
```

Expected (the numbers may differ; most devices auto-resolve by USB identity):

| Device | Typical port |
| --- | --- |
| QWP rotator (Elliptec, address 2) | COM4 |
| HWP rotator (Elliptec, address 1) | COM5 |
| Analyser rotator (Elliptec, address 2) | COM8 |
| SMU4201 | COM11 or COM13 |
| Arduino switch matrix | COM12 |
| Function generator (Aim-TTi) | ASRL3 (COM3) |
| Oscilloscope | `USB0::0x0699::0x03C7::C021052::0::INSTR` |
| DSP7230 lock-in | `TCPIP0::169.254.150.230::50001::SOCKET` (Ethernet) |

The SMU and the Arduino are auto-detected by identity
([`pockels/serial_port_resolver.py`](../../pockels/serial_port_resolver.py)).
The **three rotator ports are hard-coded** in
[`pockels/POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py).
If one has changed, edit `PORT_QWP` / `PORT_HWP` / `PORT_ANL` there.

While you are at it, check the lock-in is on the network:

```bat
ping 169.254.150.230
```

---

## 4. First-day optical calibration (~20 minutes)

You only need this when the sample or the optics have changed. If a good
calibration already exists, skip to [§5](#5-run-one-pixel-the-shakedown).

### 4.1 Guided sample-in calibration

In the GUI, press **Guided Sample-In Axis/Extinction Calibration**. A second
process starts and the *Run Status* panel turns into a step-by-step wizard. It
walks you through three physical steps and tells you exactly when to press
**Continue**.

| Step | What you physically do | What the software measures |
| --- | --- | --- |
| 1 | **Remove the QWP** from the beam (sample stays in) | Sweeps the analyser and fits $\cos^2$ → analyser extinction and parallel angles |
| 2 | **Insert the QWP** | Sweeps the QWP → fast-axis zero $\gamma_0$ and the retardance (should come out near 90°) |
| 3 | Leave everything in | Iteratively nulls QWP + analyser through the sample → the `(q_null, a_null, p_null)` triple |

**Health check.** Step 1 should show a clean $\cos^2$ with more than 20 dB
contrast; step 3 should reach more than 25 dB extinction. The result is written
to `calibration_results_withSample/<timestamp>_<name>/sample_calibration.json`
at the repository root.

Afterwards press **Find Latest Sample Optical Cal** so the GUI fills the
*Optical cal* field with that file.

> **Note** The retardance fit in step 2 has a trap worth knowing about: the
> expected visibility of the parallel-analyser QWP sweep is **1/3**, not 1. The
> relation is $\delta = 2\arccos\sqrt{I_\mathrm{min}/I_\mathrm{max}}$. See
> [Sénarmont readout](../physics/03-senarmont-readout.md).

### 4.2 Substrate HWP null-seed table

The measurement needs a *null*, a QWP + analyser extinction pair, for **every**
incident polarisation it will visit, not just one. That table is built
automatically at the start of the run when **Substrate = `do`** (the default).

- **First ever run on a chip:** leave `Substrate` on `do`.
- **Every run after that:** press **Find Latest Substrate Null Cal**, which sets
  `Substrate = load` and fills in the path. Re-deriving the table costs ~20
  minutes and gains nothing if the optics have not moved.

---

## 5. Run one pixel (the shakedown)

**Always** start a new chip, a new day, or a new code version with a single
pixel. If pixel 1 works end to end, the chip run will too.

The pixel numbering, and how it maps onto what you see through the camera, is in
[the pixel grid figure](../../assets/figures/pixel_grid.png).

Fill in the settings panel:

| Field | Value for the shakedown | Why |
| --- | --- | --- |
| **Chip ID** | e.g. `BTNO_0087` | Required. Goes into the folder name and every summary file. |
| **Run label** | `shakedown` | Optional free text appended to the folder name. |
| **Pixels** | `1` | One pixel only. |
| **HWP grid preset** | *Current calibrated default (9 points)* | The 9-point incident-polarisation grid centred on the verified high-response angle. |
| **Calibration pixel** | `1` | Must be inside *Pixels*. This pixel runs the expensive full calibration. |
| **DC pixels** | *(clear it)* | Skip hysteresis on the first try, and add it once the map works. |
| Advanced ▸ **Substrate** | `load` (or `do` if this is the very first run) | |

Leave everything else at its default. The defaults *are* the production
settings: 9 Vpp AC drive at 30 kHz, +40 V poling with adaptive dwell, adaptive
re-nulling, triplet readout, a 500 ms lock-in time constant on a fixed 200 µV
RMS full-scale range.

Press **Start Measurement**.

### What should happen, in order

The *Run Status* "Operation:" line steps through roughly this sequence. Watch
the **Terminal** panel for detail.

1. **Bring-up**: `Connecting lock-in` → `Connecting XY stage` →
   `Connecting oscilloscope` → `Starting camera live view` →
   `Connecting switch matrix` → `Connecting KLS1550 laser` →
   `Configuring function generator` → `Connecting rotators` →
   `Homing/taring rotators` → `Configuring fixed lock-in range` →
   `Connecting SMU` → `Loading optical calibration`.
2. If `Substrate = do`, the HWP null-seed table is built, one null search per
   HWP angle. **This is the slow part (~20 min).**
3. **Pixel 1.** The stage moves to the pixel, the analyser rotates +45° off null
   to brighten the beam, and the stage hill-climbs onto the local transmission
   peak. Watch the *Stage Alignment Live* panel.
4. The Arduino routes pixel 1's electrode pair. The **DC LED** turns red as the
   SMU ramps to +40 V in 5 V chunks and poles the pixel for up to 180 s, usually
   less, because adaptive poling stops at the plateau (60 s floor, 180 s cap,
   3 consecutive quiet intervals under 2 %, at least 2 µV of signal).
5. For each of the 9 HWP angles: null check → **AC LED** on → three lock-in
   reads (local null / +45° / −45°) → AC off. The *HWP/Analyser Lock-In* panel
   fills with bars.
6. **Teardown:** AC off → SMU off → matrix off. The *Live Chip Map* cell for
   pixel 1 gets a colour.

**Expected wall clock: ≈ 8 minutes** for the calibration pixel (≈ 4.5 to 5 min
for an ordinary production pixel), plus the substrate table if you built one.

### Did it work?

Press **Open Run Folder**. You should find, at the repository root:

```text
pockels_fast_map/20260915_143012_BTNO_0087_shakedown/
├── run_config.json                 ← everything the run was told to do
├── lockin_configuration.json       ← the exact lock-in setup applied
├── fast_map_all_pixels.csv         ← one row per pixel (the chip summary)
└── pockels_pixels/pixel_001/
    ├── fast_map.csv                ← one row per lock-in point (~140 columns)
    ├── fast_map_summary.json       ← best conditions + angular fit + gates
    ├── fast_map_angular_polar.png  ← the polarisation-dependence polar plot
    └── poling_kinetics.csv
```

**Green flags.** Open `fast_map.csv` and check:

| Look at | Want to see | Why it matters |
| --- | --- | --- |
| `p_null_mV` | ≤ **14.5** at most HWP angles | the optics can null properly |
| `lockin_net_signed_V` | **opposite signs** for `plus45` and `minus45` at the same HWP | the signal really is a polarisation rotation |
| `quality_flags` | contains `s9_operating_point_certified`, not `s9_operating_point_failed`, at the strong HWP angles | the operating point passed every geometry gate |
| `s9_status` | `certified_triplet` or `certified_fit` | the readout is trustworthy (`geometry_only_low_signal` is also accepted: the geometry is right, the pixel is simply weak) |

**Red flags.** Stop and read [Troubleshooting](troubleshooting.md):

| Symptom | What it usually means |
| --- | --- |
| `p_null_mV` high everywhere | the optics are not nulling; recheck the calibration |
| `plus_minus_same_sign` flag | you are measuring electrical pickup, not light |
| `lockin_overload` / `lockin_range_exceeded` | the lock-in range is wrong, or the null is leaking badly |
| Everything near zero | no signal: is the laser on, is the pixel routed, is the drive actually reaching the chip? |

The full column reference is in [Data Schema](../reference/data-schema.md); the
complete flag table with severities is
[there too](../reference/data-schema.md#11-quality-flags).

---

## 6. Add hysteresis to the same pixel

Once the map works, prove the electrical path.

1. Set **DC pixels** to `1`.
2. Leave the hysteresis defaults: **30 s** dwell, **1** cycle, **±40 V** limit,
   **4 Vpp** small-signal probe, and the standard 45-point centre-dense voltage
   grid (absolute levels 40, 30, 25, 20, 15, 12.5, 10, 7.5, 5, 2.5, 1.25, 0 V,
   mirrored and returned).
3. Press **Start Measurement** again, or use *Resume Latest Run*.

This takes about **29 minutes** (≈ 39 s per DC point). While it runs, switch the
*View:* dropdown above the chip map to **"Hysteresis sweep: lock-in |R| vs
Vdc"** and leave *Pixel: Auto*; the curve updates after every acquired point.

### What a butterfly should look like

You should see a **butterfly**: $|R|$ high at both voltage extremes, dipping
towards zero near the coercive voltages, and the descending and ascending
branches *not* overlapping. That separation **is** the ferroelectric hysteresis.
The dips are where the domain population is halfway through reversing and the
electro-optic contributions cancel.

Branch colours in the live plot: virgin = purple, pre-saturation = orange,
down = red, up = green; later cycles dashed; compliance trips marked with red
crosses.

The signed S-shaped loop $S(V)$ is produced automatically by the analysis, along
with the metrics, in the pixel's `dc_hysteresis/sweep/` folder
(`dc_hysteresis_loops.png` and `dc_hysteresis_metrics.json`). Compare it with
[a measured loop from this bench](../../assets/figures/hysteresis_measured.png)
and read [Ferroelectrics](../physics/05-ferroelectrics.md) for what the shape
means.

> **Warning** The dwell sets the loop shape. 30 s is the default and the
> production value. Whatever you choose, keep it fixed for the whole campaign,
> because loops taken at different dwells are not comparable.

---

## 7. Go full chip

Only after both of the above are clean:

| Field | Production value |
| --- | --- |
| **Pixels** | `all`, or the default working set `1-6,11-16,21-26,31-37,41-48,51-100` (83 pixels) |
| **Calibration pixel** | a pixel you already know gives a strong, clean response |
| **DC pixels** | the subset you want loops on (each loop is ~29 min, so 100 of them is ~49 hours) |
| Advanced ▸ **Substrate** | `load` |

Rough budget: the chip map runs at ≈ 4.5 to 5 min/pixel, so **6 to 7 hours for
83 pixels**. Add hysteresis only where you need it.

Choosing the calibration pixel matters more than any other single setting: a
dead or pinned one poisons the seeds for the whole chip. See
[Operator Manual §2](operating.md#choosing-the-calibration-pixel).

Press **Start Measurement** and leave it. The run is restartable: if anything
interrupts it, press **Resume Latest Run**, then **Unmeasured Only**, and it
picks up exactly where it stopped without redoing finished work.

---

## 8. Stopping safely

| Action | What it does | When |
| --- | --- | --- |
| **Request Safe Stop** | The worker finishes the point it is on, turns AC off, ramps the SMU down, opens the switch matrix, and saves. | **This is the one to use.** |
| **Skip Current Pixel** | Abandons just the current pixel (saved as partial) and moves to the next. | One pixel is dead and burning time in alignment retries. |
| Closing the window / killing the process | Last resort. Outputs may be left energised, so check the SMU and function generator front panels by hand. | Never, if you can help it. |

---

## 9. What to do next

- [Operator Manual](operating.md), the complete SOP and every setting.
- [Physics](../physics/index.md), what you just measured and why it works.
- [Data Schema](../reference/data-schema.md), every file, every column.

### Post-run analysis one-liners

```bat
REM Per-pixel loop metrics for one sweep
python pockels\pockels_hysteresis_analysis.py <run>\pockels_pixels\pixel_001\dc_hysteresis\sweep\dc_hysteresis.csv

REM 21 chip heatmaps + loop-type map + loop gallery for a whole run
python pockels\make_hysteresis_maps.py <run_dir> --gap-um 7 --alpha <FEM alpha>

REM Cross-metric correlations, composition trends, clustering
python pockels\make_compositional_report.py <run_dir> --composition composition.csv
```

---

<div align="center">

[← Installation](installation.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Operator manual →](operating.md)

</div>
