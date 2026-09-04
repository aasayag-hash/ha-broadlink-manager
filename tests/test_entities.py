"""Tests for MQTT discovery payloads and the entity mapping store.

The payloads matter because a wrong field means an entity that appears in Home
Assistant but never works, which is far harder to debug than an error here.
"""

from __future__ import annotations

import json

import pytest

from backend import entities


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BROADLINK_MANAGER_DATA_DIR", str(tmp_path))
    import backend.entities_store as store_module

    return store_module


class FakePublisher:
    """Captures publishes instead of talking to a broker."""

    def __init__(self):
        self.published: list[tuple[str, str, bool]] = []
        self.status = "connected"
        self.last_error = None

    def publish(self, topic, payload, retain=True):
        self.published.append((topic, payload, retain))


@pytest.fixture()
def fake_mqtt(monkeypatch):
    fake = FakePublisher()
    monkeypatch.setattr(entities, "publisher", fake)
    return fake


# --- slugs -----------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("Luz del patio", "luz_del_patio"),
        ("TV Living", "tv_living"),
        ("Portón", "port_n"),
        ("  espacios  ", "espacios"),
        ("A/B: C", "a_b_c"),
        ("", "sin_nombre"),
        ("!!!", "sin_nombre"),
    ],
)
def test_slugify(given, expected):
    assert entities.slugify(given) == expected


def test_mac_slug_is_usable_in_a_topic():
    """Colons are legal in MQTT topics but confusing; slugs keep them out."""
    assert entities.slugify("d1:24:da:0d:43:b4") == "d1_24_da_0d_43_b4"


# --- discovery payloads ----------------------------------------------------


def test_button_payload_has_what_ha_requires(fake_mqtt):
    entities.publish_button("d1:24:da:0d:43:b4", "RM pro", "Abrir portón", "abrir_porton")

    topic, payload, retain = fake_mqtt.published[0]
    assert topic == "homeassistant/button/d1_24_da_0d_43_b4_abrir_porton/config"
    # Retained, so the entity survives a restart of HA or of this add-on.
    assert retain is True

    data = json.loads(payload)
    assert data["name"] == "Abrir portón"
    assert data["command_topic"] == "broadlink_manager/d1_24_da_0d_43_b4/abrir_porton/set"
    assert data["unique_id"] == "broadlink_manager_d1_24_da_0d_43_b4_abrir_porton"
    assert data["device"]["identifiers"] == ["broadlink_manager_d1_24_da_0d_43_b4"]


def test_switch_payload_is_optimistic(fake_mqtt):
    """A one-way remote never reports back.

    Without optimistic the switch sits unavailable in HA forever, since nothing
    ever publishes a real state for it.
    """
    entities.publish_switch("d1:24:da:0d:43:b4", "RM pro", "Luz patio", "luz_patio")

    data = json.loads(fake_mqtt.published[0][1])
    assert data["optimistic"] is True
    assert data["payload_on"] == "ON"
    assert data["payload_off"] == "OFF"
    assert data["state_topic"] == "broadlink_manager/d1_24_da_0d_43_b4/luz_patio/state"


def test_entities_of_one_broadlink_share_a_device(fake_mqtt):
    entities.publish_button("aa:bb:cc:dd:ee:ff", "RM4 pro", "Uno", "uno")
    entities.publish_switch("aa:bb:cc:dd:ee:ff", "RM4 pro", "Dos", "dos")

    devices = [json.loads(p[1])["device"]["identifiers"] for p in fake_mqtt.published]
    assert devices[0] == devices[1]


def test_removal_publishes_an_empty_retained_payload(fake_mqtt):
    """That is how HA deletes a discovered entity; anything else leaves it behind."""
    entities.remove_entity("switch", "d1:24:da:0d:43:b4", "luz_patio")

    topic, payload, retain = fake_mqtt.published[0]
    assert topic == "homeassistant/switch/d1_24_da_0d_43_b4_luz_patio/config"
    assert payload == ""
    assert retain is True


def test_publish_without_a_connection_raises_rather_than_silently_failing():
    """Publishing into a dead connection otherwise looks like it worked."""
    publisher = entities.MqttPublisher()
    with pytest.raises(entities.MqttError, match="Mosquitto"):
        publisher.publish("x/y", "payload")


# --- broker settings -------------------------------------------------------


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BROADLINK_MANAGER_DATA_DIR", str(tmp_path))
    import backend.settings_store as settings_module

    return settings_module


def test_no_manual_settings_falls_back_to_the_supervisor(settings, monkeypatch):
    monkeypatch.setattr(
        entities, "supervisor_broker", lambda: ({"host": "core-mosquitto", "port": 1883}, None)
    )
    config, error = entities.broker_config()
    assert config["host"] == "core-mosquitto"
    assert error is None


def test_manual_settings_win_over_detection(settings, monkeypatch):
    """A broker on another machine, or a non-default port, has to be reachable.

    Home Assistant Container has no Supervisor at all, so detection can never
    work there and the manual path is the only one.
    """
    monkeypatch.setattr(
        entities, "supervisor_broker", lambda: ({"host": "core-mosquitto", "port": 1883}, None)
    )
    settings.set_mqtt({"host": "192.168.1.2", "port": 8883, "username": "mqtt"})

    config, error = entities.broker_config()
    assert config["host"] == "192.168.1.2"
    assert config["port"] == 8883
    assert error is None


def test_clearing_manual_settings_restores_detection(settings, monkeypatch):
    monkeypatch.setattr(entities, "supervisor_broker", lambda: ({"host": "auto", "port": 1883}, None))
    settings.set_mqtt({"host": "192.168.1.2", "port": 1883})
    settings.set_mqtt(None)
    assert entities.broker_config()[0]["host"] == "auto"


def test_manual_settings_without_a_host_are_ignored(settings, monkeypatch):
    """An empty host must not shadow detection with an unusable config."""
    monkeypatch.setattr(entities, "supervisor_broker", lambda: ({"host": "auto", "port": 1883}, None))
    settings.set_mqtt({"host": "", "port": 1883})
    assert entities.broker_config()[0]["host"] == "auto"


def test_settings_survive_a_corrupt_file(settings, tmp_path):
    (tmp_path / "settings.json").write_text("{roto", encoding="utf-8")
    assert settings.get_mqtt() is None


def test_detection_error_is_passed_through(settings, monkeypatch):
    monkeypatch.setattr(entities, "supervisor_broker", lambda: (None, "sin broker"))
    config, error = entities.broker_config()
    assert config is None
    assert error == "sin broker"


# --- mapping store ---------------------------------------------------------


def test_add_and_list(store):
    store.add({"mac": "aa", "slug": "luz", "kind": "button", "name": "Luz", "commands": {}})
    assert [e["slug"] for e in store.list_entities()] == ["luz"]


def test_list_filters_by_device(store):
    store.add({"mac": "aa", "slug": "uno", "kind": "button", "name": "Uno", "commands": {}})
    store.add({"mac": "bb", "slug": "dos", "kind": "button", "name": "Dos", "commands": {}})
    assert [e["slug"] for e in store.list_entities("bb")] == ["dos"]


def test_adding_the_same_slug_replaces_it(store):
    """Republishing is how an edit works; two rows would orphan one of them."""
    store.add({"mac": "aa", "slug": "luz", "kind": "button", "name": "Vieja", "commands": {}})
    store.add({"mac": "aa", "slug": "luz", "kind": "switch", "name": "Nueva", "commands": {}})

    rows = store.list_entities()
    assert len(rows) == 1
    assert rows[0]["name"] == "Nueva"


def test_same_slug_on_different_devices_coexists(store):
    store.add({"mac": "aa", "slug": "luz", "kind": "button", "name": "A", "commands": {}})
    store.add({"mac": "bb", "slug": "luz", "kind": "button", "name": "B", "commands": {}})
    assert len(store.list_entities()) == 2


def test_remove_returns_the_row_so_the_caller_can_unpublish(store):
    store.add({"mac": "aa", "slug": "luz", "kind": "switch", "name": "Luz", "commands": {}})
    removed = store.remove("aa", "luz")
    assert removed["kind"] == "switch"  # needed to build the right config topic
    assert store.list_entities() == []


def test_removing_something_absent_returns_none(store):
    assert store.remove("aa", "no-existe") is None


def test_find_resolves_the_slugified_mac_from_a_topic(store):
    """MQTT topics carry the slug, so the lookup has to translate back."""
    store.add(
        {"mac": "d1:24:da:0d:43:b4", "slug": "luz", "kind": "button", "name": "Luz", "commands": {}}
    )
    assert store.find("d1_24_da_0d_43_b4", "luz")["name"] == "Luz"
    assert store.find("otra_mac", "luz") is None


def test_store_survives_a_corrupt_file(store, tmp_path):
    (tmp_path / "entities.json").write_text("{roto", encoding="utf-8")
    assert store.list_entities() == []


def test_no_temp_file_is_left_behind(store, tmp_path):
    store.add({"mac": "aa", "slug": "luz", "kind": "button", "name": "Luz", "commands": {}})
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
