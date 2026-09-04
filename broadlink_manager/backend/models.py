from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Families that can learn and send codes, by the class name python-broadlink
# reports. rmpro/rm4pro inherit the RF sweep methods; the mini variants only
# ever do infrared. Anything not listed here (sp*, a1, mp1, hysen, dooya...)
# is inventory: it answers the discovery broadcast but has no codes to learn.
IR_CAPABLE_CLASSES = {"rm", "rmmini", "rmminib", "rmpro", "rm4", "rm4mini", "rm4pro"}
RF_CAPABLE_CLASSES = {"rm", "rmpro", "rm4", "rm4pro"}

# Families that expose readable state. The app only ever reads these: Home
# Assistant already creates the switch/sensor/climate entities for them, and two
# processes taking turns on the same device socket is a known source of
# connection errors.
STATE_READABLE_CLASSES = {
    # check_power / get_energy
    "sp2",
    "sp2s",
    "sp3",
    "sp3s",
    "sp4",
    "sp4b",
    "mp1",
    "mp1s",
    # check_sensors
    "a1",
    "rmpro",
    "rm4mini",
    "rm4pro",
    # get_state: whole parameter sets (hvac) or a power state
    "bg1",
    "hvac",
    "lb1",
    "lb2",
    "s3",
    "ehc31",
    # get_full_status
    "hysen",
}

# Families the library recognises but exposes no reader for. Listed so the UI
# can say so instead of showing an empty row that looks like a failure.
# Verified against python-broadlink 0.19: no check_sensors, check_power,
# get_state or get_full_status on any of these.
NO_STATE_CLASSES = {"dooya", "dooya2", "wser", "s1c", "a2", "sp1", "rmmini", "rmminib"}

# Why a device cannot learn, phrased for the user. Keyed by class name prefix so
# a whole family shares one explanation.
NO_LEARN_REASONS: dict[str, str] = {
    "sp": "Es un enchufe: no tiene códigos para aprender. Home Assistant ya lo controla.",
    "bg": "Es un enchufe con panel: no tiene códigos para aprender. Home Assistant ya lo controla.",
    "mp": "Es una zapatilla: no tiene códigos para aprender. Home Assistant ya la controla.",
    "a1": "Es un sensor ambiental: no emite ni recibe códigos. Se muestran sus lecturas.",
    "a2": (
        "Es un sensor ambiental: no emite ni recibe códigos. La librería todavía no expone "
        "sus lecturas, así que solo se lista."
    ),
    "hysen": "Es un termostato: Home Assistant ya lo controla como entidad climate.",
    "hvac": "Es un aire acondicionado: Home Assistant ya lo controla como entidad climate.",
    "dooya": (
        "Es un motor de cortina: se comunica por su propio protocolo, no con códigos RF que "
        "se puedan aprender. Home Assistant ya lo controla como entidad cover."
    ),
    "wser": (
        "Es un motor de cortina: se comunica por su propio protocolo, no con códigos RF que "
        "se puedan aprender. Home Assistant ya lo controla como entidad cover."
    ),
    "lb": "Es una lámpara: no tiene códigos para aprender. Home Assistant ya la controla.",
    "s1c": "Es un kit de alarma: no tiene códigos para aprender.",
    "s3": "Es un hub: no tiene códigos para aprender. Home Assistant ya lo controla.",
    "ehc31": "Es un enchufe: no tiene códigos para aprender. Home Assistant ya lo controla.",
}


# Bands the RF-capable models cover. Shown for information only: the discovery
# protocol reports model, MAC and IP and nothing about radio bands, so these are
# a property of the model rather than something the device announces. The real
# frequency of a remote is only known once sweep_frequency() finds it.
RF_BANDS = "433 MHz (433,05-434,79) y 315 MHz (314,95-315,25)"


class Capabilities(BaseModel):
    """What a discovered device can actually do.

    Drives the UI directly: the learn tab offers only the modes that are true
    here, so there is no way to start an RF sweep on a device with no radio.
    """

    learn_ir: bool = False
    send_ir: bool = False
    learn_rf: bool = False
    send_rf: bool = False
    read_state: bool = False
    # Shown next to the disabled actions so it is clear the device was seen and
    # why nothing is offered for it.
    no_learn_reason: str | None = None
    # Informational only, see RF_BANDS. None for devices with no radio.
    rf_bands: str | None = None


class Device(BaseModel):
    mac: str
    host: str
    port: int = 80
    devtype: int
    model: str
    manufacturer: str
    device_class: str
    capabilities: Capabilities
    online: bool = True
    # Set when the device was added by IP instead of answering the broadcast, so
    # a failed rescan does not drop it from the list.
    manual: bool = False
    last_seen: float | None = None
    last_error: str | None = None
    # Whatever check_sensors()/check_power() returned on the last poll, already
    # formatted for display. None for devices with nothing to report.
    state: dict[str, Any] | None = None


def capabilities_for(device_class: str) -> Capabilities:
    """Map a python-broadlink class name to what the UI may offer."""
    learn_ir = device_class in IR_CAPABLE_CLASSES
    learn_rf = device_class in RF_CAPABLE_CLASSES
    reason = None

    if not learn_ir:
        for prefix, text in NO_LEARN_REASONS.items():
            if device_class.startswith(prefix):
                reason = text
                break
        else:
            reason = "Este modelo no aprende ni envía códigos."
    elif not learn_rf:
        reason = "Este modelo no tiene radio: solo infrarrojo."

    return Capabilities(
        learn_ir=learn_ir,
        send_ir=learn_ir,
        learn_rf=learn_rf,
        send_rf=learn_rf,
        read_state=device_class in STATE_READABLE_CLASSES,
        no_learn_reason=reason,
        rf_bands=RF_BANDS if learn_rf else None,
    )
