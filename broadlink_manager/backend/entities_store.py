from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("broadlink_manager.entities_store")

_lock = threading.Lock()


def _data_dir() -> Path:
    """Read per call so tests and local runs can redirect it.

    Capturing this at import time once sent backups to /config while the data
    went elsewhere; the same mistake is easy to repeat here.
    """
    return Path(os.environ.get("BROADLINK_MANAGER_DATA_DIR", "/data"))


def _entities_file() -> Path:
    return _data_dir() / "entities.json"


def _atomic_write(data: list[dict[str, Any]]) -> None:
    """Write via temp file + os.replace so a crash can't truncate the store."""
    directory = _data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)

    fd, tmp_path = tempfile.mkstemp(dir=str(directory), prefix=".entities-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, _entities_file())
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_entities(mac: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        path = _entities_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error("No se pudo leer %s: %s", path, exc)
            return []

    if not isinstance(data, list):
        logger.error("%s no contiene una lista; se ignora.", _entities_file())
        return []
    return [e for e in data if mac is None or e.get("mac") == mac]


def save_entities(entities: list[dict[str, Any]]) -> None:
    with _lock:
        _atomic_write(entities)


def add(entity: dict[str, Any]) -> None:
    entities = list_entities()
    # Replace rather than append when the slug already exists: republishing the
    # same entity is how an edit works, and two rows for one entity would leave
    # an orphan that nothing can delete.
    entities = [
        e for e in entities if not (e["mac"] == entity["mac"] and e["slug"] == entity["slug"])
    ]
    entities.append(entity)
    save_entities(entities)


def remove(mac: str, slug: str) -> dict[str, Any] | None:
    entities = list_entities()
    match = next((e for e in entities if e["mac"] == mac and e["slug"] == slug), None)
    if match is None:
        return None
    save_entities([e for e in entities if e is not match])
    return match


def find(mac_slug: str, slug: str) -> dict[str, Any] | None:
    """Look an entity up by the slugified MAC that arrives in an MQTT topic."""
    from .entities import slugify

    for entity in list_entities():
        if entity["slug"] == slug and slugify(entity["mac"]) == mac_slug:
            return entity
    return None
