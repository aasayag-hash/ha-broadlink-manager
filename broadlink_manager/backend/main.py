from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import device_state, discovery, ha_api, storage

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
        entities = ha_api.list_remote_entities()
        if not entities:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No se encontró ninguna entidad remote.* en Home Assistant. Configurá la "
                    "integración Broadlink en Ajustes → Dispositivos y servicios para poder "
                    "probar códigos desde acá."
                ),
            )
        entity_id = entities[0]

    ok, error = ha_api.send_command(entity_id, payload.subdevice, payload.command)
    if not ok:
        raise HTTPException(status_code=502, detail=error or "No se pudo enviar el código")
    return {"ok": True}


@app.get("/api/remote-entities")
def remote_entities() -> list[str]:
    return ha_api.list_remote_entities()


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
