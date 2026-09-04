from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("broadlink_manager.storage")

def _config_dir() -> Path:
    """Where Home Assistant's config lives.

    Read per call rather than captured at import: BROADLINK_MANAGER_CONFIG_DIR
    is set by tests and by local runs, and binding it at import time silently
    wrote backups to /config while the codes went to the temp dir.
    """
    return Path(os.environ.get("BROADLINK_MANAGER_CONFIG_DIR", "/config"))


def _backup_dir() -> Path:
    return _config_dir() / "broadlink_manager" / "backups"

# Home Assistant's CODE_STORAGE_VERSION. Refusing to write anything else is the
# whole point: a future version could change the layout, and blindly writing v1
# into a v2 file would destroy every code the user has.
STORAGE_VERSION = 1

MAX_BACKUPS = 20

_lock = threading.Lock()


class StorageError(Exception):
    """Raised when the codes file cannot be read or written safely."""


def normalize_mac(mac: str) -> str:
    """Return the MAC as Home Assistant writes it into the file name.

    The config flow calls async_set_unique_id(device.mac.hex()), so the file is
    broadlink_remote_<12 lowercase hex chars>_codes with no separators. Getting
    this wrong means writing into a file no integration reads.
    """
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "").strip().lower()
    if len(cleaned) != 12 or not all(c in "0123456789abcdef" for c in cleaned):
        raise StorageError(f"'{mac}' no es una MAC válida")
    return cleaned


def codes_path(mac: str) -> Path:
    return _config_dir() / ".storage" / f"broadlink_remote_{normalize_mac(mac)}_codes"


def read_codes(mac: str) -> dict[str, dict[str, Any]]:
    """Return {device: {command: code}} for one Broadlink, or {} if unknown.

    Values are either a base64 string or, for commands learned with the
    'alternative' flag, a two-item list that Home Assistant alternates between
    on each send. Both shapes are passed through untouched.
    """
    path = codes_path(mac)
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"No se pudo leer {path.name}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise StorageError(
            f"{path.name} no es un JSON válido ({exc}). No se toca el archivo para no "
            "perder los códigos que pueda contener."
        ) from exc

    version = payload.get("version")
    if version != STORAGE_VERSION:
        raise StorageError(
            f"{path.name} está en la versión {version} y este add-on solo entiende la "
            f"{STORAGE_VERSION}. No se modifica nada para no romper los códigos existentes."
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise StorageError(f"{path.name} no tiene el bloque 'data' esperado.")

    # Drop anything not shaped like {subdevice: {command: code}} rather than
    # letting it reach the UI: a malformed entry here would break rendering for
    # every other code in the file.
    result: dict[str, dict[str, Any]] = {}
    for subdevice, commands in data.items():
        if isinstance(commands, dict):
            result[str(subdevice)] = dict(commands)
        else:
            logger.warning("Se ignora '%s' en %s: no es un diccionario.", subdevice, path.name)
    return result


def _backup(path: Path) -> None:
    """Copy the current file aside before it is modified.

    Home Assistant warns against editing .storage by hand, so every write keeps
    the previous state recoverable. Backups are pruned to the newest MAX_BACKUPS
    so this cannot slowly fill the config volume.
    """
    if not path.exists():
        return

    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Milliseconds, not seconds: several edits in a row are normal (rename then
    # move, say), and a second-resolution stamp made them overwrite each other,
    # leaving only one recovery point for a whole batch of changes.
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    target = backup_dir / f"{path.name}.{stamp}.json"
    try:
        target.write_bytes(path.read_bytes())
    except OSError as exc:
        # A failed backup must stop the write: proceeding would leave the user
        # with no way back if the new content turns out wrong.
        raise StorageError(f"No se pudo respaldar {path.name}: {exc}") from exc

    backups = sorted(backup_dir.glob(f"{path.name}.*.json"))
    for old in backups[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_codes(mac: str, mutate) -> dict[str, dict[str, Any]]:
    """Re-read, apply mutate(codes) and write the result back.

    mutate receives the current {device: {command: code}} and edits it in place.
    Reading inside the lock right before writing is what makes this safe to run
    while Home Assistant is live: HA defers its own save by 15 seconds, so a
    read taken earlier in the request could easily be stale by now, and writing
    it back would silently drop a code the user just learned through HA itself.
    """
    path = codes_path(mac)
    with _lock:
        current = read_codes(mac)
        mutate(current)

        _backup(path)
        try:
            _atomic_write(path, {"version": STORAGE_VERSION, "key": path.name, "data": current})
        except OSError as exc:
            raise StorageError(f"No se pudo escribir {path.name}: {exc}") from exc
        return current


def save_code(mac: str, subdevice: str, command: str, code: str) -> None:
    """Store one code, creating the subdevice group if needed."""
    subdevice = subdevice.strip()
    command = command.strip()
    if not subdevice:
        raise StorageError("El nombre del equipo no puede estar vacío")
    if not command:
        raise StorageError("El nombre del comando no puede estar vacío")

    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        codes.setdefault(subdevice, {})[command] = code

    write_codes(mac, mutate)


def delete_code(mac: str, subdevice: str, command: str) -> None:
    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        group = codes.get(subdevice)
        if group is None or command not in group:
            raise StorageError(f"No existe el comando '{command}' en '{subdevice}'")
        del group[command]
        # Drop the group once its last command is gone: an empty subdevice would
        # otherwise linger in the table and in remote.send_command's autocomplete.
        if not group:
            del codes[subdevice]

    write_codes(mac, mutate)


def rename_code(mac: str, subdevice: str, command: str, new_command: str) -> None:
    new_command = new_command.strip()
    if not new_command:
        raise StorageError("El nombre del comando no puede estar vacío")

    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        group = codes.get(subdevice)
        if group is None or command not in group:
            raise StorageError(f"No existe el comando '{command}' en '{subdevice}'")
        if new_command != command and new_command in group:
            raise StorageError(f"Ya existe un comando '{new_command}' en '{subdevice}'")
        group[new_command] = group.pop(command)

    write_codes(mac, mutate)


def move_code(mac: str, subdevice: str, command: str, new_subdevice: str) -> None:
    new_subdevice = new_subdevice.strip()
    if not new_subdevice:
        raise StorageError("El nombre del equipo no puede estar vacío")

    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        group = codes.get(subdevice)
        if group is None or command not in group:
            raise StorageError(f"No existe el comando '{command}' en '{subdevice}'")
        target = codes.setdefault(new_subdevice, {})
        if command in target and new_subdevice != subdevice:
            raise StorageError(f"Ya existe un comando '{command}' en '{new_subdevice}'")
        target[command] = group.pop(command)
        if not group and subdevice != new_subdevice:
            del codes[subdevice]

    write_codes(mac, mutate)


def rename_subdevice(mac: str, subdevice: str, new_subdevice: str) -> None:
    """Rename a whole group.

    Note for callers: remote.send_command references the group by name, so any
    automation or script using the old name keeps pointing at something that no
    longer exists. The UI has to warn about this -- the add-on cannot rewrite
    the user's automations.
    """
    new_subdevice = new_subdevice.strip()
    if not new_subdevice:
        raise StorageError("El nombre del equipo no puede estar vacío")

    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        if subdevice not in codes:
            raise StorageError(f"No existe el equipo '{subdevice}'")
        if new_subdevice != subdevice and new_subdevice in codes:
            raise StorageError(f"Ya existe un equipo llamado '{new_subdevice}'")
        codes[new_subdevice] = codes.pop(subdevice)

    write_codes(mac, mutate)


def delete_subdevice(mac: str, subdevice: str) -> None:
    def mutate(codes: dict[str, dict[str, Any]]) -> None:
        if subdevice not in codes:
            raise StorageError(f"No existe el equipo '{subdevice}'")
        del codes[subdevice]

    write_codes(mac, mutate)
