"""
arduino_switch_matrix.py
Python interface for the 100-Channel Signal Switching Matrix Controller
running on the Arduino Nano (Arduino_Manually_Switching.cc firmware).

Provides:
  * ArduinoSwitchMatrix class for programmatic control from other scripts.
  * Interactive CLI mode (run this file directly) that mimics the
    Arduino IDE Serial Monitor.

Wire protocol (mirrors the firmware):
  - Send "1".."100" + newline -> switch logical stage pin/pixel via the
    firmware's configured pin-to-electrical-switch wiring map.
  - Send "E1".."E100" + newline -> switch a raw electrical switch/channel.
    Automation uses this raw command because it already maps pixel -> switch.
  - Send "0" + newline          -> turn ALL channels off.
  - Arduino replies with a human-readable status line per command,
    plus a 3-line banner on boot.

Notes for the Chinese-clone Nano (old bootloader, often CH340 USB):
  - Opening the serial port toggles DTR which resets the MCU.
  - The old bootloader sits for ~1.5-2 s before user code runs, so we
    wait for the boot banner before accepting commands.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from queue import Queue, Empty

import serial
from serial.tools import list_ports


DEFAULT_PORT = "COM12"
DEFAULT_BAUD = 9600
# CH340 / old-bootloader Nano clones can be slow — the bootloader stalls for
# several seconds before user code starts. Be generous; the wait is one-shot.
BOOT_RESET_WAIT_S = 3.0
# How long to wait for the firmware banner before giving up (still usable).
BOOT_BANNER_TIMEOUT_S = 10.0
# Token in the firmware banner that signals "ready for commands".
BANNER_READY_TOKEN = "Signal Matrix Ready"

PIXEL_TO_ELECTRICAL_SWITCH = {
    1: 26, 2: 24, 3: 21, 4: 18, 5: 14, 6: 13, 7: 9, 8: 6, 9: 3, 10: 1,
    11: 28, 12: 27, 13: 23, 14: 19, 15: 15, 16: 12, 17: 8, 18: 4, 19: 100, 20: 99,
    21: 31, 22: 30, 23: 25, 24: 20, 25: 16, 26: 11, 27: 7, 28: 2, 29: 97, 30: 96,
    31: 35, 32: 33, 33: 32, 34: 29, 35: 17, 36: 10, 37: 98, 38: 95, 39: 94, 40: 92,
    41: 38, 42: 37, 43: 36, 44: 34, 45: 22, 46: 5, 47: 93, 48: 91, 49: 90, 50: 89,
    51: 39, 52: 40, 53: 41, 54: 43, 55: 55, 56: 72, 57: 84, 58: 86, 59: 87, 60: 88,
    61: 42, 62: 44, 63: 45, 64: 48, 65: 60, 66: 67, 67: 79, 68: 82, 69: 83, 70: 85,
    71: 46, 72: 47, 73: 52, 74: 57, 75: 61, 76: 66, 77: 70, 78: 75, 79: 80, 80: 81,
    81: 49, 82: 50, 83: 54, 84: 58, 85: 62, 86: 65, 87: 69, 88: 73, 89: 77, 90: 78,
    91: 51, 92: 53, 93: 56, 94: 59, 95: 63, 96: 64, 97: 68, 98: 71, 99: 74, 100: 76,
}


class ArduinoSwitchMatrix:
    """
    Serial wrapper for the 100-channel exclusive switching matrix.

    Typical use:
        with ArduinoSwitchMatrix(port="COM10") as mtx:
            mtx.switch_to_pin(7)
            ...
            mtx.turn_all_off()
    """

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUD,
        timeout: float = 0.1,
        verbose: bool = True,
        wait_for_banner: bool = True,
        verify_commands: bool = False,
    ):
        self.port = port
        self.baudrate = baudrate
        self.verbose = verbose
        self.verify_commands = verify_commands
        self._reader_error = None
        self._lock = threading.Lock()
        self._stop_reader = threading.Event()
        self._banner_seen = threading.Event()
        self._reader_thread = None
        self._rx_queue: "Queue[str]" = Queue()
        # Mirrors firmware state so callers can query without a round trip.
        self.active_channel = 0  # raw electrical switch/channel; 0 == all off
        self.active_pin = 0      # logical pin/pixel if selected via switch_to_pin()

        # Open the port. The act of opening toggles DTR -> resets the MCU,
        # which is what we want for a deterministic boot.
        self._ser = serial.Serial()
        self._ser.port = port
        self._ser.baudrate = baudrate
        self._ser.timeout = timeout
        self._ser.write_timeout = 1.0
        try:
            self._ser.open()
        except BaseException:
            self._ser.close()
            raise

        # Start the reader IMMEDIATELY so we don't lose banner bytes while we
        # wait for the bootloader. (Earlier versions cleared the buffer first;
        # on a slow clone that wiped out an already-received banner.)
        try:
            self._reader_thread = threading.Thread(
                target=self._reader_loop, name="ArduinoSwitchMatrixReader", daemon=True
            )
            self._reader_thread.start()

            # Give the bootloader a head start before we even consider sending.
            time.sleep(BOOT_RESET_WAIT_S)

            if wait_for_banner:
                self._await_banner()
        except BaseException:
            self.close()
            raise

    # ---- public API -----------------------------------------------------

    def switch_to_channel(self, channel: int) -> None:
        """
        Exclusively enable raw electrical switch/channel `channel` (1..100).
        If already active, the firmware toggles it off (matrix disabled).
        """
        if not isinstance(channel, int):
            raise TypeError(f"channel must be int, got {type(channel).__name__}")
        if not (1 <= channel <= 100):
            raise ValueError(f"channel must be 1..100, got {channel}")

        self._send(f"E{channel}")
        # Track expected state. The firmware toggles off if it was already on.
        self.active_channel = 0 if self.active_channel == channel else channel
        self.active_pin = 0

    def switch_to_pin(self, pin: int) -> int:
        """
        Exclusively enable the electrical switch mapped to logical stage
        pin/pixel `pin` (1..100). Returns the raw electrical switch number.
        """
        if not isinstance(pin, int):
            raise TypeError(f"pin must be int, got {type(pin).__name__}")
        if not (1 <= pin <= 100):
            raise ValueError(f"pin must be 1..100, got {pin}")

        electrical_switch = PIXEL_TO_ELECTRICAL_SWITCH[pin]
        was_active = self.active_channel == electrical_switch
        self.switch_to_channel(electrical_switch)
        self.active_pin = 0 if was_active else pin
        return electrical_switch

    def turn_all_off(self) -> None:
        """Disable the entire matrix."""
        self._send("0")
        self.active_channel = 0
        self.active_pin = 0

    def sweep_channels(
        self,
        start: int = 1,
        end: int = 100,
        dwell_s: float = 5.0,
        off_at_end: bool = True,
    ) -> None:
        """
        Step through raw electrical switches/channels [start..end] inclusive,
        holding each one active for `dwell_s` seconds. Prints a one-line
        banner per channel so you can verify outputs visually.

        Ctrl+C aborts cleanly and turns the matrix off.
        """
        if not (1 <= start <= 100 and 1 <= end <= 100):
            raise ValueError("start/end must be in 1..100")
        step = 1 if end >= start else -1
        total = abs(end - start) + 1
        # Mute the reader's chatter during the sweep — the sweep prints its
        # own clear status. Restore the user's prior setting at the end.
        prior_verbose = self.verbose
        self.verbose = False
        try:
            print(f"\n=== SWEEP: raw channels {start}..{end} @ {dwell_s:.1f}s each "
                  f"({total} steps) ===")
            print("    Press Ctrl+C to abort.\n")
            t0 = time.monotonic()
            for i, ch in enumerate(range(start, end + step, step), start=1):
                self.switch_to_channel(ch)
                # Drain firmware reply (so it doesn't print after our banner).
                time.sleep(0.05)
                replies = self.read_lines()
                reply_tail = f"  [{replies[-1]}]" if replies else ""
                elapsed = time.monotonic() - t0
                print(f"  [{i:>3}/{total}]  CHANNEL {ch:>3}  ACTIVE   "
                      f"(t+{elapsed:6.1f}s){reply_tail}", flush=True)
                # Sleep in small slices so Ctrl+C is responsive.
                end_t = time.monotonic() + dwell_s
                while time.monotonic() < end_t:
                    time.sleep(min(0.1, end_t - time.monotonic()))
            print(f"\n=== SWEEP COMPLETE in {time.monotonic() - t0:.1f}s ===")
        except KeyboardInterrupt:
            print(f"\n!!! SWEEP ABORTED at channel {self.active_channel} !!!")
        finally:
            if off_at_end:
                self.turn_all_off()
                time.sleep(0.05)
                self.read_lines()  # drain off-reply
                print("    Matrix disabled (all channels OFF).")
            self.verbose = prior_verbose

    def send_raw(self, line: str) -> None:
        """Send an arbitrary line (terminated with '\\n'). For debugging."""
        self._send(line)

    def read_lines(self, timeout: float = 0.0) -> list[str]:
        """
        Drain any pending response lines from the Arduino.
        Returns immediately if `timeout` is 0; otherwise waits up to `timeout`
        seconds for at least one line.
        """
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                line = self._rx_queue.get(timeout=remaining if timeout else 0.0)
                lines.append(line)
            except Empty:
                break
            # After the first line, keep draining without further waiting.
            timeout = 0.0
            deadline = time.monotonic()
        return lines

    def close(self) -> None:
        """Disable matrix and close the port."""
        # Best-effort safety: turn everything off before disconnecting.
        try:
            if self._ser and self._ser.is_open:
                self._send("0")
                # Give firmware a moment to apply.
                time.sleep(0.05)
        except Exception:
            pass

        self._stop_reader.set()
        try:
            self._ser.cancel_read()
        except Exception:
            pass
        if self._reader_thread is not None:
            try:
                self._reader_thread.join(timeout=1.0)
            except RuntimeError:
                pass  # Thread creation may itself have failed.
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass

    # ---- context manager -----------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---- internals ------------------------------------------------------

    def _send(self, payload: str) -> None:
        if not self._ser.is_open:
            raise serial.SerialException("Switch-matrix serial port is closed")
        data = (payload.strip() + "\n").encode("ascii", errors="replace")
        with self._lock:
            self._check_reader()
            if self.verify_commands:
                self.read_lines()  # Discard replies to earlier commands/banner.
            if self._ser.write(data) != len(data):
                raise serial.SerialException("Incomplete switch-matrix serial write")
            # write() has a finite write_timeout; pyserial.flush() on Windows
            # waits for an empty output queue without a deadline of its own.
            if self.verify_commands:
                self._verify_reply(payload.strip())

    def _check_reader(self) -> None:
        if self._reader_error is not None:
            raise serial.SerialException(
                f"Switch-matrix serial reader failed: {self._reader_error}"
            ) from self._reader_error

    def _verify_reply(self, payload: str) -> None:
        if payload == "0":
            expected = "-> ALL channels have been turned OFF."
        elif payload.startswith("E") and payload[1:].isdigit():
            channel = int(payload[1:])
            expected = (
                "-> Matrix Disabled (All channels OFF)."
                if self.active_channel == channel
                else f"-> Electrical Switch {channel}"
            )
        else:
            return  # Raw/debug commands have no guaranteed reply format.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self._check_reader()
            for line in self.read_lines(timeout=min(0.1, max(0.0, deadline - time.monotonic()))):
                if line.strip() == expected:
                    return
                if line.startswith("Error:"):
                    raise serial.SerialException(f"Switch-matrix command rejected: {line}")
        raise serial.SerialException(
            f"Switch-matrix acknowledgement timeout on {self.port} for {payload!r}"
        )

    def _reader_loop(self) -> None:
        buf = bytearray()
        while not self._stop_reader.is_set():
            try:
                chunk = self._ser.read(64)
            except Exception as exc:
                if not self._stop_reader.is_set():
                    self._reader_error = exc
                break
            if not chunk:
                continue
            buf.extend(chunk)
            while b"\n" in buf:
                line_bytes, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                line = line_bytes.decode("ascii", errors="replace").rstrip("\r")
                if not line:
                    continue
                self._rx_queue.put(line)
                if BANNER_READY_TOKEN in line:
                    self._banner_seen.set()
                if self.verbose:
                    print(f"[arduino] {line}")

    def banner_seen(self) -> bool:
        """True once the firmware's 'ready' banner has been observed."""
        return self._banner_seen.is_set()

    def _await_banner(self) -> None:
        """
        Block until we see the firmware "ready" banner, or until timeout.
        The reader thread sets `_banner_seen` as soon as it spots the token,
        so this just waits on that event.
        """
        if self._banner_seen.wait(timeout=BOOT_BANNER_TIMEOUT_S):
            # Give the trailing banner line a beat to arrive so it doesn't
            # interleave with subsequent prompt output.
            time.sleep(0.3)
            return
        if self.verbose:
            print(
                "[arduino] WARNING: no boot banner seen within "
                f"{BOOT_BANNER_TIMEOUT_S:.1f}s. Continuing anyway — "
                "if commands don't respond, try unplugging/replugging the Nano."
            )


# ---------------------------------------------------------------------------
# Interactive CLI (replicates the Arduino IDE Serial Monitor experience)
# ---------------------------------------------------------------------------

HELP_TEXT = """\
Commands:
  1..100     switch logical pin/pixel via the configured wiring map
  channel N  switch raw electrical switch/channel N
  0          turn ALL channels OFF
  status     show locally-tracked active pin/channel
  ports      list available serial ports
  raw <txt>  send arbitrary text to the Arduino
  help / ?   show this help
  quit / q   exit
"""


def _list_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return
    for p in ports:
        print(f"  {p.device:<8}  {p.description}")


def _drain_and_print(matrix: ArduinoSwitchMatrix, settle_s: float = 0.15) -> None:
    """Wait briefly then print whatever the Arduino replied with."""
    time.sleep(settle_s)
    for line in matrix.read_lines():
        print(f"  {line}")


def _interactive(matrix: ArduinoSwitchMatrix) -> int:
    # Heads-up for users running this from Spyder/IPython runfile(): input()
    # is flaky there. The class still works fine programmatically.
    if "ipykernel" in sys.modules or "spyder_kernels" in sys.modules:
        print(
            "[hint] Detected Spyder/IPython. The interactive prompt can be flaky here.\n"
            "       For the smoothest experience, run from a plain terminal:\n"
            "         python arduino_switch_matrix.py\n"
            "       Or drive the matrix programmatically from your IPython console:\n"
            "         from arduino_switch_matrix import ArduinoSwitchMatrix\n"
            "         m = ArduinoSwitchMatrix(); m.switch_to_pin(7)"
        )

    # Silence the reader's auto-print so it doesn't interleave with input().
    # We'll drain and print replies ourselves after each command.
    matrix.verbose = False

    print("=" * 53)
    ready = "READY" if matrix.banner_seen() else "NO BANNER (firmware may not be running)"
    print(f"Connected to {matrix.port} @ {matrix.baudrate} baud  [{ready}]")
    print("Type 'help' for commands, 'quit' to exit.")
    print("=" * 53)
    # Discard any lingering banner lines so the first command's reply is clean.
    matrix.read_lines()

    while True:
        try:
            sys.stdout.write("> ")
            sys.stdout.flush()
            raw = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if raw == "":  # EOF
            print()
            break
        raw = raw.strip()

        if not raw:
            continue

        low = raw.lower()
        if low in ("quit", "q", "exit"):
            break
        if low in ("help", "?", "h"):
            print(HELP_TEXT)
            continue
        if low == "status":
            ch = matrix.active_channel
            pin = matrix.active_pin
            if ch and pin:
                print(f"  active pin = {pin}, electrical switch/channel = {ch}")
            else:
                print(f"  active electrical switch/channel = {ch if ch else 'none (all off)'}")
            continue
        if low == "ports":
            _list_ports()
            continue
        if low.startswith("channel "):
            try:
                n = int(raw.split(None, 1)[1])
            except (IndexError, ValueError):
                print("  ?  enter 'channel N' with N in 1-100")
                continue
            try:
                matrix.switch_to_channel(n)
            except ValueError as e:
                print(f"  ! {e}")
                continue
            _drain_and_print(matrix)
            continue
        if low.startswith("raw "):
            matrix.send_raw(raw[4:])
            _drain_and_print(matrix)
            continue

        # Numeric input -> logical stage pin/pixel.
        try:
            n = int(raw)
        except ValueError:
            print("  ?  enter a pin number 0-100, 'channel N', or 'help'")
            continue

        try:
            if n == 0:
                matrix.turn_all_off()
            else:
                electrical_switch = matrix.switch_to_pin(n)
                print(f"  pin {n} -> electrical switch/channel {electrical_switch}")
        except ValueError as e:
            print(f"  ! {e}")
            continue

        _drain_and_print(matrix)

    return 0


def _running_under_ipython() -> bool:
    return "ipykernel" in sys.modules or "spyder_kernels" in sys.modules


def _get_ipython_ns():
    """Return the IPython user_ns dict, or None if not running in IPython."""
    try:
        from IPython import get_ipython  # type: ignore
        ip = get_ipython()
        if ip is None:
            return None
        return ip.user_ns
    except Exception:
        return None


def _close_prior_matrix_in_ipython() -> None:
    """
    If a previous run left an ArduinoSwitchMatrix bound in the IPython
    namespace (as `m` or `matrix`), close it so we can reopen the port.
    Without this, re-running the script in Spyder fails with
    PermissionError(13) because Windows still hands the port to the old object.
    """
    ns = _get_ipython_ns()
    if ns is None:
        return
    for name in ("m", "matrix"):
        obj = ns.get(name)
        if isinstance(obj, ArduinoSwitchMatrix):
            try:
                obj.close()
            except Exception:
                pass
            ns.pop(name, None)


def _inject_into_ipython(matrix: ArduinoSwitchMatrix) -> bool:
    """Best-effort: drop the matrix into the IPython user namespace as `m`."""
    ns = _get_ipython_ns()
    if ns is None:
        return False
    ns["m"] = matrix
    ns["matrix"] = matrix
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive Python serial console for the 100-channel switching-matrix Arduino."
    )
    parser.add_argument("-p", "--port", default=DEFAULT_PORT,
                        help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD,
                        help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--list-ports", action="store_true",
                        help="List available serial ports and exit.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress live Arduino output (still queued).")
    parser.add_argument("--pin", type=int, default=None,
                        help="One-shot: switch logical pin/pixel via configured wiring map and exit.")
    parser.add_argument("-c", "--channel", type=int, default=None,
                        help="One-shot: switch raw electrical switch/channel and exit (use 0 for all-off).")
    parser.add_argument("--sweep", action="store_true",
                        help="Sweep through raw electrical switches/channels and exit. "
                             "Use with --from / --to / --dwell.")
    parser.add_argument("--no-sweep", action="store_true",
                        help="Disable the default sweep when run from Spyder.")
    parser.add_argument("--from", dest="sweep_from", type=int, default=1,
                        help="Sweep start raw electrical switch/channel (default 1).")
    parser.add_argument("--to", dest="sweep_to", type=int, default=100,
                        help="Sweep end raw electrical switch/channel (default 100).")
    parser.add_argument("--dwell", type=float, default=5.0,
                        help="Seconds per channel during sweep (default 5).")
    args = parser.parse_args(argv)

    if args.list_ports:
        _list_ports()
        return 0

    # If we're inside an IPython kernel, a prior run may still hold the port.
    # Close any stale `m`/`matrix` it left in the namespace before reopening.
    _close_prior_matrix_in_ipython()

    try:
        matrix = ArduinoSwitchMatrix(
            port=args.port, baudrate=args.baud, verbose=not args.quiet
        )
    except serial.SerialException as e:
        msg = str(e)
        print(f"ERROR: could not open {args.port}: {msg}", file=sys.stderr)
        if "Access is denied" in msg or "PermissionError" in msg:
            print(
                "\nThe port is held by another process. Likely causes:\n"
                "  - Arduino IDE Serial Monitor is open  -> close it.\n"
                "  - A previous Spyder run still owns it -> Restart kernel,\n"
                "    or run:  m.close()  (if `m` exists in your namespace).\n"
                "  - Another script (e.g. POL_Chip_Test_Working_2026.py) is\n"
                "    using it.\n"
                "  - CH340 driver hiccup -> unplug/replug the Nano.",
                file=sys.stderr,
            )
        print("\nAvailable ports:", file=sys.stderr)
        _list_ports()
        return 2

    # One-shot logical pin mode: do the action and exit, closing cleanly.
    if args.pin is not None:
        try:
            electrical_switch = matrix.switch_to_pin(args.pin)
            print(f"pin {args.pin} -> electrical switch/channel {electrical_switch}")
            time.sleep(0.2)
            return 0
        finally:
            matrix.close()

    # One-shot raw electrical-switch mode: do the action and exit, closing cleanly.
    if args.channel is not None:
        try:
            if args.channel == 0:
                matrix.turn_all_off()
            else:
                matrix.switch_to_channel(args.channel)
            time.sleep(0.2)
            return 0
        finally:
            matrix.close()

    # Explicit sweep mode (works from terminal too).
    if args.sweep:
        try:
            matrix.sweep_channels(
                start=args.sweep_from, end=args.sweep_to, dwell_s=args.dwell
            )
            return 0
        finally:
            matrix.close()

    # Spyder/IPython: stdin is not interactively usable. Run a sweep by
    # default (the main use-case), then hand the live matrix to IPython so
    # the user can keep poking at individual pins/channels afterwards.
    if _running_under_ipython():
        injected = _inject_into_ipython(matrix)
        if not injected:
            print(
                "Running under IPython but could not inject `m`. "
                "Run from your IPython console instead:\n"
                "    from arduino_switch_matrix import ArduinoSwitchMatrix\n"
                "    m = ArduinoSwitchMatrix(port='COM10')"
            )
            matrix.close()
            return 1

        print()
        print("=" * 60)
        print(f"Matrix is live on {matrix.port} and bound to `m` (and `matrix`).")
        print("=" * 60)

        if not args.no_sweep:
            try:
                matrix.sweep_channels(
                    start=args.sweep_from,
                    end=args.sweep_to,
                    dwell_s=args.dwell,
                )
            except Exception as e:
                print(f"\n!!! Sweep error: {e}")

        print()
        print("Matrix is still connected. Drive it from the console, e.g.:")
        print("    m.switch_to_pin(7)                     # logical pin/pixel -> mapped switch")
        print("    m.switch_to_channel(7)                 # raw electrical switch/channel")
        print("    m.sweep_channels(1, 100, dwell_s=5)   # raw electrical switch sweep")
        print("    m.turn_all_off()")
        print("    m.close()                              # release COM10 when done")
        return 0

    # Real terminal: run the interactive loop and close on exit.
    try:
        return _interactive(matrix)
    finally:
        matrix.close()


if __name__ == "__main__":
    raise SystemExit(main())
