#!/usr/bin/env python3
"""
Helpers for resolving Windows COM ports by instrument identity.

Windows can renumber USB virtual COM ports when instruments are moved between
USB sockets or hubs. These helpers prefer stable device identity over hard-coded
COM numbers and keep the automation script from needing Device Manager edits.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Iterable

import serial
from serial.tools import list_ports


AUTO_PORT = "auto"
ARDUINO_READY_TOKEN = "Signal Matrix Ready"


@dataclass
class PortResolution:
    port: str | None
    method: str
    message: str


def normalize_com_port(port: str | None) -> str | None:
    """Normalize values such as 'COM 11' to 'COM11'."""
    if port is None:
        return None
    value = str(port).strip()
    if not value:
        return value
    if value.lower() == AUTO_PORT:
        return AUTO_PORT
    match = re.fullmatch(r"(?i)\s*COM\s*(\d+)\s*", value)
    if match:
        return f"COM{int(match.group(1))}"
    return value


def is_auto_port(port: str | None) -> bool:
    return normalize_com_port(port) == AUTO_PORT


def list_port_details(
    port_labels: dict[str, str] | None = None,
    include_bluetooth: bool = False,
) -> list[str]:
    ports = list(list_ports.comports())
    if not include_bluetooth:
        ports = [p for p in ports if not _looks_like_bluetooth_metadata(p)]
    if not ports:
        return ["No serial ports detected."]
    return [describe_port(p, port_labels=port_labels) for p in ports]


def describe_port(port_info, port_labels: dict[str, str] | None = None) -> str:
    bits = [port_info.device]
    role = identify_port_role(port_info, port_labels=port_labels)
    if role:
        bits.append(f"[{role}]")
    desc = getattr(port_info, "description", "") or ""
    manufacturer = getattr(port_info, "manufacturer", "") or ""
    product = getattr(port_info, "product", "") or ""
    serial_number = getattr(port_info, "serial_number", "") or ""
    hwid = getattr(port_info, "hwid", "") or ""

    if desc and desc != "n/a":
        bits.append(desc)
    if manufacturer:
        bits.append(f"manufacturer={manufacturer}")
    if product:
        bits.append(f"product={product}")
    if serial_number:
        bits.append(f"serial={serial_number}")
    if hwid:
        bits.append(f"hwid={hwid}")
    return " | ".join(bits)


def identify_port_role(port_info, port_labels: dict[str, str] | None = None) -> str | None:
    device = normalize_com_port(getattr(port_info, "device", None))
    labels = {_normalise_label_key(k): v for k, v in (port_labels or {}).items()}
    serial_number = getattr(port_info, "serial_number", "") or ""

    serial_key = _normalise_label_key(f"serial:{serial_number}") if serial_number else None
    if serial_key and serial_key in labels:
        return labels[serial_key]

    device_key = _normalise_label_key(device)
    if device_key in labels:
        return labels[device_key]

    info = _info_text(port_info)
    for key, label in labels.items():
        if key.startswith("hwid:") and key[5:] in info:
            return label

    if _looks_like_arduino_metadata(port_info):
        return "Arduino switch matrix"
    if _looks_like_smu_metadata(port_info):
        return "SMU4201"
    if _looks_like_funcgen_metadata(port_info):
        return "Aim-TTi function generator"
    if _looks_like_bluetooth_metadata(port_info):
        return "Bluetooth serial link"
    return None


def _normalise_label_key(key: str | None) -> str:
    return str(normalize_com_port(key) or "").strip().lower()


def resolve_arduino_switch_matrix_port(
    requested_port: str | None,
    baudrate: int = 9600,
    exclude_ports: Iterable[str | None] = (),
) -> PortResolution:
    requested_port = normalize_com_port(requested_port)
    if not is_auto_port(requested_port):
        return PortResolution(requested_port, "manual", f"Using requested Arduino port {requested_port}.")

    excluded = _normalised_set(exclude_ports)
    ports = [p for p in list_ports.comports() if normalize_com_port(p.device) not in excluded]

    metadata_matches = [p for p in ports if _looks_like_arduino_metadata(p)]
    if len(metadata_matches) == 1:
        p = metadata_matches[0]
        return PortResolution(
            normalize_com_port(p.device),
            "metadata",
            f"Arduino switch matrix matched by USB metadata: {describe_port(p)}",
        )

    banner_matches = []
    for p in ports:
        device = normalize_com_port(p.device)
        if not device:
            continue
        if _arduino_banner_seen(device, baudrate=baudrate):
            banner_matches.append(p)

    if len(banner_matches) == 1:
        p = banner_matches[0]
        return PortResolution(
            normalize_com_port(p.device),
            "banner",
            f"Arduino switch matrix matched by firmware banner: {describe_port(p)}",
        )

    if metadata_matches:
        details = "; ".join(describe_port(p) for p in metadata_matches)
        return PortResolution(
            None,
            "ambiguous",
            "More than one Arduino-like serial port was found: " + details,
        )

    if banner_matches:
        details = "; ".join(describe_port(p) for p in banner_matches)
        return PortResolution(
            None,
            "ambiguous",
            "More than one switch-matrix firmware banner was found: " + details,
        )

    return PortResolution(None, "not_found", "Arduino switch matrix was not found.")


def resolve_smu4201_port(
    requested_port: str | None,
    baudrate: int = 9600,
    exclude_ports: Iterable[str | None] = (),
) -> PortResolution:
    requested_port = normalize_com_port(requested_port)
    if not is_auto_port(requested_port):
        return PortResolution(requested_port, "manual", f"Using requested SMU port {requested_port}.")

    excluded = _normalised_set(exclude_ports)
    ports = [p for p in list_ports.comports() if normalize_com_port(p.device) not in excluded]

    metadata_matches = [p for p in ports if _looks_like_smu_metadata(p)]
    if len(metadata_matches) == 1:
        p = metadata_matches[0]
        return PortResolution(
            normalize_com_port(p.device),
            "metadata",
            f"SMU4201 matched by USB metadata: {describe_port(p)}",
        )

    idn_matches = []
    for p in ports:
        device = normalize_com_port(p.device)
        if not device:
            continue
        idn = _query_serial_idn(device, baudrate=baudrate)
        if _is_smu4201_idn(idn):
            idn_matches.append((p, idn))

    if len(idn_matches) == 1:
        p, idn = idn_matches[0]
        return PortResolution(
            normalize_com_port(p.device),
            "idn",
            f"SMU4201 matched by *IDN? on {p.device}: {idn}",
        )

    if metadata_matches:
        details = "; ".join(describe_port(p) for p in metadata_matches)
        return PortResolution(
            None,
            "ambiguous",
            "More than one SMU-like serial port was found: " + details,
        )

    if idn_matches:
        details = "; ".join(f"{p.device}: {idn}" for p, idn in idn_matches)
        return PortResolution(
            None,
            "ambiguous",
            "More than one SMU4201 replied to *IDN?: " + details,
        )

    return PortResolution(None, "not_found", "SMU4201 was not found.")


def _normalised_set(ports: Iterable[str | None]) -> set[str]:
    return {p for p in (normalize_com_port(port) for port in ports) if p}


def _info_text(port_info) -> str:
    fields = (
        getattr(port_info, "description", "") or "",
        getattr(port_info, "manufacturer", "") or "",
        getattr(port_info, "product", "") or "",
        getattr(port_info, "hwid", "") or "",
    )
    return " ".join(fields).lower()


def _looks_like_arduino_metadata(port_info) -> bool:
    text = _info_text(port_info)
    return any(
        token in text
        for token in (
            "arduino",
            "wch.cn",
            "ch340",
            "ch341",
            "vid:pid=1a86:7523",
        )
    )


def _looks_like_smu_metadata(port_info) -> bool:
    text = _info_text(port_info)
    return any(
        token in text
        for token in (
            "smu4201",
            "smu4000",
            # Aim-TTi SMU4201 on this setup reports as a generic Microsoft
            # USB Serial Device, so VID/PID is the stable identifier.
            "vid:pid=103e:04f6",
        )
    )


def _looks_like_funcgen_metadata(port_info) -> bool:
    text = _info_text(port_info)
    return any(token in text for token in ("vid:pid=103e:0500",))


def _looks_like_bluetooth_metadata(port_info) -> bool:
    text = _info_text(port_info)
    return "bthenum" in text or "bluetooth" in text


def _arduino_banner_seen(port: str, baudrate: int) -> bool:
    try:
        with serial.Serial(port, baudrate, timeout=0.1, write_timeout=0.5) as ser:
            # Opening the Nano resets it. Give the bootloader/user sketch time
            # to print the same banner ArduinoSwitchMatrix waits for.
            deadline = time.monotonic() + 5.0
            buf = bytearray()
            while time.monotonic() < deadline:
                chunk = ser.read(128)
                if chunk:
                    buf.extend(chunk)
                    if ARDUINO_READY_TOKEN.encode("ascii") in buf:
                        return True
                else:
                    time.sleep(0.05)
    except (OSError, serial.SerialException):
        return False
    return False


def _query_serial_idn(port: str, baudrate: int) -> str | None:
    try:
        with serial.Serial(
            port,
            baudrate,
            timeout=0.5,
            write_timeout=0.5,
            bytesize=8,
            parity="N",
            stopbits=1,
            rtscts=False,
            dsrdtr=False,
        ) as ser:
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(b"*IDN?\r\n")
            ser.flush()
            deadline = time.monotonic() + 1.0
            chunks = []
            while time.monotonic() < deadline:
                line = ser.readline()
                if line:
                    chunks.append(line)
                    if b"\n" in line:
                        break
            if not chunks:
                return None
            return b"".join(chunks).decode("ascii", errors="replace").strip()
    except (OSError, serial.SerialException):
        return None


def _is_smu4201_idn(response: str | None) -> bool:
    if not response:
        return False
    text = response.lower()
    return "smu4201" in text or "smu4000" in text
