from __future__ import annotations

import logging
import time
from typing import Any

from . import storage

logger = logging.getLogger("broadlink_manager.transfer")

# Bumped only on a breaking change to the export layout. An import refuses a
# version it does not know rather than guessing at the shape.
EXPORT_VERSION = 1

# Modes for an incoming code whose name is already taken.
MODE_SKIP = "skip"
MODE_OVERWRITE = "overwrite"
MODE_RENAME = "rename"
VALID_MODES = (MODE_SKIP, MODE_OVERWRITE, MODE_RENAME)


class TransferError(Exception):
    """Raised when an export or import cannot proceed."""


def export_codes(mac: str, subdevices: list[str] | None = None) -> dict[str, Any]:
    """Build a portable snapshot of the stored codes.

    Deliberately its own format rather than a copy of HA's file: this one is
    meant to be read, kept as a backup and moved between installs, so it must
    not break when HA changes its internal layout.
    """
    codes = storage.read_codes(mac)
    if subdevices:
        missing = [s for s in subdevices if s not in codes]
        if missing:
            raise TransferError(f"No existe el equipo '{missing[0]}'")
        codes = {name: group for name, group in codes.items() if name in subdevices}

    return {
        "format": "broadlink_manager_codes",
        "version": EXPORT_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # Informative only: an import can target any device, since codes are not
        # tied to the Broadlink that captured them.
        "source_mac": mac,
        "devices": {
            name: {command: code for command, code in sorted(group.items())}
            for name, group in sorted(codes.items())
        },
    }


def _validate(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TransferError("El archivo no contiene un objeto JSON válido.")

    if payload.get("format") != "broadlink_manager_codes":
        raise TransferError(
            "El archivo no parece un export de este add-on. Tiene que tener "
            '"format": "broadlink_manager_codes".'
        )

    version = payload.get("version")
    if version != EXPORT_VERSION:
        raise TransferError(
            f"El archivo está en la versión {version} y este add-on entiende la "
            f"{EXPORT_VERSION}."
        )

    devices = payload.get("devices")
    if not isinstance(devices, dict) or not devices:
        raise TransferError("El archivo no tiene ningún equipo con códigos.")

    cleaned: dict[str, dict[str, Any]] = {}
    for name, group in devices.items():
        if not isinstance(group, dict):
            raise TransferError(f"El equipo '{name}' no tiene un formato válido.")
        for command, code in group.items():
            # A string or a two-item list (a toggle command); anything else would
            # be written into .storage and break remote.send_command later.
            valid_string = isinstance(code, str) and code
            valid_toggle = (
                isinstance(code, list)
                and len(code) == 2
                and all(isinstance(c, str) and c for c in code)
            )
            if not (valid_string or valid_toggle):
                raise TransferError(
                    f"El código '{name} / {command}' no es válido: se esperaba texto en "
                    "base64, o una lista de dos para un comando toggle."
                )
        cleaned[str(name)] = dict(group)
    return cleaned


def preview_import(mac: str, payload: Any) -> dict[str, Any]:
    """Report what an import would do, without writing anything.

    Shown before importing because the destructive part -- overwriting a code
    that already exists -- is invisible otherwise.
    """
    incoming = _validate(payload)
    existing = storage.read_codes(mac)

    groups = []
    for name, group in sorted(incoming.items()):
        current = existing.get(name, {})
        groups.append(
            {
                "subdevice": name,
                "new": sorted(c for c in group if c not in current),
                "conflicting": sorted(c for c in group if c in current),
            }
        )

    return {
        "groups": groups,
        "total": sum(len(g) for g in incoming.values()),
        "conflicts": sum(len(g["conflicting"]) for g in groups),
    }


def import_codes(mac: str, payload: Any, mode: str = MODE_SKIP) -> dict[str, Any]:
    """Merge an export into the stored codes. Returns a summary of what changed."""
    if mode not in VALID_MODES:
        raise TransferError(f"Modo desconocido '{mode}'")

    incoming = _validate(payload)
    added: list[str] = []
    overwritten: list[str] = []
    renamed: list[str] = []
    skipped: list[str] = []

    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        for name, group in incoming.items():
            target = codes.setdefault(name, {})
            for command, code in group.items():
                label = f"{name} / {command}"
                if command not in target:
                    target[command] = code
                    added.append(label)
                elif mode == MODE_OVERWRITE:
                    target[command] = code
                    overwritten.append(label)
                elif mode == MODE_RENAME:
                    # Keep both: the existing code may be the one wired into an
                    # automation, so it must not move or change.
                    suffix = 2
                    while f"{command}_{suffix}" in target:
                        suffix += 1
                    new_name = f"{command}_{suffix}"
                    target[new_name] = code
                    renamed.append(f"{label} -> {new_name}")
                else:
                    skipped.append(label)

            # An import of only-conflicting codes in skip mode must not leave an
            # empty group behind.
            if not target:
                del codes[name]

    # One write for the whole file: a per-code write would leave a half-imported
    # set behind if it failed midway, and 40 backups for one import.
    storage.write_codes(mac, mutate)

    return {
        "added": added,
        "overwritten": overwritten,
        "renamed": renamed,
        "skipped": skipped,
    }
