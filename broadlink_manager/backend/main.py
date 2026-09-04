from __future__ import annotations

import base64
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    device_state,
    discovery,
    entities,
    entities_store,
    ha_api,
    learning,
    settings_store,
    storage,
    transfer,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("broadlink_manager.main")

# Home Assistant's ingress proxy (Supervisor) strips the "/api/hassio_ingress/<token>"
# prefix before forwarding the request to the add-on container, so the app is written
# to serve everything relative to "/" and does not need to know the ingress prefix.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

STATE_POLL_SECONDS = 30
RESCAN_SECONDS = 120

app = FastAPI(title="Broadlink Manager")

_shutdown = threading.Event()


class AddDeviceIn(BaseModel):
    ip: str


class RenameCodeIn(BaseModel):
    subdevice: str
    command: str
    new_command: str


class MoveCodeIn(BaseModel):
    subdevice: str
    command: str
    new_subdevice: str


class RenameGroupIn(BaseModel):
    subdevice: str
    new_subdevice: str


class SendCodeIn(BaseModel):
    subdevice: str
    command: str
    entity_id: str | None = None


class LearnIn(BaseModel):
    mode: str  # "ir" or "rf"
    # Skips the RF sweep when the frequency of this remote is already known.
    frequency: float | None = None


class SaveLearnedIn(BaseModel):
    subdevice: str
    command: str


class MqttSettingsIn(BaseModel):
    # Empty host means "go back to detecting it from Home Assistant".
    host: str = ""
    port: int = 1883
    username: str | None = None
    password: str | None = None
    ssl: bool = False


class ImportIn(BaseModel):
    payload: dict[str, Any]
    # skip / overwrite / rename, for codes whose name is already taken.
    mode: str = transfer.MODE_SKIP


class CreateEntityIn(BaseModel):
    kind: str  # "button" or "switch"
    name: str
    subdevice: str
    # A button fires one command; a switch needs the ON/OFF pair.
    command: str | None = None
    command_on: str | None = None
    command_off: str | None = None


@app.get("/api/devices")
def list_devices() -> list[dict[str, Any]]:
    return [d.model_dump() for d in discovery.list_known()]


@app.post("/api/devices/scan")
def scan_devices() -> list[dict[str, Any]]:
    return [d.model_dump() for d in discovery.scan()]


@app.post("/api/devices")
def add_device(payload: AddDeviceIn) -> dict[str, Any]:
    device, error = discovery.add_by_ip(payload.ip)
    if error:
        raise HTTPException(status_code=400, detail=error)
    assert device is not None
    return device.model_dump()


@app.delete("/api/devices/{mac}")
def delete_device(mac: str) -> dict[str, bool]:
    if not discovery.forget(mac):
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
    return {"ok": True}


@app.get("/api/codes/{mac}")
def list_codes(mac: str) -> dict[str, Any]:
    """Return the codes for one Broadlink, grouped by equipment.

    Reads the same file Home Assistant writes, so codes learned earlier through
    Developer Tools show up here too.
    """
    try:
        codes = storage.read_codes(mac)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    groups = [
        {
            "subdevice": subdevice,
            "commands": [
                {
                    "command": command,
                    # A two-item list means the command was learned with the
                    # 'alternative' flag: HA alternates between both on send.
                    "toggle": isinstance(code, list),
                    "kind": learning.packet_kind(str(code[0] if isinstance(code, list) else code)),
                    "preview": (code[0] if isinstance(code, list) else str(code))[:24],
                }
                for command in sorted(commands)
                for code in [commands[command]]
            ],
        }
        for subdevice, commands in sorted(codes.items())
    ]
    total = sum(len(g["commands"]) for g in groups)
    return {"mac": mac, "groups": groups, "total": total}


@app.delete("/api/codes/{mac}/{subdevice}/{command}")
def delete_code(mac: str, subdevice: str, command: str) -> dict[str, bool]:
    try:
        storage.delete_code(mac, subdevice, command)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/codes/{mac}/rename")
def rename_code(mac: str, payload: RenameCodeIn) -> dict[str, bool]:
    try:
        storage.rename_code(mac, payload.subdevice, payload.command, payload.new_command)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/codes/{mac}/move")
def move_code(mac: str, payload: MoveCodeIn) -> dict[str, bool]:
    try:
        storage.move_code(mac, payload.subdevice, payload.command, payload.new_subdevice)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/codes/{mac}/rename-group")
def rename_group(mac: str, payload: RenameGroupIn) -> dict[str, bool]:
    try:
        storage.rename_subdevice(mac, payload.subdevice, payload.new_subdevice)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.delete("/api/codes/{mac}/{subdevice}")
def delete_group(mac: str, subdevice: str) -> dict[str, bool]:
    try:
        storage.delete_subdevice(mac, subdevice)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/codes/{mac}/send")
def send_code(mac: str, payload: SendCodeIn) -> dict[str, bool]:
    """Fire a stored code through Home Assistant.

    Deliberately not sent straight to the hardware: HA owns the Broadlink
    session, and two processes on the same device socket drop connections.
    """
    entity_id = payload.entity_id
    if not entity_id:
        # Not named `entities`: that is the module imported at the top, and
        # shadowing it here would break any later use of it in this handler.
        remotes = ha_api.list_remote_entities()
        if not remotes:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No se encontró ninguna entidad remote.* en Home Assistant. Configurá la "
                    "integración Broadlink en Ajustes → Dispositivos y servicios para poder "
                    "probar códigos desde acá."
                ),
            )
        entity_id = remotes[0]

    ok, error = ha_api.send_command(entity_id, payload.subdevice, payload.command)
    if not ok:
        raise HTTPException(status_code=502, detail=error or "No se pudo enviar el código")
    return {"ok": True}


@app.get("/api/remote-entities")
def remote_entities() -> list[str]:
    return ha_api.list_remote_entities()


def _require_device(mac: str):
    """Return (device, handle) or raise with a message the user can act on."""
    device = next((d for d in discovery.list_known() if d.mac == mac), None)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
    raw = discovery.get_handle(mac)
    if raw is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No hay conexión con el dispositivo. Buscá de nuevo desde la pestaña "
                "Dispositivos para reconectarlo."
            ),
        )
    return device, raw


@app.post("/api/learn/{mac}")
def start_learn(mac: str, payload: LearnIn) -> dict[str, Any]:
    device, raw = _require_device(mac)
    mode = payload.mode.lower()

    if mode not in ("ir", "rf"):
        raise HTTPException(status_code=400, detail="El modo debe ser 'ir' o 'rf'")
    if mode == "ir" and not device.capabilities.learn_ir:
        raise HTTPException(
            status_code=400, detail=device.capabilities.no_learn_reason or "No aprende infrarrojo"
        )
    if mode == "rf" and not device.capabilities.learn_rf:
        raise HTTPException(
            status_code=400,
            detail=device.capabilities.no_learn_reason or "Este modelo no tiene radio",
        )

    try:
        session = learning.start(mac, raw, mode, payload.frequency)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return session.as_dict()


@app.get("/api/learn/{mac}")
def learn_status(mac: str) -> dict[str, Any]:
    session = learning.get_session(mac)
    if session is None:
        return {"mac": mac, "state": learning.IDLE, "message": "", "error": None, "code": None}
    return session.as_dict()


@app.post("/api/learn/{mac}/cancel")
def cancel_learn(mac: str) -> dict[str, bool]:
    return {"ok": learning.cancel(mac)}


@app.post("/api/learn/{mac}/test")
def test_learned(mac: str) -> dict[str, bool]:
    """Fire the just-captured code without saving it first.

    This one goes straight to the hardware, unlike the codes table: the code is
    not in .storage yet, so remote.send_command has nothing to reference.
    """
    _, raw = _require_device(mac)
    session = learning.get_session(mac)
    if session is None or not session.code:
        raise HTTPException(status_code=400, detail="No hay ningún código capturado para probar")

    try:
        raw.send_data(base64.b64decode(session.code))
    except Exception as exc:  # noqa: BLE001 - library raises its own tree
        raise HTTPException(
            status_code=502, detail=f"No se pudo enviar el código: {exc}"
        ) from exc
    return {"ok": True}


@app.post("/api/learn/{mac}/save")
def save_learned(mac: str, payload: SaveLearnedIn) -> dict[str, Any]:
    session = learning.get_session(mac)
    if session is None or not session.code:
        raise HTTPException(status_code=400, detail="No hay ningún código capturado para guardar")

    try:
        storage.save_code(mac, payload.subdevice, payload.command, session.code)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Keep the frequency around: it lets the next button of the same remote skip
    # the sweep, which is the difference between ~30 seconds and ~3.
    frequency = session.frequency
    learning.clear(mac)
    return {"ok": True, "frequency": frequency}


@app.get("/api/export/{mac}")
def export_codes(mac: str, subdevices: str | None = None) -> dict[str, Any]:
    """Export every code, or only the equipment named in a comma-separated list."""
    wanted = [s for s in (subdevices or "").split(",") if s.strip()] or None
    try:
        return transfer.export_codes(mac, wanted)
    except (storage.StorageError, transfer.TransferError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/import/{mac}/preview")
def preview_import(mac: str, payload: ImportIn) -> dict[str, Any]:
    try:
        return transfer.preview_import(mac, payload.payload)
    except (storage.StorageError, transfer.TransferError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/import/{mac}")
def import_codes(mac: str, payload: ImportIn) -> dict[str, Any]:
    try:
        return transfer.import_codes(mac, payload.payload, payload.mode)
    except (storage.StorageError, transfer.TransferError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/mqtt")
def get_mqtt_settings() -> dict[str, Any]:
    """Current broker settings and where they came from.

    The password is never returned: it would be readable by anyone who can open
    the panel, and the form treats an empty field as "keep the stored one".
    """
    manual = settings_store.get_mqtt()
    detected, detect_error = entities.supervisor_broker()

    if manual:
        shown = {k: v for k, v in manual.items() if k != "password"}
        shown["has_password"] = bool(manual.get("password"))
    elif detected:
        shown = {k: v for k, v in detected.items() if k != "password"}
        shown["has_password"] = bool(detected.get("password"))
    else:
        shown = {"host": "", "port": 1883, "username": "", "ssl": False, "has_password": False}

    return {
        "source": "manual" if manual else ("auto" if detected else "none"),
        "settings": shown,
        "detected": (
            {k: v for k, v in detected.items() if k != "password"} if detected else None
        ),
        "detect_error": detect_error,
        "status": entities.publisher.status,
        "error": entities.publisher.last_error,
    }


@app.post("/api/mqtt")
def set_mqtt_settings(payload: MqttSettingsIn) -> dict[str, Any]:
    host = payload.host.strip()

    if not host:
        settings_store.set_mqtt(None)
    else:
        config: dict[str, Any] = {
            "host": host,
            "port": payload.port,
            "ssl": payload.ssl,
        }
        if payload.username:
            config["username"] = payload.username
        # An empty password field keeps whatever was stored, so editing the host
        # does not silently wipe the credentials.
        if payload.password:
            config["password"] = payload.password
        else:
            previous = settings_store.get_mqtt() or {}
            if previous.get("password") and previous.get("username") == payload.username:
                config["password"] = previous["password"]
        settings_store.set_mqtt(config)

    # Reconnect with the new settings and report the outcome straight away,
    # rather than letting the user discover it when an entity fails to appear.
    entities.publisher.stop()
    ok, error = entities.publisher.start(_handle_mqtt_command)
    if not ok:
        raise HTTPException(status_code=400, detail=error or "No se pudo conectar al broker")
    return {"ok": True, "status": entities.publisher.status}


@app.get("/api/entities/{mac}")
def list_entities(mac: str) -> dict[str, Any]:
    return {
        "mqtt": {"status": entities.publisher.status, "error": entities.publisher.last_error},
        "entities": entities_store.list_entities(mac),
    }


@app.post("/api/entities/{mac}")
def create_entity(mac: str, payload: CreateEntityIn) -> dict[str, Any]:
    device = next((d for d in discovery.list_known() if d.mac == mac), None)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")

    kind = payload.kind.lower()
    if kind == "button":
        if not payload.command:
            raise HTTPException(status_code=400, detail="Falta el comando a disparar")
        commands = {"press": payload.command}
    elif kind == "switch":
        if not payload.command_on or not payload.command_off:
            raise HTTPException(
                status_code=400, detail="Un interruptor necesita un comando para encender y otro para apagar"
            )
        commands = {"on": payload.command_on, "off": payload.command_off}
    else:
        raise HTTPException(status_code=400, detail="El tipo debe ser 'button' o 'switch'")

    # Verify the codes exist before creating an entity that would fail on its
    # first press: a broken entity in HA is harder to notice than an error here.
    try:
        stored = storage.read_codes(mac)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    group = stored.get(payload.subdevice, {})
    missing = [c for c in commands.values() if c not in group]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"No existe el comando '{missing[0]}' en '{payload.subdevice}'",
        )

    slug = entities.slugify(name)
    entity = {
        "mac": mac,
        "slug": slug,
        "kind": kind,
        "name": name,
        "subdevice": payload.subdevice,
        "commands": commands,
    }

    try:
        if kind == "button":
            entities.publish_button(mac, device.model, name, slug)
        else:
            entities.publish_switch(mac, device.model, name, slug)
            entities.publish_state(mac, slug, "OFF")
    except entities.MqttError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    entities_store.add(entity)
    return entity


@app.delete("/api/entities/{mac}/{slug}")
def delete_entity(mac: str, slug: str) -> dict[str, bool]:
    entity = entities_store.remove(mac, slug)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entidad no encontrada")
    try:
        entities.remove_entity(entity["kind"], mac, slug)
    except entities.MqttError as exc:
        # The mapping is already gone, so the entity cannot fire any more; say
        # plainly that Home Assistant may still show it until MQTT is back.
        raise HTTPException(
            status_code=503,
            detail=(
                f"Se borró el mapeo pero no se pudo avisar al broker ({exc}). La entidad puede "
                "seguir apareciendo en Home Assistant hasta que MQTT vuelva."
            ),
        ) from exc
    return {"ok": True}


def _handle_mqtt_command(mac_slug: str, entity_slug: str, payload: str) -> None:
    """Fire the code behind an entity when Home Assistant publishes to it.

    Runs on paho's network thread, so every failure is logged rather than
    raised: an exception here would take the MQTT loop down with it.
    """
    entity = entities_store.find(mac_slug, entity_slug)
    if entity is None:
        logger.warning("Comando para una entidad desconocida: %s/%s", mac_slug, entity_slug)
        return

    if entity["kind"] == "button":
        command = entity["commands"]["press"]
    else:
        wanted = payload.strip().upper()
        command = entity["commands"].get("on" if wanted == "ON" else "off")

    if not command:
        logger.warning("Sin comando para %s con payload %r", entity_slug, payload)
        return

    ok, error = ha_api.send_command_auto(entity["subdevice"], command)
    if not ok:
        logger.warning("No se pudo enviar %s/%s: %s", entity["subdevice"], command, error)
        return

    if entity["kind"] == "switch":
        # Optimistic: a one-way remote gives no feedback, so the reported state
        # is simply what was last asked for.
        try:
            entities.publish_state(entity["mac"], entity_slug, payload.strip().upper())
        except entities.MqttError as exc:
            logger.warning("No se pudo publicar el estado de %s: %s", entity_slug, exc)


def _refresh_state() -> None:
    """Poll readable devices one at a time, isolating failures per device.

    A device that stops answering must not stall the others: the loop records
    the error on that row and moves on, so the table stays usable.
    """
    for device in discovery.list_known():
        if _shutdown.is_set():
            return
        if not device.capabilities.read_state or not device.online:
            continue
        raw = discovery.get_handle(device.mac)
        if raw is None:
            continue
        state, error = device_state.read_state(raw)
        device.state = state
        if error:
            device.last_error = error


def _worker(name: str, interval: int, task) -> None:
    """Run task on a fixed interval, quietly, until shutdown.

    Repeated identical failures are logged only a few times: an unreachable
    device would otherwise fill the add-on log with the same line forever and
    bury anything useful.
    """
    failures = 0
    while not _shutdown.wait(interval):
        try:
            task()
            if failures >= 3:
                logger.info("%s: resuelto", name)
            failures = 0
        except Exception as exc:  # noqa: BLE001 - a worker must never die
            failures += 1
            if failures <= 3:
                logger.warning("%s falló: %s", name, exc)


@app.on_event("startup")
def startup() -> None:
    discovery.load_persisted()

    # MQTT is optional: without a broker the add-on still discovers devices,
    # learns codes and manages the table -- only entity creation is unavailable.
    ok, error = entities.publisher.start(_handle_mqtt_command)
    if not ok:
        logger.warning("MQTT no disponible: %s", error)

    # First scan runs in the background: it takes seconds per interface, and
    # blocking startup would leave the ingress panel spinning on a blank page.
    threading.Thread(target=discovery.scan, name="initial-scan", daemon=True).start()
    threading.Thread(
        target=_worker, args=("state-poll", STATE_POLL_SECONDS, _refresh_state), daemon=True
    ).start()
    threading.Thread(
        target=_worker, args=("rescan", RESCAN_SECONDS, discovery.scan), daemon=True
    ).start()


@app.on_event("shutdown")
def shutdown() -> None:
    _shutdown.set()
    entities.publisher.stop()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
