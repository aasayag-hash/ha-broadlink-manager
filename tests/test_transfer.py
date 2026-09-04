"""Tests for exporting and importing code sets.

The export doubles as a backup the user can keep, so its format has to be stable
and its validation strict: a bad code written into .storage breaks
remote.send_command later, far from where the mistake was made.
"""

from __future__ import annotations

import importlib
import json

import pytest

MAC = "34:ea:34:bb:14:27"


@pytest.fixture()
def modules(tmp_path, monkeypatch):
    monkeypatch.setenv("BROADLINK_MANAGER_CONFIG_DIR", str(tmp_path))
    import backend.storage as storage_module

    importlib.reload(storage_module)
    import backend.transfer as transfer_module

    importlib.reload(transfer_module)
    return transfer_module, storage_module


def seed(storage, data: dict) -> None:
    path = storage.codes_path(MAC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "key": path.name, "data": data}), encoding="utf-8"
    )


def stored(storage) -> dict:
    return json.loads(storage.codes_path(MAC).read_text(encoding="utf-8"))["data"]


def export_of(data: dict) -> dict:
    return {
        "format": "broadlink_manager_codes",
        "version": 1,
        "devices": data,
    }


# --- export ----------------------------------------------------------------


def test_export_is_self_describing(modules):
    """The format tag and version are what let an import refuse the wrong file."""
    transfer, storage = modules
    seed(storage, {"TV": {"power": "JgBQAAAB"}})

    payload = transfer.export_codes(MAC)
    assert payload["format"] == "broadlink_manager_codes"
    assert payload["version"] == 1
    assert payload["devices"] == {"TV": {"power": "JgBQAAAB"}}
    assert payload["source_mac"] == MAC


def test_export_can_be_limited_to_some_equipment(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "a"}, "Aire": {"on": "b"}, "Portón": {"abrir": "c"}})

    payload = transfer.export_codes(MAC, ["TV", "Portón"])
    assert sorted(payload["devices"]) == ["Portón", "TV"]


def test_exporting_something_absent_errors(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "a"}})
    with pytest.raises(transfer.TransferError, match="No existe el equipo"):
        transfer.export_codes(MAC, ["Inexistente"])


def test_toggle_commands_survive_a_round_trip(modules):
    """A toggle is a two-item list; flattening it would break the alternation."""
    transfer, storage = modules
    seed(storage, {"TV": {"power": ["a", "b"]}})

    payload = transfer.export_codes(MAC)
    seed(storage, {})
    transfer.import_codes(MAC, payload)

    assert stored(storage)["TV"]["power"] == ["a", "b"]


def test_export_of_an_empty_device_is_still_valid(modules):
    transfer, storage = modules
    assert transfer.export_codes(MAC)["devices"] == {}


# --- validation ------------------------------------------------------------


def test_a_foreign_file_is_refused(modules):
    transfer, _ = modules
    with pytest.raises(transfer.TransferError, match="no parece un export"):
        transfer.import_codes(MAC, {"some": "other json"})


def test_an_unknown_version_is_refused(modules):
    """Better to refuse than to guess at a layout from the future."""
    transfer, _ = modules
    payload = export_of({"TV": {"power": "a"}})
    payload["version"] = 99
    with pytest.raises(transfer.TransferError, match="versión 99"):
        transfer.import_codes(MAC, payload)


def test_a_file_with_no_devices_is_refused(modules):
    transfer, _ = modules
    with pytest.raises(transfer.TransferError, match="ningún equipo"):
        transfer.import_codes(MAC, export_of({}))


@pytest.mark.parametrize(
    "bad",
    [
        {"TV": {"power": 42}},
        {"TV": {"power": None}},
        {"TV": {"power": ""}},
        {"TV": {"power": ["solo-uno"]}},
        {"TV": {"power": ["a", "b", "c"]}},
        {"TV": {"power": [1, 2]}},
    ],
)
def test_invalid_codes_are_refused(modules, bad):
    """Writing one of these would only fail later, at remote.send_command."""
    transfer, _ = modules
    with pytest.raises(transfer.TransferError, match="no es válido"):
        transfer.import_codes(MAC, export_of(bad))


def test_nothing_is_written_when_validation_fails(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "original"}})
    with pytest.raises(transfer.TransferError):
        transfer.import_codes(MAC, export_of({"TV": {"power": 42}}))
    assert stored(storage) == {"TV": {"power": "original"}}


# --- preview ---------------------------------------------------------------


def test_preview_separates_new_from_conflicting(modules):
    """Overwriting an existing code is invisible without this."""
    transfer, storage = modules
    seed(storage, {"TV": {"power": "vieja"}})

    result = transfer.preview_import(MAC, export_of({"TV": {"power": "nueva", "mute": "m"}}))
    group = result["groups"][0]
    assert group["new"] == ["mute"]
    assert group["conflicting"] == ["power"]
    assert result["total"] == 2
    assert result["conflicts"] == 1


def test_preview_writes_nothing(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "original"}})
    transfer.preview_import(MAC, export_of({"TV": {"power": "nueva"}}))
    assert stored(storage) == {"TV": {"power": "original"}}


# --- import modes ----------------------------------------------------------


def test_import_merges_into_what_is_already_there(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "a"}, "Aire": {"on": "b"}})

    transfer.import_codes(MAC, export_of({"TV": {"mute": "m"}, "Nuevo": {"x": "x"}}))

    assert stored(storage) == {
        "TV": {"power": "a", "mute": "m"},
        "Aire": {"on": "b"},
        "Nuevo": {"x": "x"},
    }


def test_skip_mode_leaves_existing_codes_alone(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "original"}})

    result = transfer.import_codes(MAC, export_of({"TV": {"power": "nueva"}}), "skip")

    assert stored(storage)["TV"]["power"] == "original"
    assert result["skipped"] == ["TV / power"]


def test_overwrite_mode_replaces(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "original"}})

    result = transfer.import_codes(MAC, export_of({"TV": {"power": "nueva"}}), "overwrite")

    assert stored(storage)["TV"]["power"] == "nueva"
    assert result["overwritten"] == ["TV / power"]


def test_rename_mode_keeps_both(modules):
    """The existing code may be the one wired into an automation."""
    transfer, storage = modules
    seed(storage, {"TV": {"power": "original"}})

    result = transfer.import_codes(MAC, export_of({"TV": {"power": "nueva"}}), "rename")

    group = stored(storage)["TV"]
    assert group["power"] == "original"
    assert group["power_2"] == "nueva"
    assert result["renamed"] == ["TV / power -> power_2"]


def test_rename_mode_finds_the_next_free_suffix(modules):
    transfer, storage = modules
    seed(storage, {"TV": {"power": "a", "power_2": "b", "power_3": "c"}})

    transfer.import_codes(MAC, export_of({"TV": {"power": "d"}}), "rename")
    assert stored(storage)["TV"]["power_4"] == "d"


def test_skipping_everything_leaves_no_empty_group(modules):
    """An empty group would linger in the table and in send_command."""
    transfer, storage = modules
    seed(storage, {})

    transfer.import_codes(MAC, export_of({"Vacio": {}}), "skip")
    assert "Vacio" not in stored(storage)


def test_an_unknown_mode_is_refused(modules):
    transfer, _ = modules
    with pytest.raises(transfer.TransferError, match="Modo desconocido"):
        transfer.import_codes(MAC, export_of({"TV": {"power": "a"}}), "borrar_todo")


def test_import_takes_one_backup_not_one_per_code(modules, tmp_path):
    """A per-code write would leave a half-imported set behind on failure."""
    transfer, storage = modules
    seed(storage, {"TV": {"power": "a"}})

    transfer.import_codes(MAC, export_of({"TV": {"b": "b", "c": "c", "d": "d"}}))

    backups = list((tmp_path / "broadlink_manager" / "backups").glob("*.json"))
    assert len(backups) == 1


def test_a_full_round_trip_between_devices(modules):
    """Codes are not tied to the Broadlink that captured them."""
    transfer, storage = modules
    seed(storage, {"Portón": {"abrir": "sgAyAHFw", "cerrar": ["a", "b"]}})

    payload = transfer.export_codes(MAC)
    seed(storage, {})
    transfer.import_codes(MAC, json.loads(json.dumps(payload)))

    assert stored(storage) == {"Portón": {"abrir": "sgAyAHFw", "cerrar": ["a", "b"]}}
