"""Tests for the template progress endpoint.

Its answer decides whether the wizard shows a button as already learned, and a
wrong "not learned" costs the user a re-capture that overwrites the existing
code without asking.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

MAC = "34:ea:34:bb:14:27"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BROADLINK_MANAGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BROADLINK_MANAGER_DATA_DIR", str(tmp_path / "data"))

    import backend.storage as storage_module

    importlib.reload(storage_module)
    import backend.main as main_module

    importlib.reload(main_module)

    # Nothing in these tests needs discovery, MQTT or the background workers.
    monkeypatch.setattr(main_module, "startup", lambda: None)
    return TestClient(main_module.app), storage_module


def seed(storage, data):
    path = storage.codes_path(MAC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "key": path.name, "data": data}), encoding="utf-8")


def test_progress_for_a_group_lists_the_learned_buttons(client):
    api, storage = client
    seed(storage, {"TV Living": {"power": "a", "mute": "b", "otro": "c"}})

    body = api.get(f"/api/templates/tv/{MAC}", params={"subdevice": "TV Living"}).json()

    # Only commands that belong to the template, and only those already stored.
    assert body["learned"] == ["power", "mute"]
    assert body["groups"] == ["TV Living"]


def test_progress_is_omitted_when_no_group_is_given(client):
    """An empty list would assert "nothing learned", which is a wrong answer.

    A caller believing it would have the user re-capture existing codes, and
    save_code overwrites them silently.
    """
    api, storage = client
    seed(storage, {"TV Living": {"power": "a"}})

    body = api.get(f"/api/templates/tv/{MAC}").json()

    assert "learned" not in body
    assert body["groups"] == ["TV Living"]


def test_an_unknown_group_reports_nothing_learned(client):
    """Distinct from the case above: here the question was asked and answered."""
    api, storage = client
    seed(storage, {"TV Living": {"power": "a"}})

    body = api.get(f"/api/templates/tv/{MAC}", params={"subdevice": "Comedor"}).json()
    assert body["learned"] == []


def test_a_device_with_no_codes_yet(client):
    api, _ = client
    body = api.get(f"/api/templates/tv/{MAC}", params={"subdevice": "Nuevo"}).json()
    assert body["learned"] == []
    assert body["groups"] == []


def test_an_unknown_template_is_a_404(client):
    api, _ = client
    assert api.get(f"/api/templates/no-existe/{MAC}").status_code == 404


def test_an_invalid_mac_is_rejected(client):
    api, _ = client
    response = api.get("/api/templates/tv/no-es-una-mac")
    assert response.status_code == 400
    assert "MAC" in response.json()["detail"]


def test_a_corrupt_storage_file_is_reported_not_crashed(client):
    api, storage = client
    path = storage.codes_path(MAC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{roto", encoding="utf-8")

    response = api.get(f"/api/templates/tv/{MAC}", params={"subdevice": "TV"})
    assert response.status_code == 400


def test_the_template_list_is_served(client):
    api, _ = client
    body = api.get("/api/templates").json()
    assert {t["id"] for t in body} >= {"tv", "aire", "porton"}
