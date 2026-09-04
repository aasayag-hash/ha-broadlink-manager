from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("broadlink_manager.device_state")

# Sensor keys python-broadlink returns, mapped to what the UI shows. Anything
# not listed is passed through untouched rather than dropped: a firmware that
# reports an extra field should still surface it.
SENSOR_LABELS = {
    "temperature": ("Temperatura", "°C"),
    "humidity": ("Humedad", "%"),
    "light": ("Luz", ""),
    "air_quality": ("Aire", ""),
    "noise": ("Ruido", ""),
}

LIGHT_LEVELS = {0: "oscuro", 1: "poca", 2: "normal", 3: "mucha"}
AIR_LEVELS = {0: "excelente", 1: "bueno", 2: "normal", 3: "malo"}
NOISE_LEVELS = {0: "silencio", 1: "poco", 2: "normal", 3: "mucho"}


def read_state(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Read whatever a non-RM device reports. Returns (state, error).

    Read-only on purpose: Home Assistant already owns these devices as native
    switch/sensor/climate entities. This is inventory, so the user can confirm
    the app sees the device and that it is alive.
    """
    state: dict[str, Any] = {}

    check_sensors = getattr(raw, "check_sensors", None)
    if callable(check_sensors):
        try:
            for key, value in (check_sensors() or {}).items():
                label, unit = SENSOR_LABELS.get(key, (key.replace("_", " ").capitalize(), ""))
                if key == "light":
                    state[label] = LIGHT_LEVELS.get(value, value)
                elif key == "air_quality":
                    state[label] = AIR_LEVELS.get(value, value)
                elif key == "noise":
                    state[label] = NOISE_LEVELS.get(value, value)
                else:
                    state[label] = f"{value} {unit}".strip()
        except Exception as exc:  # noqa: BLE001 - library raises its own exception tree
            return None, f"No se pudieron leer los sensores: {exc}"

    check_power = getattr(raw, "check_power", None)
    if callable(check_power):
        try:
            power = check_power()
            if isinstance(power, dict):
                # mp1 reports one entry per outlet.
                for outlet, on in power.items():
                    state[f"Toma {outlet}"] = "encendida" if on else "apagada"
            else:
                state["Estado"] = "encendido" if power else "apagado"
        except Exception as exc:  # noqa: BLE001
            return None, f"No se pudo leer el estado: {exc}"

    check_energy = getattr(raw, "get_energy", None)
    if callable(check_energy):
        try:
            state["Consumo"] = f"{check_energy()} W"
        except Exception:  # noqa: BLE001 - many models advertise this and then refuse it
            pass

    return (state or None), None
