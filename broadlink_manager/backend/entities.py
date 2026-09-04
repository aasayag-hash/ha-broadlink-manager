from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any

import httpx
import paho.mqtt.client as mqtt

logger = logging.getLogger("broadlink_manager.entities")

SUPERVISOR_BASE = "http://supervisor"
DISCOVERY_PREFIX = "homeassistant"

# Prefix for every topic this add-on owns, so its entities are easy to spot in
# an MQTT explorer and simple to wipe if needed.
BASE_TOPIC = "broadlink_manager"

CONNECT_TIMEOUT = 10.0
MAX_BACKOFF_SECONDS = 30


class MqttError(Exception):
    """Raised when MQTT is unavailable or a publish cannot be confirmed."""


def slugify(value: str) -> str:
    """Turn a display name into something usable in a topic and an entity id."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "sin_nombre"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}"}


def supervisor_broker() -> tuple[dict[str, Any] | None, str | None]:
    """Ask the Supervisor for the MQTT broker. Returns (config, error).

    Requires `services: [mqtt:need]` in config.yaml; without it the Supervisor
    refuses the request. This is the zero-configuration path: the credentials
    come from whatever broker add-on the user already runs.
    """
    try:
        response = httpx.get(f"{SUPERVISOR_BASE}/services/mqtt", headers=_headers(), timeout=10.0)
        response.raise_for_status()
        data = response.json().get("data", {})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            return None, (
                "No hay ningún broker MQTT configurado en Home Assistant. Instalá el add-on "
                "Mosquitto, o cargá los datos del broker a mano acá abajo."
            )
        return None, f"El Supervisor respondió HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"No se pudo consultar el broker MQTT automáticamente: {exc}"

    if not data.get("host"):
        return None, "El Supervisor no devolvió los datos del broker MQTT."
    return data, None


def broker_config() -> tuple[dict[str, Any] | None, str | None]:
    """The broker to use: the user's own settings if any, else the Supervisor's.

    Manual settings win so the add-on also works where the Supervisor cannot
    help -- a broker on another machine, a non-default port, or Home Assistant
    Container, where there is no Supervisor at all.
    """
    from . import settings_store

    manual = settings_store.get_mqtt()
    if manual and manual.get("host"):
        return manual, None
    return supervisor_broker()


class MqttPublisher:
    """Publishes retained discovery configs and listens for button presses.

    Retained on purpose: the entities must survive a restart of Home Assistant
    and of this add-on, without the user having to reopen the app.
    """

    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._lock = threading.RLock()
        self._connected = threading.Event()
        self._status = "disconnected"
        self._last_error: str | None = None
        self._stop = False
        self._on_command: Any = None
        self._reconnect_thread: threading.Thread | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self, on_command) -> tuple[bool, str | None]:
        """Connect to the broker. Returns (ok, error)."""
        config, error = broker_config()
        if error:
            self._status = "error"
            self._last_error = error
            return False, error

        self._on_command = on_command
        self._stop = False
        # Cleared before connecting: a leftover flag from a previous session
        # would make the wait below return instantly and report success for a
        # connection that never happened.
        self._connected.clear()
        with self._lock:
            self._open(config)

        if not self._connected.wait(CONNECT_TIMEOUT):
            return False, (
                f"No se pudo conectar al broker MQTT en {config['host']}:{config['port']} "
                f"({self._last_error or 'sin respuesta'})."
            )
        return True, None

    def _close_client(self) -> None:
        """Tear down the current client, if any.

        Must run before building a replacement. Dropping the reference alone
        leaves paho's network thread and socket alive, and since every client
        shares one id the newcomer kicks the leaked one off the broker, which
        fires _handle_disconnect and schedules yet another reconnect. Measured:
        three reconnects leaked eight threads and turned into a self-sustaining
        loop, with each still subscribed to the command topic, so one button
        press fired several times.
        """
        client, self._client = self._client, None
        if client is None:
            return
        # No _handle_disconnect for a teardown we asked for: it would schedule a
        # reconnect against the client we are replacing.
        client.on_disconnect = None
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:  # noqa: BLE001 - best effort teardown
            logger.debug("Error cerrando el cliente MQTT anterior: %s", exc)

    def _open(self, config: dict[str, Any]) -> None:
        self._close_client()

        # A fixed client id keeps one session per add-on; a random one would
        # leave orphaned sessions on the broker after every restart.
        client = mqtt.Client(client_id="broadlink_manager")
        if config.get("username"):
            client.username_pw_set(config["username"], config.get("password"))
        if config.get("ssl"):
            client.tls_set()

        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        self._config = config
        self._client = client
        self._status = "connecting"

        client.connect_async(config["host"], int(config["port"]), keepalive=30)
        client.loop_start()

    def _handle_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:
        if rc != 0:
            self._status = "error"
            self._last_error = f"El broker rechazó la conexión (código {rc})"
            logger.warning("MQTT connect refused: rc=%s", rc)
            return
        self._status = "connected"
        self._last_error = None
        # One wildcard subscription covers every entity this add-on owns,
        # including ones created later, so no resubscribe is needed per entity.
        client.subscribe(f"{BASE_TOPIC}/+/+/set")
        self._connected.set()
        logger.info("MQTT conectado a %s", self._config.get("host"))

    def _handle_disconnect(self, client: mqtt.Client, userdata, rc) -> None:
        self._connected.clear()
        if self._stop:
            return
        self._status = "disconnected"
        logger.warning("MQTT desconectado (rc=%s), reintentando", rc)
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        with self._lock:
            if self._stop or (self._reconnect_thread and self._reconnect_thread.is_alive()):
                return

            def retry() -> None:
                delay = 1
                while not self._stop and not self._connected.is_set():
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                    try:
                        # Rebuild rather than reconnect(): after loop_stop() the
                        # network loop does not restart on its own.
                        with self._lock:
                            self._open(self._config)
                    except Exception as exc:  # noqa: BLE001 - keep retrying
                        self._last_error = str(exc)

            self._reconnect_thread = threading.Thread(
                target=retry, name="mqtt-reconnect", daemon=True
            )
            self._reconnect_thread.start()

    def _handle_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        """Route a command topic back to whoever registered on_command.

        Topic shape: broadlink_manager/<mac_slug>/<entity_slug>/set
        """
        try:
            parts = msg.topic.split("/")
            if len(parts) != 4 or parts[0] != BASE_TOPIC or parts[3] != "set":
                return
            payload = msg.payload.decode("utf-8", errors="replace").strip()
            if self._on_command:
                self._on_command(parts[1], parts[2], payload)
        except Exception:  # noqa: BLE001 - a bad message must not kill the loop
            logger.exception("Falló el manejo de %s", msg.topic)

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        with self._lock:
            client = self._client
        if client is None or not self._connected.is_set():
            raise MqttError(
                "No hay conexión con el broker MQTT. Verificá que el add-on Mosquitto esté "
                "andando y volvé a intentar."
            )
        info = client.publish(topic, payload, qos=1, retain=retain)
        # Waiting for the broker's ack turns a silent failure into an error the
        # user sees: without it, publishing to a dead connection looks like it
        # worked and the entity simply never appears.
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise MqttError("El broker no confirmó la publicación.")

    def stop(self) -> None:
        # Set before taking the lock so a retry already waiting on it exits
        # instead of opening one more client behind us.
        self._stop = True
        with self._lock:
            self._close_client()
        self._connected.clear()
        self._status = "disconnected"


publisher = MqttPublisher()


def _device_block(mac: str, model: str) -> dict[str, Any]:
    """Groups every entity of one Broadlink under a single HA device."""
    # No via_device: it must point at a device that already exists in the
    # registry, and there is no parent device here -- HA logs a warning and
    # drops the link otherwise.
    return {
        "identifiers": [f"broadlink_manager_{slugify(mac)}"],
        "name": f"Broadlink {model}",
        "manufacturer": "Broadlink",
        "model": model,
    }


def config_topic(component: str, mac: str, entity_slug: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{slugify(mac)}_{entity_slug}/config"


def command_topic(mac: str, entity_slug: str) -> str:
    return f"{BASE_TOPIC}/{slugify(mac)}/{entity_slug}/set"


def state_topic(mac: str, entity_slug: str) -> str:
    return f"{BASE_TOPIC}/{slugify(mac)}/{entity_slug}/state"


def publish_button(mac: str, model: str, name: str, entity_slug: str) -> None:
    payload = {
        "name": name,
        "unique_id": f"broadlink_manager_{slugify(mac)}_{entity_slug}",
        "command_topic": command_topic(mac, entity_slug),
        "device": _device_block(mac, model),
    }
    publisher.publish(config_topic("button", mac, entity_slug), json.dumps(payload))


def publish_switch(mac: str, model: str, name: str, entity_slug: str) -> None:
    payload = {
        "name": name,
        "unique_id": f"broadlink_manager_{slugify(mac)}_{entity_slug}",
        "command_topic": command_topic(mac, entity_slug),
        "state_topic": state_topic(mac, entity_slug),
        "payload_on": "ON",
        "payload_off": "OFF",
        # One-way remotes give no feedback, so the switch has to assume its
        # command worked. Without this HA shows it as unavailable forever.
        "optimistic": True,
        "device": _device_block(mac, model),
    }
    publisher.publish(config_topic("switch", mac, entity_slug), json.dumps(payload))


def remove_entity(component: str, mac: str, entity_slug: str) -> None:
    """Delete a discovered entity: an empty retained payload on its config topic."""
    publisher.publish(config_topic(component, mac, entity_slug), "")


def publish_state(mac: str, entity_slug: str, state: str) -> None:
    publisher.publish(state_topic(mac, entity_slug), state)
