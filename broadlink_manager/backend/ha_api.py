from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("broadlink_manager.ha_api")

SUPERVISOR_API_BASE = "http://supervisor/core/api"


def _headers() -> dict[str, str]:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def call_service(
    domain: str, service: str, data: dict[str, Any] | None = None, timeout: float = 30.0
) -> tuple[bool, str | None]:
    """Call a Home Assistant service. Returns (ok, error_message).

    Sending codes goes through HA rather than straight to the hardware, so Home
    Assistant stays the only thing holding the Broadlink session. Two processes
    taking turns on the same device socket is a known source of dropped
    connections.
    """
    url = f"{SUPERVISOR_API_BASE}/services/{domain}/{service}"
    try:
        response = httpx.post(url, json=data or {}, headers=_headers(), timeout=timeout)
        response.raise_for_status()
        return True, None
    except httpx.HTTPStatusError as exc:
        detail = f"HTTP {exc.response.status_code} de Home Assistant"
        if exc.response.status_code == 401:
            detail += " (token del Supervisor inválido)"
        elif exc.response.status_code == 400:
            # HA puts the useful part in the body: usually an unknown device or
            # command name, which is exactly what the user needs to see.
            body = exc.response.text.strip()
            if body:
                detail += f": {body[:300]}"
        logger.warning("Falló %s.%s: %s", domain, service, exc)
        return False, detail
    except httpx.HTTPError as exc:
        logger.warning("Falló %s.%s: %s", domain, service, exc)
        return False, f"No se pudo contactar a Home Assistant: {exc}"


def send_command(entity_id: str, device: str, command: str) -> tuple[bool, str | None]:
    """Fire one learned code through HA's remote.send_command."""
    return call_service(
        "remote",
        "send_command",
        {"entity_id": entity_id, "device": device, "command": command},
    )


def list_remote_entities() -> list[str]:
    """Return the remote.* entity ids Home Assistant currently knows about.

    Needed to translate "this Broadlink" into the entity remote.send_command
    expects. An empty list means the Broadlink integration is not set up, which
    the UI reports rather than failing on the first send.
    """
    try:
        response = httpx.get(f"{SUPERVISOR_API_BASE}/states", headers=_headers(), timeout=10.0)
        response.raise_for_status()
        states = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("No se pudo listar entidades: %s", exc)
        return []

    return [
        state["entity_id"]
        for state in states
        if isinstance(state, dict) and str(state.get("entity_id", "")).startswith("remote.")
    ]
