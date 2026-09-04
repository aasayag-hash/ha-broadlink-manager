from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("broadlink_manager.settings_store")

_lock = threading.Lock()


def _data_dir() -> Path:
    return Path(os.environ.get("BROADLINK_MANAGER_DATA_DIR", "/data"))


def _settings_file() -> Path:
    return _data_dir() / "settings.json"


def _read() -> dict[str, Any]:
    path = _settings_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error("No se pudo leer %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    directory = _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(dir=str(directory), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, _settings_file())
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_mqtt() -> dict[str, Any] | None:
    """Manual broker settings, or None to fall back to the Supervisor's."""
    with _lock:
        mqtt = _read().get("mqtt")
    return mqtt if isinstance(mqtt, dict) and mqtt.get("host") else None


def set_mqtt(config: dict[str, Any] | None) -> None:
    """Store manual broker settings, or None to go back to auto-detection."""
    with _lock:
        data = _read()
        if config is None:
            data.pop("mqtt", None)
        else:
            data["mqtt"] = config
        _write(data)
