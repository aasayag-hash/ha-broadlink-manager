from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("broadlink_manager.devices_store")

# /data is the add-on's persistent volume. BROADLINK_MANAGER_DATA_DIR overrides it
# so the app can be run outside a container during development.
DATA_DIR = Path(os.environ.get("BROADLINK_MANAGER_DATA_DIR", "/data"))
DEVICES_FILE = DATA_DIR / "devices.json"

_lock = threading.Lock()


def _atomic_write(data: list[dict[str, Any]]) -> None:
    """Write via temp file + os.replace so a crash can't truncate the store.

    write_text() opens with "w" (truncate) and then writes, so being killed
    mid-write leaves a partial file that fails to parse on the next boot. The
    manually added devices are the ones that hurt to lose: they were typed in
    precisely because discovery cannot find them on its own.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)

    fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".devices-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, DEVICES_FILE)
    except BaseException:
        # Never leave a stray temp file behind on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_devices() -> list[dict[str, Any]]:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not DEVICES_FILE.exists():
            return []

        try:
            raw = DEVICES_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("No se pudo leer %s: %s", DEVICES_FILE, exc)
            return []

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            # Preserve the bad file instead of letting the next save overwrite
            # it: it holds the IPs of devices the user added by hand.
            backup = DEVICES_FILE.with_suffix(".corrupt")
            logger.error(
                "%s está corrupto (%s). Se preserva una copia en %s y se arranca sin dispositivos.",
                DEVICES_FILE,
                exc,
                backup,
            )
            try:
                os.replace(DEVICES_FILE, backup)
            except OSError:
                pass
            return []

        if not isinstance(data, list):
            logger.error("%s no contiene una lista; se ignora.", DEVICES_FILE)
            return []
        return data


def save_devices(devices: list[dict[str, Any]]) -> None:
    with _lock:
        _atomic_write(devices)
