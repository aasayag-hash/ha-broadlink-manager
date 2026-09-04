from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import broadlink.exceptions as blk_exc

logger = logging.getLogger("broadlink_manager.learning")

# How long to wait for the user to press a button before giving up. Generous:
# people walk to the other room to fetch the remote.
IR_TIMEOUT = 30.0
SWEEP_TIMEOUT = 30.0
PACKET_TIMEOUT = 30.0

POLL_INTERVAL = 0.5

# States the UI renders. Kept as plain strings so they can be sent as JSON and
# compared in the frontend without a shared enum.
IDLE = "idle"
WAITING_IR = "waiting_ir"
SWEEPING = "sweeping"
WAITING_PACKET = "waiting_packet"
CAPTURED = "captured"
FAILED = "failed"
CANCELLED = "cancelled"


# First byte of a Broadlink packet says how the code is transmitted. Reported
# because it is easy to get wrong: an RF remote pointed at an RM pro is captured
# by the IR flow too, and the resulting code looks identical in the table.
PACKET_KINDS = {0x26: "IR", 0xB2: "RF 433", 0xD7: "RF 315"}


def packet_kind(code_b64: str) -> str | None:
    try:
        return PACKET_KINDS.get(base64.b64decode(code_b64)[0])
    except (ValueError, IndexError, TypeError):
        return None


@dataclass
class Session:
    """One capture in progress, polled by the UI.

    A single session per device: the Broadlink has one learning mode, so
    starting a second capture while one is running would fight over it.
    """

    mac: str
    mode: str  # "ir" or "rf"
    state: str = IDLE
    message: str = ""
    error: str | None = None
    code: str | None = None
    frequency: float | None = None
    started_at: float = field(default_factory=time.time)
    deadline: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        remaining = max(0, int(self.deadline - time.time())) if self.deadline else 0
        return {
            "mac": self.mac,
            "mode": self.mode,
            "state": self.state,
            "message": self.message,
            "error": self.error,
            "code": self.code,
            "frequency": self.frequency,
            "kind": packet_kind(self.code) if self.code else None,
            "remaining": remaining,
        }


_sessions: dict[str, Session] = {}
_cancels: dict[str, threading.Event] = {}
_lock = threading.Lock()


def get_session(mac: str) -> Session | None:
    with _lock:
        return _sessions.get(mac)


def clear(mac: str) -> None:
    """Drop a finished session, so the learn tab starts clean for the next code."""
    with _lock:
        _sessions.pop(mac, None)
        _cancels.pop(mac, None)


def cancel(mac: str) -> bool:
    with _lock:
        event = _cancels.get(mac)
    if event is None:
        return False
    event.set()
    return True


def _is_no_data_yet(exc: BaseException) -> bool:
    """True while the device simply has nothing captured yet.

    Verified against an RM pro: polling check_data() before any button is
    pressed raises ReadError (-10) every time. This is the normal reply on every
    poll until the user presses something, so treating it as a failure aborts
    the capture within a second of starting it.

    StorageError (-5) is included because some firmwares answer "storage is
    full" for the same condition.
    """
    return isinstance(exc, (blk_exc.ReadError, blk_exc.StorageError))


def _poll_for_data(raw: Any, deadline: float, cancel_event: threading.Event) -> bytes | None:
    """Poll check_data() until a code arrives, time runs out, or the user cancels."""
    while time.time() < deadline:
        if cancel_event.is_set():
            return None
        try:
            data = raw.check_data()
            if data:
                return data
        except blk_exc.BroadlinkException as exc:
            if not _is_no_data_yet(exc):
                raise
        time.sleep(POLL_INTERVAL)
    return None


def _learn_ir(session: Session, raw: Any, cancel_event: threading.Event) -> None:
    """Single step: enter learning mode and wait for one press."""
    session.state = WAITING_IR
    session.message = "Apuntá el control al Broadlink y apretá el botón que querés aprender."
    session.deadline = time.time() + IR_TIMEOUT

    raw.enter_learning()
    data = _poll_for_data(raw, session.deadline, cancel_event)

    if cancel_event.is_set():
        session.state = CANCELLED
        session.message = "Captura cancelada."
        return
    if data is None:
        session.state = FAILED
        session.error = (
            "No se recibió ninguna señal. Verificá que el control tenga pilas, que apunte al "
            "Broadlink y que estés a menos de un metro, y probá de nuevo."
        )
        return

    session.code = base64.b64encode(data).decode("utf8")
    session.state = CAPTURED
    session.message = "Código capturado. Probalo antes de guardarlo."


def _learn_rf(session: Session, raw: Any, cancel_event: threading.Event, frequency: float | None) -> None:
    """Two phases: find the frequency, then capture the packet on it.

    When frequency is given the sweep is skipped entirely -- that is the
    difference between ~30 seconds and ~3 per button once the first one of a
    remote has been learned.

    Known hardware quirk: some units branded "RM pro" report locked=True from
    check_frequency() within a few seconds even with no transmitter anywhere
    near, always with freq=0.0, and then never capture anything in phase two.
    Confirmed on real hardware by sweeping with nothing pressed. The sequence
    here matches the official broadlink_cli exactly, so a device that behaves
    this way is not something the client code can work around -- phase two just
    times out with its usual message.
    """
    try:
        if frequency is None:
            session.state = SWEEPING
            session.message = (
                "Mantené apretado el botón del control apuntando al Broadlink, "
                "hasta que se detecte la frecuencia."
            )
            session.deadline = time.time() + SWEEP_TIMEOUT

            raw.sweep_frequency()
            found = False
            while time.time() < session.deadline:
                if cancel_event.is_set():
                    session.state = CANCELLED
                    session.message = "Captura cancelada."
                    return
                is_found, freq = raw.check_frequency()
                if is_found:
                    found = True
                    # Measured on a real RM pro: it reports found=True with
                    # freq=0.0 for the entire sweep -- this firmware simply does
                    # not disclose the value. That is fine for capturing, since
                    # the device keeps the frequency internally and
                    # find_rf_packet() with no argument reuses it. It only means
                    # there is nothing to remember for the next button.
                    session.frequency = round(freq, 3) if freq > 0 else None
                    break
                time.sleep(POLL_INTERVAL)

            if not found:
                session.state = FAILED
                session.error = (
                    "No se detectó la frecuencia. Mantené el botón apretado durante todo el "
                    "barrido, acercá el control al Broadlink y probá de nuevo."
                )
                return
        else:
            if not frequency or frequency <= 0:
                session.state = FAILED
                session.error = (
                    "La frecuencia guardada no es válida. Hacé el barrido completo una vez más."
                )
                return
            session.frequency = frequency

        # Phase two. Deliberately a separate step the user can retry without
        # sweeping again: failing here and losing the frequency is the single
        # most common complaint in the forum threads about RF learning.
        session.state = WAITING_PACKET
        session.message = (
            f"Frecuencia detectada: {session.frequency} MHz. "
            if session.frequency
            else "Frecuencia detectada. "
        ) + "Soltá el botón y apretalo una sola vez, corto."
        session.deadline = time.time() + PACKET_TIMEOUT

        # No argument when the device did not disclose the frequency: it keeps
        # the one it just found internally. Passing 0.0 would leave the radio
        # listening on no band at all.
        if session.frequency:
            raw.find_rf_packet(session.frequency)
        else:
            raw.find_rf_packet()
        data = _poll_for_data(raw, session.deadline, cancel_event)

        if cancel_event.is_set():
            session.state = CANCELLED
            session.message = "Captura cancelada."
            return
        if data is None:
            session.state = FAILED
            session.error = (
                "Se encontró la frecuencia pero no se capturó el código. Probá de nuevo con "
                "una pulsación corta: la frecuencia ya está detectada, no hace falta repetir "
                "el barrido. Si falla varias veces seguidas, puede que el control no sea de "
                "433 ni 315 MHz, o que este Broadlink detecte de más en el barrido; probá "
                "aprenderlo en modo IR, que también captura algunos controles de radio."
            )
            return

        session.code = base64.b64encode(data).decode("utf8")
        session.state = CAPTURED
        session.message = (
            f"Código capturado en {session.frequency} MHz. Probalo antes de guardarlo."
            if session.frequency
            else "Código capturado. Probalo antes de guardarlo."
        )
    finally:
        # Leaving the device in sweep mode wedges it until it is power cycled,
        # so this must run on every exit path, including cancel and timeout.
        try:
            raw.cancel_sweep_frequency()
        except Exception as exc:  # noqa: BLE001 - best effort cleanup
            logger.warning("No se pudo cancelar el barrido en %s: %s", session.mac, exc)


def start(mac: str, raw: Any, mode: str, frequency: float | None = None) -> Session:
    """Begin a capture in the background and return the session immediately."""
    with _lock:
        existing = _sessions.get(mac)
        if existing and existing.state in (WAITING_IR, SWEEPING, WAITING_PACKET):
            raise RuntimeError(
                "Ya hay una captura en curso para este dispositivo. Esperá a que termine o cancelala."
            )
        session = Session(mac=mac, mode=mode)
        cancel_event = threading.Event()
        _sessions[mac] = session
        _cancels[mac] = cancel_event

    def run() -> None:
        try:
            if mode == "rf":
                _learn_rf(session, raw, cancel_event, frequency)
            else:
                _learn_ir(session, raw, cancel_event)
        except blk_exc.BroadlinkException as exc:
            session.state = FAILED
            session.error = (
                f"El dispositivo rechazó la operación ({exc}). Suele pasar cuando Home Assistant "
                "está usando el Broadlink al mismo tiempo; probá de nuevo en unos segundos."
            )
        except OSError as exc:
            session.state = FAILED
            session.error = f"No se pudo contactar al dispositivo: {exc}"
        except Exception as exc:  # noqa: BLE001 - a capture thread must never die silently
            logger.exception("Falló la captura en %s", mac)
            session.state = FAILED
            session.error = f"Error inesperado durante la captura: {exc}"

    threading.Thread(target=run, name=f"learn-{mac}", daemon=True).start()
    return session
