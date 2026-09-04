"""Tests for storage.py, the module that writes into Home Assistant's .storage.

This is the riskiest code in the add-on: a bad write destroys codes the user
spent real time learning, in a file Home Assistant tells people never to edit by
hand. Every guard that protects that file is pinned down here.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

MAC = "34:ea:34:bb:14:27"
MAC_HEX = "34ea34bb1427"
FILENAME = f"broadlink_remote_{MAC_HEX}_codes"


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import storage.py pointed at a throwaway config dir."""
    monkeypatch.setenv("BROADLINK_MANAGER_CONFIG_DIR", str(tmp_path))
    import backend.storage as storage_module

    importlib.reload(storage_module)
    return storage_module


def write_file(storage, data: dict, version: int = 1) -> Path:
    path = storage.codes_path(MAC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version, "key": FILENAME, "data": data}), encoding="utf-8"
    )
    return path


def read_file(storage) -> dict:
    return json.loads(storage.codes_path(MAC).read_text(encoding="utf-8"))


# --- file naming -----------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    ["34:ea:34:bb:14:27", "34EA34BB1427", "34-ea-34-bb-14-27", "34ea34bb1427", "34ea.34bb.1427"],
)
def test_mac_normalizes_to_ha_filename(storage, given):
    """Any MAC spelling must land on the file Home Assistant actually reads.

    HA's config flow uses device.mac.hex(): lowercase, no separators. Writing to
    a differently named file would appear to work and be read by nobody.
    """
    assert storage.codes_path(given).name == FILENAME


@pytest.mark.parametrize("bad", ["", "not-a-mac", "34ea34bb14", "34ea34bb1427ff", "zz:ea:34:bb:14:27"])
def test_invalid_mac_is_rejected(storage, bad):
    with pytest.raises(storage.StorageError):
        storage.codes_path(bad)


# --- reading ---------------------------------------------------------------


def test_missing_file_reads_as_empty(storage):
    assert storage.read_codes(MAC) == {}


def test_reads_nested_structure(storage):
    write_file(storage, {"TV": {"power": "JgBQAAAB"}, "Aire": {"on": "JgCUAAAB"}})
    assert storage.read_codes(MAC) == {"TV": {"power": "JgBQAAAB"}, "Aire": {"on": "JgCUAAAB"}}


def test_alternative_toggle_codes_survive_a_roundtrip(storage):
    """Commands learned with 'alternative' hold a two-item list, not a string.

    Coercing that to a string would break the toggle behaviour HA implements by
    alternating between the two codes on each send.
    """
    write_file(storage, {"TV": {"power": ["JgBQAAAB", "JgBQAAAC"]}})
    storage.save_code(MAC, "TV", "mute", "JgBQAAAD")
    assert read_file(storage)["data"]["TV"]["power"] == ["JgBQAAAB", "JgBQAAAC"]


def test_unknown_version_refuses_to_read(storage):
    """A future layout must stop everything rather than be overwritten as v1."""
    write_file(storage, {"TV": {"power": "JgBQAAAB"}}, version=2)
    with pytest.raises(storage.StorageError, match="versión 2"):
        storage.read_codes(MAC)


def test_unknown_version_is_never_written_over(storage):
    path = write_file(storage, {"TV": {"power": "JgBQAAAB"}}, version=2)
    before = path.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.save_code(MAC, "TV", "mute", "JgBQAAAD")
    assert path.read_bytes() == before


def test_corrupt_json_is_left_untouched(storage):
    path = storage.codes_path(MAC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(storage.StorageError):
        storage.read_codes(MAC)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_malformed_group_is_skipped_not_fatal(storage):
    """One bad entry must not hide every valid code in the file."""
    write_file(storage, {"TV": {"power": "JgBQAAAB"}, "roto": "no soy un dict"})
    assert storage.read_codes(MAC) == {"TV": {"power": "JgBQAAAB"}}


# --- writing ---------------------------------------------------------------


def test_save_creates_file_with_ha_layout(storage):
    storage.save_code(MAC, "TV", "power", "JgBQAAAB")
    payload = read_file(storage)
    assert payload["version"] == 1
    assert payload["key"] == FILENAME
    assert payload["data"] == {"TV": {"power": "JgBQAAAB"}}


def test_save_merges_instead_of_replacing(storage):
    write_file(storage, {"TV": {"power": "JgBQAAAB"}, "Aire": {"on": "JgCUAAAB"}})
    storage.save_code(MAC, "TV", "vol_up", "JgBQAAAC")
    assert read_file(storage)["data"] == {
        "TV": {"power": "JgBQAAAB", "vol_up": "JgBQAAAC"},
        "Aire": {"on": "JgCUAAAB"},
    }


def test_write_uses_a_fresh_read_not_a_stale_one(storage):
    """HA defers its own save by 15s, so the file changes between requests.

    The caller may hold a codes dict read seconds ago; write_codes must ignore
    it and re-read, or a command HA saved in the meantime is silently dropped.
    """
    write_file(storage, {"TV": {"power": "JgBQAAAB"}})

    # What a request read at its start.
    stale = storage.read_codes(MAC)

    # HA finishes its deferred save afterwards.
    current = json.loads(storage.codes_path(MAC).read_text(encoding="utf-8"))
    current["data"]["TV"]["learned_by_ha"] = "JgBQAAAZ"
    storage.codes_path(MAC).write_text(json.dumps(current), encoding="utf-8")

    def mutate(codes):
        # codes must be the fresh read, not `stale`.
        assert "learned_by_ha" in codes["TV"]
        codes["TV"]["ours"] = "JgBQAAAY"

    storage.write_codes(MAC, mutate)

    data = read_file(storage)["data"]["TV"]
    assert data["learned_by_ha"] == "JgBQAAAZ"  # HA's code survived
    assert data["ours"] == "JgBQAAAY"
    assert "learned_by_ha" not in stale["TV"]  # the stale snapshot really was behind


def test_concurrent_writers_do_not_lose_each_other(storage):
    """Two threads saving at once must both land: the lock serialises them."""
    import threading

    write_file(storage, {"TV": {}})
    barrier = threading.Barrier(4)

    def save(n: int) -> None:
        barrier.wait()
        storage.save_code(MAC, "TV", f"cmd{n}", f"code{n}")

    threads = [threading.Thread(target=save, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert set(read_file(storage)["data"]["TV"]) == {"cmd0", "cmd1", "cmd2", "cmd3"}


def test_backup_is_written_before_modifying(storage, tmp_path):
    write_file(storage, {"TV": {"power": "original"}})
    storage.save_code(MAC, "TV", "power", "reemplazado")

    backups = list((tmp_path / "broadlink_manager" / "backups").glob(f"{FILENAME}.*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["data"]["TV"]["power"] == "original"


def test_no_backup_for_a_file_that_did_not_exist(storage, tmp_path):
    storage.save_code(MAC, "TV", "power", "JgBQAAAB")
    backup_dir = tmp_path / "broadlink_manager" / "backups"
    assert not backup_dir.exists() or not list(backup_dir.glob("*.json"))


def test_config_dir_is_read_per_call_not_at_import(tmp_path, monkeypatch):
    """Paths must follow the env var even if it changes after import.

    Capturing CONFIG_DIR at import time sent backups to /config while the codes
    went to the configured dir -- the codes looked fine and the safety net was
    silently missing.
    """
    import backend.storage as storage_module

    monkeypatch.setenv("BROADLINK_MANAGER_CONFIG_DIR", str(tmp_path / "later"))
    assert str(tmp_path / "later") in str(storage_module.codes_path(MAC))
    assert str(tmp_path / "later") in str(storage_module._backup_dir())


def test_rapid_edits_each_get_their_own_backup(storage, tmp_path):
    """Editing twice within the same second must leave two recovery points.

    A second-resolution stamp made consecutive edits overwrite each other, so a
    rename followed by a move left only one backup for both changes.
    """
    write_file(storage, {"TV": {"power": "a", "mute": "b"}})
    storage.rename_code(MAC, "TV", "power", "encender")
    storage.move_code(MAC, "TV", "encender", "Living")
    storage.delete_code(MAC, "TV", "mute")

    backups = list((tmp_path / "broadlink_manager" / "backups").glob("*.json"))
    assert len(backups) == 3


def test_backups_are_pruned(storage, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_BACKUPS", 3)
    write_file(storage, {"TV": {"power": "v0"}})
    for i in range(6):
        storage.save_code(MAC, "TV", "power", f"v{i + 1}")

    backups = list((tmp_path / "broadlink_manager" / "backups").glob("*.json"))
    assert len(backups) == 3


def test_no_temp_file_is_left_behind(storage, tmp_path):
    storage.save_code(MAC, "TV", "power", "JgBQAAAB")
    leftovers = [p for p in (tmp_path / ".storage").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_write_is_atomic_on_failure(storage, monkeypatch):
    """A crash mid-write must leave the previous file intact, not truncated."""
    path = write_file(storage, {"TV": {"power": "original"}})
    original = path.read_bytes()

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disco lleno")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(storage.StorageError):
        storage.save_code(MAC, "TV", "power", "nuevo")

    monkeypatch.setattr(storage.os, "replace", real_replace)
    assert path.read_bytes() == original


def test_empty_names_are_rejected(storage):
    with pytest.raises(storage.StorageError):
        storage.save_code(MAC, "  ", "power", "JgBQAAAB")
    with pytest.raises(storage.StorageError):
        storage.save_code(MAC, "TV", "  ", "JgBQAAAB")


def test_names_are_trimmed(storage):
    storage.save_code(MAC, "  TV  ", "  power  ", "JgBQAAAB")
    assert read_file(storage)["data"] == {"TV": {"power": "JgBQAAAB"}}


# --- editing ---------------------------------------------------------------


def test_delete_removes_only_that_command(storage):
    write_file(storage, {"TV": {"power": "a", "mute": "b"}})
    storage.delete_code(MAC, "TV", "mute")
    assert read_file(storage)["data"] == {"TV": {"power": "a"}}


def test_deleting_last_command_drops_the_group(storage):
    """An empty group would linger in the UI and in send_command's autocomplete."""
    write_file(storage, {"TV": {"power": "a"}, "Aire": {"on": "b"}})
    storage.delete_code(MAC, "TV", "power")
    assert read_file(storage)["data"] == {"Aire": {"on": "b"}}


def test_delete_missing_command_errors(storage):
    write_file(storage, {"TV": {"power": "a"}})
    with pytest.raises(storage.StorageError):
        storage.delete_code(MAC, "TV", "nope")


def test_rename_keeps_the_code(storage):
    write_file(storage, {"TV": {"power": "a"}})
    storage.rename_code(MAC, "TV", "power", "encender")
    assert read_file(storage)["data"] == {"TV": {"encender": "a"}}


def test_rename_onto_existing_name_errors(storage):
    write_file(storage, {"TV": {"power": "a", "mute": "b"}})
    with pytest.raises(storage.StorageError):
        storage.rename_code(MAC, "TV", "power", "mute")


def test_move_between_groups(storage):
    write_file(storage, {"TV": {"power": "a", "mute": "b"}, "Aire": {}})
    storage.move_code(MAC, "TV", "power", "Aire")
    data = read_file(storage)["data"]
    assert data["Aire"]["power"] == "a"
    assert "power" not in data["TV"]


def test_move_last_command_drops_the_source_group(storage):
    write_file(storage, {"TV": {"power": "a"}})
    storage.move_code(MAC, "TV", "power", "Living")
    assert read_file(storage)["data"] == {"Living": {"power": "a"}}


def test_move_creates_the_target_group(storage):
    write_file(storage, {"TV": {"power": "a", "mute": "b"}})
    storage.move_code(MAC, "TV", "power", "Nuevo")
    assert read_file(storage)["data"]["Nuevo"] == {"power": "a"}


def test_rename_group(storage):
    write_file(storage, {"TV": {"power": "a"}})
    storage.rename_subdevice(MAC, "TV", "TV Living")
    assert read_file(storage)["data"] == {"TV Living": {"power": "a"}}


def test_rename_group_onto_existing_errors(storage):
    write_file(storage, {"TV": {"power": "a"}, "Aire": {"on": "b"}})
    with pytest.raises(storage.StorageError):
        storage.rename_subdevice(MAC, "TV", "Aire")


def test_delete_group(storage):
    write_file(storage, {"TV": {"power": "a"}, "Aire": {"on": "b"}})
    storage.delete_subdevice(MAC, "TV")
    assert read_file(storage)["data"] == {"Aire": {"on": "b"}}
