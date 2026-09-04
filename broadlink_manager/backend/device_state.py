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

    # get_state() covers the families check_sensors/check_power miss: hvac
    # returns the unit's whole parameter set, while lb1/lb2, bg1, s3 and ehc31
    # return a power state. Without this they showed up with no readings at all.
    get_state = getattr(raw, "get_state", None)
    if callable(get_state) and not state:
        try:
            for key, value in _flatten_state(get_state()).items():
                state[key] = value
        except Exception as exc:  # noqa: BLE001
            return None, f"No se pudo leer el estado: {exc}"

    # Hysen thermostats name it differently.
    full_status = getattr(raw, "get_full_status", None)
    if callable(full_status) and not state:
        try:
            for key, value in _flatten_state(full_status()).items():
                state[key] = value
        except Exception as exc:  # noqa: BLE001
            return None, f"No se pudo leer el termostato: {exc}"

    return (state or None), None


def _flatten_state(raw_state: Any) -> dict[str, Any]:
    """Turn whatever get_state() returned into labelled display values.

    Shapes vary by family: a dict of parameters (hvac, hysen), a bare bool
    (lb1, s3), or an int. Only the keys worth showing are kept -- a thermostat
    returns two dozen fields, most of them scheduling internals nobody reads in
    an inventory list.
    """
    if isinstance(raw_state, bool):
        return {"Estado": "encendido" if raw_state else "apagado"}
    if isinstance(raw_state, int):
        return {"Estado": "encendido" if raw_state else "apagado"}
    if not isinstance(raw_state, dict):
        return {"Estado": str(raw_state)}

    labels = {
        "pwr": ("Estado", None),
        "power": ("Estado", None),
        "state": ("Estado", None),
        "temp": ("Temperatura", "°C"),
        "room_temp": ("Temperatura", "°C"),
        "thermostat_temp": ("Consigna", "°C"),
        "external_temp": ("Temperatura externa", "°C"),
        "target_temp": ("Consigna", "°C"),
        "mode": ("Modo", None),
        "fixation_v": ("Aletas", None),
        "fanspeed": ("Ventilador", None),
        "speed": ("Velocidad", None),
        "brightness": ("Brillo", "%"),
        "colortemp": ("Temperatura de color", "K"),
        "hue": ("Tono", None),
        "saturation": ("Saturación", "%"),
    }

    out: dict[str, Any] = {}
    for key, value in raw_state.items():
        entry = labels.get(str(key).lower())
        if entry is None:
            continue
        label, unit = entry
        if label == "Estado" and isinstance(value, (bool, int)) and not isinstance(value, str):
            out[label] = "encendido" if value else "apagado"
        else:
            out[label] = f"{value} {unit}".strip() if unit else value
    return out
