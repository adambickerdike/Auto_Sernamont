# Installation and Setup

Everything you need to do once per machine, before you can take a measurement.
Roughly half of this repository will run anywhere; the half that drives motors
will only run on Windows. This page explains which half is which, installs
both, and finishes with two verification steps that take under a minute.

---

## 1. Windows is required for measurement

> **Rule 0: the measurement software must be run from Windows Python, not
> WSL or Linux.**

The XY stage (two Thorlabs KCubeStepper controllers, serials **26006987** for X
and **26007025** for Y) and the KLS1550 laser are driven through Thorlabs'
**Kinesis .NET assemblies**. Those assemblies are Windows-only DLLs, loaded into
Python by `pythonnet` (`import clr`). There is no Linux equivalent, no
open-source reimplementation in this repository, and no USB-level fallback.
[`pockels/stage_xy.py`](../../pockels/stage_xy.py) and
[`pockels/kls1550.py`](../../pockels/kls1550.py) both fail at import time
without them.

Everything else on the bench speaks a portable protocol (VISA over USB and
Ethernet, or plain serial), but the stage sits in the middle of every
measurement, so in practice **the whole acquisition path is Windows-only**.

### What runs anywhere

These can be developed, tested and re-run on WSL, Linux, macOS or a laptop with
no hardware attached at all, because they import **no** instrument drivers:

| Module | What it does |
| --- | --- |
| [`pockels/pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py) | Sénarmont normalisation, the S9 operating-point certificate, angular fits, $r_\mathrm{eff}$ gating |
| [`pockels/pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py) | signed loop projection, the full loop metric set, loop classification, poling kinetics |
| [`pockels/pockels_transport_recovery.py`](../../pockels/pockels_transport_recovery.py) | the reconnect-and-verify state machine |
| [`pockels/pockels_lockin_ranging.py`](../../pockels/pockels_lockin_ranging.py) | the predictive lock-in range controller |
| [`pockels/pockels_angular_plots.py`](../../pockels/pockels_angular_plots.py) | the polar and diagnostic angular plots |
| [`pockels/make_hysteresis_maps.py`](../../pockels/make_hysteresis_maps.py), [`make_compositional_report.py`](../../pockels/make_compositional_report.py), [`make_fast_map_extra_plots.py`](../../pockels/make_fast_map_extra_plots.py), [`make_peak_hwp_angle_map.py`](../../pockels/make_peak_hwp_angle_map.py) | the post-run chip-level figures |
| **the entire `tests/` suite** | 23 test files, all passing, no drivers imported |

The test suite parses the large GUI module with `ast` and exercises individual
functions in isolation, precisely so it never needs PyVISA, pythonnet or a
display. That makes "run the tests" a valid sanity check on any machine; see
[§6](#6-verify-the-installation).

---

## 2. Python dependencies

Python 3.9 or newer. Anaconda is the usual choice on the measurement PC because
it ships Tk and a working SciPy stack.

```bat
python -m pip install -r requirements.txt
```

[`requirements.txt`](../../requirements.txt) at the repository root lists the
runtime packages in the table below. A machine that will never drive hardware
only needs [`requirements-test.txt`](../../requirements-test.txt), the four
packages the test suite and the figure scripts import.

| Package | Needed for | Required where |
| --- | --- | --- |
| `numpy` | all numerics: fits, phasor algebra, loop projection | everywhere |
| `scipy` | least-squares fits, optimisers used by the null searches and the loop fits | everywhere |
| `matplotlib` | every plot the run and the analysis scripts produce | everywhere |
| `pandas` | reading and joining the CSV outputs in the chip-level reports | analysis |
| `pyvisa` | oscilloscope, function generator and DSP7230 lock-in (USB / TCP sockets) | measurement PC |
| `pyserial` | Elliptec rotators, SMU4201, Arduino switch matrix, and `serial.tools.list_ports` for `--list-serial-ports` | measurement PC |
| `pythonnet` + `clr_loader` | loading the Kinesis .NET assemblies (`import clr`) for the stage and the laser | **Windows only** |
| `opencv-python` | the alignment camera live view and the computer-vision alignment path | measurement PC (optional) |
| `tkinter` | the GUI itself | measurement PC (ships with standard Windows Python; missing in some minimal conda environments) |

> **Note** A VISA implementation must also be present for `pyvisa` to find
> anything. Install NI-VISA or Keysight IO Libraries on the measurement PC;
> `pyvisa` is only the Python binding, not the backend.

> **Warning** If `python -c "import tkinter"` fails, the GUI cannot start. That
> is common in minimal conda environments and universal under WSL. Use the
> standard Windows Python installation, or run headless with `--cli` (see
> [Operator Manual §9](operating.md#9-running-headless-cli)).

---

## 3. Thorlabs Kinesis

Install the **Thorlabs Kinesis** software package (64-bit, matching your Python
architecture) from Thorlabs. The default installation path is the one the code
expects:

```text
C:\Program Files\Thorlabs\Kinesis
```

Three assemblies are loaded by name at import time in
[`pockels/stage_xy.py`](../../pockels/stage_xy.py),
[`pockels/POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py)
and [`pockels/stage_calibration.py`](../../pockels/stage_calibration.py):

| Assembly | Purpose |
| --- | --- |
| `Thorlabs.MotionControl.DeviceManagerCLI.dll` | device enumeration |
| `Thorlabs.MotionControl.GenericMotorCLI.dll` | generic motor interface |
| `ThorLabs.MotionControl.KCube.StepperMotorCLI.dll` | the KCubeStepper controllers themselves |

The laser module [`pockels/kls1550.py`](../../pockels/kls1550.py) searches both
`C:\Program Files\Thorlabs\Kinesis` and
`C:\Program Files (x86)\Thorlabs\Kinesis`, and additionally honours the
`THORLABS_KINESIS_PATH` environment variable (or a `--kinesis-path` argument).
If it finds neither it raises:

```text
Cannot find Thorlabs Kinesis. Set THORLABS_KINESIS_PATH or use --kinesis-path.
```

If you have installed Kinesis somewhere non-standard, set the environment
variable for the laser and edit the three `clr.AddReference` paths for the
stage. There is no runtime override for those.

> **Note** Install Kinesis *before* plugging in the KCube controllers, so that
> Windows picks up the Thorlabs driver rather than a generic one.

---

## 4. One-time Windows USB stability setup

Run this once, from an **elevated** PowerShell window, then reboot:

```powershell
powershell -ExecutionPolicy Bypass -File tools\pockels_usb_stability_setup.ps1
```

The script refuses to run without administrator rights, and it does not disable
or remove any device. It does two things:

1. **Disables USB selective suspend**, on both AC and battery power, by writing
   the relevant power-setting index into the active Windows power scheme with
   `powercfg` and then re-activating that scheme.
2. **Revokes Windows' permission to power down the two USB hub families** used
   by the Pockels USB chain, by clearing the `Enable` flag on their
   `MSPower_DeviceEnable` WMI instances (the USB 2 and USB 3 companion devices
   have separate power-policy entries, so both are handled).

> **Warning** USB selective suspend is **the single most common cause of an
> instrument vanishing mid-run.** Windows quietly suspends an "idle" hub during
> a long poling dwell, the SMU or the Arduino drops off the bus, and the worker
> has to fall into its transport-recovery loop. Do this before your first real
> campaign, not after you have lost one. The reboot is required, because the hub
> policy change does not take effect until the devices re-enumerate.

The automatic recovery behaviour that catches whatever still gets through is
described in [Operator Manual §8](operating.md#automatic-hardware-recovery) and
[Instrument control](../software/instrument-control.md).

### Power plan advice

Selective suspend is the worst offender but not the only one. On the
measurement PC:

- Set the Windows power plan to **High performance** (or a Balanced plan with
  every sleep timeout disabled).
- Set **Turn off hard disk after** to *Never*, because a run writes a CSV row
  every few seconds for hours.
- Set **Sleep** and **Hibernate** to *Never*. A 49-hour hysteresis campaign must
  survive two nights untouched.
- Disable screen-saver lock-outs that would suspend the display driver; the GUI
  keeps drawing and the camera keeps streaming.
- Leave **Windows Update** active-hours configured so it cannot reboot the
  machine mid-run.

---

## 5. COM ports and VISA resources

| Device | Typical resource | How it is resolved |
| --- | --- | --- |
| QWP rotator (Elliptec ELL14, bus address **2**) | `COM4` | **hard-coded** (`PORT_QWP`) |
| HWP rotator (Elliptec ELL14, bus address **1**) | `COM5` | **hard-coded** (`PORT_HWP`) |
| Analyser rotator (Elliptec ELL14, bus address **2**) | `COM8` | **hard-coded** (`PORT_ANL`) |
| SMU4201 | `COM11` or `COM13` | auto-resolved by USB identity; override with `--smu-port` |
| Arduino Nano switch matrix | `COM12` | auto-resolved by USB identity; override with `--arduino-port` |
| Function generator (Aim-TTi TGF3162) | `ASRL3` (`COM3`) | VISA |
| Oscilloscope (Tektronix TBS) | `USB0::0x0699::0x03C7::C021052::0::INSTR` | VISA, CH1 |
| Lock-in (Signal Recovery DSP7230) | `TCPIP0::169.254.150.230::50001::SOCKET` | VISA over Ethernet |
| XY stage (2 × KCubeStepper) | serials `26006987` (X), `26007025` (Y) | Kinesis, by serial number |

> **Note** Windows renumbers USB virtual COM ports whenever you move a cable to
> a different socket or hub. The SMU and the Arduino cope with this
> automatically: [`pockels/serial_port_resolver.py`](../../pockels/serial_port_resolver.py)
> prefers stable device identity (VID/PID, serial number, location) over the COM
> number, and the Arduino is additionally confirmed by its boot banner
> `"Signal Matrix Ready"`.
>
> **The three rotator ports are not auto-resolved.** `PORT_QWP`, `PORT_HWP` and
> `PORT_ANL` are hard-coded near the top of
> [`pockels/POL_Chip_Test_Working_2026.py`](../../pockels/POL_Chip_Test_Working_2026.py)
> (lines 92 to 94), together with their bus addresses `ADDR_QWP = 2`,
> `ADDR_HWP = 1`, `ADDR_ANL = 2`. If a rotator port changes, edit it there.

The rotator link runs at 9600 8N1. The Arduino runs at 9600 baud and answers
`1`…`100` to route a pixel, `E1`…`E100` for the paired electrode form, and `0`
to open everything; see [Switch matrix](../experiment/switch-matrix.md).

---

## 6. Verify the installation

### 6.1 Run the test suite

This works on **any** machine, hardware or not:

```bash
cd /path/to/Auto_Sernamont
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -p "test_*.py"
```

The run ends with `OK` and the number of tests it ran. The suite needs
`pyserial` installed even with no hardware attached, because the modules under
test import it; `requirements-test.txt` provides it.
A failure here is a software problem, not a hardware one, and it tells you so
before you have spent an hour at the bench. Individual test files run directly:

```bash
python tests/test_pockels_hysteresis_analysis.py
```

### 6.2 List the serial ports

This one needs the measurement PC, with the instruments powered:

```bat
python pockels\pockels_fast_map_gui.py --cli --list-serial-ports
```

Compare the output against the table in [§5](#5-com-ports-and-visa-resources).
Add `--show-bluetooth-ports` if you want Bluetooth serial links included (they
are hidden by default because they clutter the list). Every expected device
should appear; if one is missing, start with
[Troubleshooting §1](troubleshooting.md#1-startup-and-connection).

### 6.3 Launch the GUI

```bat
python pockels\pockels_fast_map_gui.py
```

The **BTO GO!** window opens. Nothing is connected and **no run folder is
created** until you press *Start Measurement*. The GUI on its own is only a
launcher and a monitor, so this is a safe thing to do just to confirm Tk works.

---

## 7. Where the data will land

[`pockels/_bootstrap.py`](../../pockels/_bootstrap.py) is imported first by every
entry script. It pins `sys.path[0]` to `pockels/` so sibling imports always
resolve to this package, and it pins the working directory to the **repository
root** so that every launch mode (CLI, Spyder `runfile`, double-click, GUI
child worker) agrees on where "here" is. It announces itself once per process:

```text
[pockels] Working directory pinned to <repo> (runs and calibrations land here); imports pinned to <repo>\pockels
```

Consequently all output folders are created at the repository root:
`pockels_fast_map/`, `pockels_calibration/`, `calibration_results_withSample/`,
`calibration_results_3step/`, `stage_calibration/`, `pockels_campaign/`,
`analyser_sweep_voltage_series/`, `smu4201_sweeps/`.

> **Warning** All of these are git-ignored on purpose. Measurement data does not
> belong in the repository, and nothing in git will bring it back. Back up each
> run folder separately, the day it is taken. The full tree is documented in
> [Data Schema](../reference/data-schema.md).

---

## Next

- [Quick Start](quickstart.md), your first measured pixel.
- [Instruments](../experiment/instruments.md), what each box on the bench is
  and how it is wired.
- [CLI Reference](../reference/cli.md), every flag, with defaults.

---

<div align="center">

[← Operating guide](index.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Quick start →](quickstart.md)

</div>
