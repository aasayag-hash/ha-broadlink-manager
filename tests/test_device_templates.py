"""Tests for the device templates that drive the guided-learning wizard.

Templates are JSON files, so adding one is a file rather than a code change --
which also means a malformed file must be skipped rather than take the whole
feature down.
"""

from __future__ import annotations

import importlib
import json

import pytest

from backend import device_templates


@pytest.fixture()
def custom_dir(tmp_path, monkeypatch):
    """Point the loader at a throwaway directory."""
    monkeypatch.setattr(device_templates, "TEMPLATES_DIR", tmp_path)
    return tmp_path


def write(directory, name, data):
    (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


# --- the shipped templates -------------------------------------------------


def test_shipped_templates_all_load():
    importlib.reload(device_templates)
    templates = device_templates.list_templates()
    ids = {t["id"] for t in templates}
    assert {"tv", "aire", "porton", "ventilador", "luces"} <= ids


def test_shipped_templates_are_well_formed():
    for template in device_templates.list_templates():
        assert template["name"]
        assert template["buttons"], template["id"]
        # Every template needs at least one required button, or the wizard has
        # no next step to point at.
        assert any(not b["optional"] for b in template["buttons"]), template["id"]
        commands = [b["command"] for b in template["buttons"]]
        assert len(commands) == len(set(commands)), template["id"]


def test_command_names_are_storage_safe():
    """They become keys in .storage and arguments to remote.send_command."""
    for template in device_templates.list_templates():
        for button in template["buttons"]:
            command = button["command"]
            assert command == command.strip()
            assert "/" not in command
            assert command.islower() or command.replace("_", "").isalnum()


def test_the_ac_template_warns_about_full_state_codes():
    """An air conditioner sends its whole state, not "one degree up".

    Someone learning "temp up" and expecting it to work repeatedly is the most
    common misunderstanding, so the template has to say so.
    """
    template = device_templates.get_template("aire")
    assert template["note"]
    assert "estado completo" in template["note"]


def test_the_gate_template_explains_the_single_button():
    template = device_templates.get_template("porton")
    assert "pulso" == template["buttons"][0]["command"]
    assert template["note"]


def test_preferred_order_is_respected():
    ids = [t["id"] for t in device_templates.list_templates()]
    assert ids[:5] == device_templates.PREFERRED_ORDER


def test_get_template_by_id():
    assert device_templates.get_template("tv")["name"] == "Televisor"
    assert device_templates.get_template("no-existe") is None


# --- loading and validation ------------------------------------------------


def test_a_broken_file_is_skipped_not_fatal(custom_dir):
    """One bad template must not hide the good ones."""
    write(custom_dir, "bueno", {"id": "bueno", "name": "Bueno", "buttons": [{"command": "a"}]})
    (custom_dir / "roto.json").write_text("{no json", encoding="utf-8")

    templates = device_templates.list_templates()
    assert [t["id"] for t in templates] == ["bueno"]


def test_a_template_with_no_buttons_is_skipped(custom_dir):
    write(custom_dir, "vacio", {"id": "vacio", "name": "Vacío", "buttons": []})
    assert device_templates.list_templates() == []


def test_duplicate_commands_are_dropped(custom_dir):
    """The second would silently overwrite the first capture."""
    write(
        custom_dir,
        "dup",
        {
            "id": "dup",
            "name": "Dup",
            "buttons": [{"command": "power"}, {"command": "power"}, {"command": "mute"}],
        },
    )
    commands = [b["command"] for b in device_templates.list_templates()[0]["buttons"]]
    assert commands == ["power", "mute"]


def test_a_button_with_no_command_is_dropped(custom_dir):
    write(
        custom_dir,
        "parcial",
        {"id": "parcial", "name": "Parcial", "buttons": [{"label": "sin comando"}, {"command": "ok"}]},
    )
    assert [b["command"] for b in device_templates.list_templates()[0]["buttons"]] == ["ok"]


def test_a_missing_label_falls_back_to_the_command(custom_dir):
    write(custom_dir, "x", {"id": "x", "name": "X", "buttons": [{"command": "power"}]})
    assert device_templates.list_templates()[0]["buttons"][0]["label"] == "power"


def test_the_id_falls_back_to_the_filename(custom_dir):
    write(custom_dir, "sin_id", {"name": "Sin id", "buttons": [{"command": "a"}]})
    assert device_templates.list_templates()[0]["id"] == "sin_id"


def test_unlisted_templates_sort_alphabetically_after_the_preferred_ones(custom_dir):
    for name in ("zebra", "alfa"):
        write(custom_dir, name, {"id": name, "name": name.title(), "buttons": [{"command": "a"}]})
    write(custom_dir, "tv", {"id": "tv", "name": "Televisor", "buttons": [{"command": "power"}]})

    ids = [t["id"] for t in device_templates.list_templates()]
    assert ids == ["tv", "alfa", "zebra"]


def test_a_missing_directory_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(device_templates, "TEMPLATES_DIR", tmp_path / "no-existe")
    assert device_templates.list_templates() == []
