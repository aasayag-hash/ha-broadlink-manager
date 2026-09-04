from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import device_state, discovery

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
