from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

import broadlink

from . import devices_store
from .models import Device, capabilities_for

logger = logging.getLogger("broadlink_manager.discovery")

DISCOVER_TIMEOUT = 5
HELLO_TIMEOUT = 5

# Cache of live device handles, keyed by MAC. Kept because every operation
# (learn, send, read state) needs an authenticated handle, and re-running auth()
# on each request adds a round trip and, on some firmwares, drops the previous
# session.
_handles: dict[str, Any] = {}
_handles_lock = threading.Lock()

# Merged view of what has ever been seen, keyed by MAC. Devices persist here
# across rescans so a device that is briefly unreachable shows as offline
# instead of vanishing from the table mid-use.
_devices: dict[str, Device] = {}
_devices_lock = threading.Lock()


def _mac_bytes(mac: str) -> bytes:
    """Turn aa:bb:cc:dd:ee:ff back into the byte order the library expects.

    gendevice() takes the same reversed bytes discovery hands out, and rejects a
    colon-separated string outright ("non-hexadecimal number found in
    fromhex()").
    """
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "")
    return bytes(reversed(bytes.fromhex(cleaned)))


def _mac_str(mac: bytes | str) -> str:
    """Normalize a MAC to aa:bb:cc:dd:ee:ff.

    python-broadlink hands back bytes in reverse order; the same device must
    key to the same string here and in storage.py, which uses the compact form
    Home Assistant writes into .storage filenames.
    """
    if isinstance(mac, str):
        return mac.lower()
    return ":".join(f"{octet:02x}" for octet in reversed(mac))


def _local_ipv4_addresses() -> list[str]:
    """Every local IPv4 this host has, so discovery can broadcast on each.

    A single broadcast only reaches the subnet of the default route. With the
    Broadlink on a second NIC or a VLAN interface, that scan silently finds
    nothing, which reads as "the device is broken" rather than "wrong network".
    """
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError as exc:
        logger.warning("No se pudieron enumerar las interfaces locales: %s", exc)

    # The default-route address is not always in getaddrinfo() results (it is
    # missing in several container setups), so ask the routing table directly.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    addresses.discard("127.0.0.1")
    return sorted(addresses)


def _hello_with_retries(ip: str, attempts: int = 2) -> Any | None:
    """Try to reach one device directly, tolerating a missed reply.

    A single timeout does not mean the device is gone: an RM pro answers
    irregularly for a while after a learning session, so one retry avoids
    flipping a perfectly healthy device to offline in the table.
    """
    for attempt in range(attempts):
        try:
            return broadlink.hello(ip, timeout=HELLO_TIMEOUT)
        except Exception:  # noqa: BLE001 - unreachable is the normal case here
            if attempt == attempts - 1:
                return None
    return None


def _apply_forced_model(raw: Any, mac: str) -> Any:
    """Rebuild a freshly discovered device as the model the user chose.

    Without this, every scan would re-detect the real devtype and undo the
    override -- which for an unrecognised device means losing the ability to use
    it at all until the user sets it again.
    """
    with _devices_lock:
        existing = _devices.get(mac)
    forced = existing.forced_devtype if existing else None
    if forced is None:
        return raw

    try:
        host, port = raw.host if isinstance(raw.host, tuple) else (raw.host, 80)
        return broadlink.gendevice(forced, (host, port), _mac_bytes(mac))
    except Exception as exc:  # noqa: BLE001 - fall back to what was discovered
        logger.warning("No se pudo reaplicar el modelo forzado en %s: %s", mac, exc)
        return raw


def _to_device(raw: Any, *, manual: bool = False) -> Device:
    device_class = type(raw).__name__
    host, port = raw.host if isinstance(raw.host, tuple) else (raw.host, 80)
    return Device(
        mac=_mac_str(raw.mac),
        host=host,
        port=port,
        devtype=raw.devtype,
        model=getattr(raw, "model", "") or "Desconocido",
        manufacturer=getattr(raw, "manufacturer", "") or "Broadlink",
        device_class=device_class,
        capabilities=capabilities_for(device_class),
        online=True,
        manual=manual,
        last_seen=time.time(),
    )


def _remember(device: Device, raw: Any) -> None:
    with _handles_lock:
        _handles[device.mac] = raw
    with _devices_lock:
        previous = _devices.get(device.mac)
        if previous is not None:
            # A device first added by IP keeps that flag even when a later
            # broadcast happens to find it: the user's manual entry is what
            # guarantees it stays listed if the broadcast stops working.
            device.manual = device.manual or previous.manual
            device.state = previous.state
            if device.forced_devtype is None:
                device.forced_devtype = previous.forced_devtype
        _devices[device.mac] = device


def get_handle(mac: str) -> Any | None:
    with _handles_lock:
        return _handles.get(mac)


def list_known() -> list[Device]:
    with _devices_lock:
        devices = list(_devices.values())
    devices.sort(key=lambda d: (not d.capabilities.learn_ir, d.model, d.host))
    return devices


def scan() -> list[Device]:
    """Broadcast on every local interface and merge the results by MAC."""
    found: dict[str, Any] = {}

    for address in _local_ipv4_addresses() or [None]:
        try:
            results = broadlink.discover(timeout=DISCOVER_TIMEOUT, local_ip_address=address)
        except OSError as exc:
            # One unusable interface must not abort the whole scan: a docker0 or
            # tun address that cannot broadcast is normal on HA hosts.
            logger.warning("Falló el descubrimiento en %s: %s", address or "auto", exc)
            continue
        for raw in results:
            found[_mac_str(raw.mac)] = raw

    for mac, raw in found.items():
        raw = _apply_forced_model(raw, mac)
        device = _to_device(raw)
        _authenticate(device, raw)
        _remember(device, raw)

    # Anything previously known that did not answer the broadcast gets one
    # direct attempt on its last known IP before being called offline. Observed
    # on a real RM pro: after a learning session it stopped answering the
    # broadcast entirely while still replying to hello() on its own address.
    with _devices_lock:
        missing = [d for mac, d in _devices.items() if mac not in found]

    for device in missing:
        raw = _hello_with_retries(device.host)
        if raw is None:
            device.online = False
            continue
        refreshed = _to_device(raw)
        _authenticate(refreshed, raw)
        _remember(refreshed, raw)

    _persist()
    return list_known()


def known_models() -> list[dict[str, Any]]:
    """Every model the library recognises, for the manual override picker.

    Offered because discovery reports a numeric devtype and the library maps it
    to a class: a device whose devtype is not in that table comes back as a
    generic Device with nothing usable. Clones and newer revisions land there,
    and picking the equivalent model by hand makes them work.
    """
    models = []
    for cls, products in broadlink.SUPPORTED_TYPES.items():
        for devtype, (model, manufacturer) in products.items():
            caps = capabilities_for(cls.__name__)
            models.append(
                {
                    "devtype": devtype,
                    "model": model,
                    "manufacturer": manufacturer,
                    "device_class": cls.__name__,
                    "learn_ir": caps.learn_ir,
                    "learn_rf": caps.learn_rf,
                }
            )
    models.sort(key=lambda m: (m["manufacturer"], m["model"]))
    return models


def is_generic(device: Device) -> bool:
    """True when the library could not identify the device.

    gendevice() falls back to the base Device class for an unknown devtype, and
    that class can neither learn nor send anything.
    """
    return device.device_class == "Device"


def set_model(mac: str, devtype: int) -> tuple[Device | None, str | None]:
    """Force a device to be treated as a given model. Returns (device, error).

    For hardware the library does not recognise, or recognises as something less
    capable than it is. The override is persisted, so it survives restarts and
    rescans.
    """
    with _devices_lock:
        existing = _devices.get(mac)
    if existing is None:
        return None, "Dispositivo no encontrado"

    if not any(devtype in products for products in broadlink.SUPPORTED_TYPES.values()):
        return None, f"El código de modelo {devtype} (0x{devtype:04x}) no lo conoce la librería."

    try:
        raw = broadlink.gendevice(devtype, (existing.host, existing.port), _mac_bytes(mac))
    except Exception as exc:  # noqa: BLE001 - library raises its own tree
        return None, f"No se pudo crear el dispositivo con ese modelo: {exc}"

    device = _to_device(raw, manual=existing.manual)
    # Keep the forced type: a later scan would otherwise re-detect the original
    # devtype and quietly undo the user's choice.
    device.forced_devtype = devtype
    _authenticate(device, raw)
    _remember(device, raw)
    _persist()

    if device.last_error:
        return device, device.last_error
    return device, None


def clear_model(mac: str) -> bool:
    """Drop a forced model so the next scan detects the device normally."""
    with _devices_lock:
        device = _devices.get(mac)
        if device is None or device.forced_devtype is None:
            return False
        device.forced_devtype = None
    _persist()
    return True


def add_by_ip(ip: str) -> tuple[Device | None, str | None]:
    """Add a device the broadcast cannot reach. Returns (device, error)."""
    ip = ip.strip()
    if not ip:
        return None, "La IP no puede estar vacía"

    try:
        raw = broadlink.hello(ip, timeout=HELLO_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - the library raises its own exception tree
        # NetworkTimeoutError is broadlink's own class, not OSError, so the
        # timeout case has to be recognised by name rather than by type.
        if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower():
            return None, (
                f"No hubo respuesta de {ip}. Verificá que la IP sea correcta y que el "
                "dispositivo esté encendido y en una red que este servidor pueda alcanzar."
            )
        return None, f"No se pudo contactar a {ip}: {exc}"

    device = _to_device(raw, manual=True)
    _authenticate(device, raw)
    _remember(device, raw)
    _persist()
    return device, None


def forget(mac: str) -> bool:
    with _devices_lock:
        existed = _devices.pop(mac, None) is not None
    with _handles_lock:
        _handles.pop(mac, None)
    if existed:
        _persist()
    return existed


def _authenticate(device: Device, raw: Any) -> None:
    """Authenticate the handle, recording the reason on failure.

    Home Assistant's own Broadlink integration holds a session with the same
    device. When that collides, auth() raises instead of returning, and without
    this the whole scan would fail on one busy device.
    """
    try:
        raw.auth()
        device.last_error = None
    except Exception as exc:  # noqa: BLE001 - library raises its own exception tree
        device.last_error = (
            f"No se pudo autenticar con el dispositivo ({exc}). Suele pasar cuando Home "
            "Assistant ya está usando este Broadlink; probá de nuevo en unos segundos."
        )
        logger.warning("auth() falló para %s (%s): %s", device.mac, device.host, exc)


def _persist() -> None:
    """Save only what discovery cannot rebuild on its own.

    Live fields (online, state, last_error) are deliberately not stored: after a
    restart they would be stale, and showing a remembered "online" for a device
    that has since been unplugged is worse than showing nothing.
    """
    with _devices_lock:
        payload = [
            {
                "mac": d.mac,
                "host": d.host,
                "port": d.port,
                "devtype": d.devtype,
                "model": d.model,
                "manufacturer": d.manufacturer,
                "device_class": d.device_class,
                "manual": d.manual,
                "forced_devtype": d.forced_devtype,
                "last_seen": d.last_seen,
            }
            for d in _devices.values()
        ]
    try:
        devices_store.save_devices(payload)
    except OSError as exc:
        logger.error("No se pudo guardar la lista de dispositivos: %s", exc)


def load_persisted() -> None:
    """Rebuild the device list from disk at startup, all marked offline.

    They light up as the first scan answers. Loading them up front means the
    table is never empty on boot, and manually added devices survive a restart
    even if their IP is momentarily unreachable.
    """
    for entry in devices_store.list_devices():
        try:
            device_class = entry["device_class"]
            device = Device(
                mac=entry["mac"],
                host=entry["host"],
                port=entry.get("port", 80),
                devtype=entry["devtype"],
                model=entry.get("model", "Desconocido"),
                manufacturer=entry.get("manufacturer", "Broadlink"),
                device_class=device_class,
                capabilities=capabilities_for(device_class),
                online=False,
                manual=entry.get("manual", False),
                forced_devtype=entry.get("forced_devtype"),
                last_seen=entry.get("last_seen"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Se ignora una entrada inválida en devices.json: %s", exc)
            continue
        with _devices_lock:
            _devices[device.mac] = device
