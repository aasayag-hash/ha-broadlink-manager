"""Tests for reading state off the non-RM families.

Read-only on purpose: Home Assistant already owns these devices as native
switch/sensor/climate/cover entities, and two processes taking turns on the same
device socket is a known source of dropped connections. This is inventory, so
the user can confirm the app sees the device and that it is alive.
"""

from __future__ import annotations

import broadlink
import pytest

from backend import device_state
from backend.models import capabilities_for

# Every reader python-broadlink 0.19 exposes across the supported families.
READER_METHODS = (
    "check_sensors",
    "check_power",
    "get_energy",
    "get_state",
    "get_full_status",
    # Curtain motors: dooya/dooya2 and wser name the same thing differently.
    "get_percentage",
    "get_position",
)


def test_declared_state_matches_what_the_library_exposes():
    """A family must claim read_state only if something can actually be read.

    Six families (hvac, lb1, lb2, bg1, s3, ehc31) expose get_state() and were
    being skipped, so they showed up in the inventory with no readings at all.
    """
    mismatched = []
    for cls in broadlink.SUPPORTED_TYPES:
        declared = capabilities_for(cls.__name__).read_state
        available = any(hasattr(cls, m) for m in READER_METHODS)
        if declared != available:
            mismatched.append((cls.__name__, declared, available))

    assert not mismatched, f"capacidades desalineadas: {mismatched}"


def test_every_family_is_classified():
    """No family may fall through without either learning or an explanation."""
    unexplained = []
    for cls in broadlink.SUPPORTED_TYPES:
        caps = capabilities_for(cls.__name__)
        if not caps.learn_ir and not caps.no_learn_reason:
            unexplained.append(cls.__name__)
    assert not unexplained, f"sin motivo para el usuario: {unexplained}"


# --- shapes get_state() comes back in ---------------------------------------


class Fake:
    def __init__(self, **methods):
        for name, value in methods.items():
            setattr(self, name, value)


def test_sensor_readings_are_labelled():
    device = Fake(check_sensors=lambda: {"temperature": 24.3, "humidity": 47})
    state, error = device_state.read_state(device)
    assert error is None
    assert state == {"Temperatura": "24.3 °C", "Humedad": "47 %"}


def test_qualitative_sensors_read_as_words():
    """A raw 0-3 means nothing to a user."""
    device = Fake(check_sensors=lambda: {"light": 2, "air_quality": 0, "noise": 3})
    state, _ = device_state.read_state(device)
    assert state == {"Luz": "normal", "Aire": "excelente", "Ruido": "mucho"}


def test_plug_power_state():
    device = Fake(check_power=lambda: True)
    state, _ = device_state.read_state(device)
    assert state == {"Estado": "encendido"}


def test_power_strip_reports_each_outlet():
    device = Fake(check_power=lambda: {1: True, 2: False})
    state, _ = device_state.read_state(device)
    assert state == {"Toma 1": "encendida", "Toma 2": "apagada"}


def test_get_state_bool_is_a_power_state():
    """lb1, s3, ehc31 and bg1 return a bare bool."""
    device = Fake(get_state=lambda: True)
    state, _ = device_state.read_state(device)
    assert state == {"Estado": "encendido"}


def test_get_state_dict_keeps_only_useful_keys():
    """hvac returns the unit's whole parameter set; most of it is internals."""
    device = Fake(
        get_state=lambda: {
            "power": 1,
            "target_temp": 24,
            "mode": "cooling",
            "fanspeed": 2,
            "sleep": False,  # not a labelled key
            "ifeel": 0,  # not a labelled key
        }
    )
    state, _ = device_state.read_state(device)
    assert state == {
        "Estado": "encendido",
        "Consigna": "24 °C",
        "Modo": "cooling",
        "Ventilador": 2,
    }


def test_thermostat_uses_get_full_status():
    """Hysen names its reader differently from every other family."""
    device = Fake(get_full_status=lambda: {"power": 1, "room_temp": 21.5, "thermostat_temp": 23})
    state, _ = device_state.read_state(device)
    assert state["Temperatura"] == "21.5 °C"
    assert state["Consigna"] == "23 °C"


def test_a_device_with_no_reader_returns_nothing_rather_than_erroring():
    """S1C alarm kits and A2 sensors expose no reader at all."""
    state, error = device_state.read_state(Fake())
    assert state is None
    assert error is None


def test_curtain_position_is_read():
    """dooya/dooya2 use get_percentage; wser calls the same thing get_position.

    Both were being missed, so curtain motors showed up with no readings at
    all -- which reads as an unresponsive device rather than a working one.
    """
    state, error = device_state.read_state(Fake(get_percentage=lambda: 60))
    assert state == {"Posición": "60 % abierta"}
    assert error is None

    state, _ = device_state.read_state(Fake(get_position=lambda: 0))
    assert state == {"Posición": "0 % abierta"}


def test_a_failing_curtain_read_is_reported():
    def boom():
        raise OSError("sin respuesta")

    state, error = device_state.read_state(Fake(get_percentage=boom))
    assert state is None
    assert "posición" in error


def test_a_failing_read_is_reported_not_raised():
    """One unresponsive device must not stall the rest of the table."""

    def boom():
        raise OSError("sin respuesta")

    state, error = device_state.read_state(Fake(check_sensors=boom))
    assert state is None
    assert "sensores" in error


def test_energy_failure_does_not_lose_the_rest():
    """Several models advertise get_energy and then refuse it."""

    def boom():
        raise OSError("no soportado")

    device = Fake(check_power=lambda: True, get_energy=boom)
    state, error = device_state.read_state(device)
    assert state == {"Estado": "encendido"}
    assert error is None


@pytest.mark.parametrize("family", ["dooya", "wser"])
def test_curtain_motors_explain_themselves(family):
    """No codes to learn, but their position is readable -- say both."""
    caps = capabilities_for(family)
    assert caps.read_state is True
    assert "cortina" in caps.no_learn_reason
    assert "cover" in caps.no_learn_reason
