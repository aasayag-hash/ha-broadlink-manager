"""Tests for forcing which model a device is treated as.

python-broadlink maps a numeric devtype to a class; a devtype missing from that
table comes back as the base Device, which can neither learn nor send. Clones
and newer hardware revisions land there, and picking the equivalent model by
hand is the only way to use them.
"""

from __future__ import annotations

import importlib

import broadlink
import pytest

# A real devtype from the library's own table: RM4 pro.
RM4PRO_DEVTYPE = next(
    dt
    for cls, products in broadlink.SUPPORTED_TYPES.items()
    if cls.__name__ == "rm4pro"
    for dt in products
)
MAC = "aa:bb:cc:dd:ee:ff"


@pytest.fixture()
def disc(tmp_path, monkeypatch):
    monkeypatch.setenv("BROADLINK_MANAGER_DATA_DIR", str(tmp_path))
    import backend.devices_store as store_module

    importlib.reload(store_module)
    import backend.discovery as discovery_module

    importlib.reload(discovery_module)
    # No network in tests: auth() would time out on a device that isn't there.
    monkeypatch.setattr(discovery_module, "_authenticate", lambda device, raw: None)
    return discovery_module


def seed_generic(disc):
    """Register a device the library could not identify."""
    from backend.models import Device, capabilities_for

    device = Device(
        mac=MAC,
        host="192.168.1.50",
        port=80,
        devtype=0x9999,  # not in SUPPORTED_TYPES
        model="Desconocido",
        manufacturer="Broadlink",
        device_class="Device",
        capabilities=capabilities_for("Device"),
    )
    disc._devices[MAC] = device
    return device


# --- the model list --------------------------------------------------------


def test_model_list_covers_the_whole_library(disc):
    total = sum(len(p) for p in broadlink.SUPPORTED_TYPES.values())
    assert len(disc.known_models()) == total


def test_model_list_says_what_each_one_can_do(disc):
    """The picker is useless without knowing which entry restores RF."""
    by_class = {m["device_class"]: m for m in disc.known_models()}
    assert by_class["rm4pro"]["learn_rf"] is True
    assert by_class["rmmini"]["learn_rf"] is False
    assert by_class["rmmini"]["learn_ir"] is True
    assert by_class["sp4"]["learn_ir"] is False


def test_model_list_is_sorted_for_a_human(disc):
    models = disc.known_models()
    keys = [(m["manufacturer"], m["model"]) for m in models]
    assert keys == sorted(keys)


# --- unrecognised devices --------------------------------------------------


def test_an_unknown_devtype_is_flagged_as_generic(disc):
    """This is the case the override exists for."""
    device = seed_generic(disc)
    assert disc.is_generic(device) is True


def test_a_recognised_device_is_not_generic(disc):
    from backend.models import Device, capabilities_for

    device = Device(
        mac=MAC,
        host="h",
        devtype=RM4PRO_DEVTYPE,
        model="RM4 pro",
        manufacturer="Broadlink",
        device_class="rm4pro",
        capabilities=capabilities_for("rm4pro"),
    )
    assert disc.is_generic(device) is False


# --- applying an override --------------------------------------------------


def test_forcing_a_model_grants_its_capabilities(disc):
    """An unusable generic device becomes a working one."""
    seed_generic(disc)

    device, error = disc.set_model(MAC, RM4PRO_DEVTYPE)

    assert error is None
    assert device.device_class == "rm4pro"
    assert device.capabilities.learn_rf is True
    assert device.forced_devtype == RM4PRO_DEVTYPE


def test_an_unknown_devtype_is_refused(disc):
    seed_generic(disc)
    device, error = disc.set_model(MAC, 0x1234)
    assert device is None
    assert "no lo conoce la librería" in error


def test_forcing_a_model_on_a_missing_device_errors(disc):
    device, error = disc.set_model("00:00:00:00:00:00", RM4PRO_DEVTYPE)
    assert device is None
    assert error == "Dispositivo no encontrado"


def test_the_override_is_persisted(disc, tmp_path):
    """It has to survive a restart, or an unrecognised device is unusable again."""
    import backend.devices_store as store

    seed_generic(disc)
    disc.set_model(MAC, RM4PRO_DEVTYPE)

    saved = store.list_devices()
    assert saved[0]["forced_devtype"] == RM4PRO_DEVTYPE

    disc._devices.clear()
    disc.load_persisted()
    assert disc.list_known()[0].forced_devtype == RM4PRO_DEVTYPE


def test_a_manual_device_stays_manual_after_an_override(disc):
    device = seed_generic(disc)
    device.manual = True

    updated, _ = disc.set_model(MAC, RM4PRO_DEVTYPE)
    assert updated.manual is True


# --- clearing an override --------------------------------------------------


def test_clearing_restores_detection(disc):
    seed_generic(disc)
    disc.set_model(MAC, RM4PRO_DEVTYPE)

    assert disc.clear_model(MAC) is True
    assert disc.list_known()[0].forced_devtype is None


def test_clearing_something_not_forced_returns_false(disc):
    seed_generic(disc)
    assert disc.clear_model(MAC) is False


# --- surviving a rescan ----------------------------------------------------


class FakeRaw:
    def __init__(self, devtype, mac=MAC):
        self.host = ("192.168.1.50", 80)
        self.mac = mac
        self.devtype = devtype
        self.model = "Desconocido"
        self.manufacturer = "Broadlink"

    def auth(self):
        pass


def test_a_rescan_reapplies_the_forced_model(disc):
    """Without this, every scan would silently undo the user's choice.

    For an unrecognised device that means losing the ability to use it at all
    until they set it again -- and the next background rescan is 120s away.
    """
    seed_generic(disc)
    disc.set_model(MAC, RM4PRO_DEVTYPE)

    # The device answers discovery with its real, unknown devtype again.
    rebuilt = disc._apply_forced_model(FakeRaw(0x9999), MAC)
    assert type(rebuilt).__name__ == "rm4pro"


def test_no_forced_model_leaves_discovery_alone(disc):
    seed_generic(disc)
    raw = FakeRaw(0x9999)
    assert disc._apply_forced_model(raw, MAC) is raw


def test_remembering_a_device_keeps_its_override(disc):
    """_remember rebuilds the Device object, so the flag must be carried over."""
    seed_generic(disc)
    disc.set_model(MAC, RM4PRO_DEVTYPE)

    from backend.models import Device, capabilities_for

    fresh = Device(
        mac=MAC,
        host="192.168.1.50",
        devtype=0x9999,
        model="Desconocido",
        manufacturer="Broadlink",
        device_class="Device",
        capabilities=capabilities_for("Device"),
    )
    disc._remember(fresh, FakeRaw(0x9999))

    assert disc._devices[MAC].forced_devtype == RM4PRO_DEVTYPE
