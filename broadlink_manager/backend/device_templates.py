from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("broadlink_manager.device_templates")

# Templates are JSON files rather than code, so adding one is a file and a pull
# request instead of a change to the backend.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Order presented in the UI; anything not listed follows, alphabetically.
PREFERRED_ORDER = ["tv", "aire", "porton", "ventilador", "luces"]


def _load_one(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error("No se pudo leer la plantilla %s: %s", path.name, exc)
        return None

    if not isinstance(data, dict):
        logger.error("La plantilla %s no es un objeto JSON.", path.name)
        return None

    buttons = data.get("buttons")
    if not isinstance(buttons, list) or not buttons:
        logger.error("La plantilla %s no tiene botones.", path.name)
        return None

    cleaned = []
    seen: set[str] = set()
    for button in buttons:
        if not isinstance(button, dict):
            continue
        command = str(button.get("command", "")).strip()
        # A duplicate command would silently overwrite the earlier capture, so
        # the second one is dropped instead.
        if not command or command in seen:
            logger.warning("Botón inválido o repetido en %s: %r", path.name, button)
            continue
        seen.add(command)
        cleaned.append(
            {
                "command": command,
                "label": str(button.get("label") or command),
                "hint": button.get("hint"),
                "optional": bool(button.get("optional", False)),
            }
        )

    if not cleaned:
        return None

    return {
        "id": str(data.get("id") or path.stem),
        "name": str(data.get("name") or path.stem),
        "icon": data.get("icon", ""),
        "description": data.get("description", ""),
        "note": data.get("note"),
        "buttons": cleaned,
    }


def list_templates() -> list[dict[str, Any]]:
    """Every valid template on disk, in the preferred order.

    A broken file is skipped with a log line rather than breaking the list: one
    bad template must not take the whole feature down.
    """
    if not TEMPLATES_DIR.is_dir():
        logger.warning("No existe el directorio de plantillas %s", TEMPLATES_DIR)
        return []

    templates = [t for path in sorted(TEMPLATES_DIR.glob("*.json")) if (t := _load_one(path))]

    def sort_key(template: dict[str, Any]) -> tuple[int, str]:
        try:
            return (PREFERRED_ORDER.index(template["id"]), "")
        except ValueError:
            return (len(PREFERRED_ORDER), template["name"].lower())

    templates.sort(key=sort_key)
    return templates


def get_template(template_id: str) -> dict[str, Any] | None:
    return next((t for t in list_templates() if t["id"] == template_id), None)
