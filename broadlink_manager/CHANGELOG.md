# Changelog

## 0.1.0

**Nuevo**

- Buscador de dispositivos Broadlink: escanea por broadcast en todas las interfaces de
  red, así que también encuentra equipos que están fuera de la red principal del servidor.
- Alta manual por IP, para cuando el dispositivo está en una VLAN o red aislada a la que
  el broadcast no llega.
- La lista recuerda los dispositivos ya vistos: si uno deja de responder queda marcado
  como offline en vez de desaparecer de la tabla.
- Cada dispositivo muestra qué puede hacer (aprender y enviar IR, aprender y enviar RF) y,
  cuando no puede, el motivo: un enchufe o un sensor no tienen códigos para aprender.
- Estado en vivo de solo lectura para enchufes, sensores y termostatos, como inventario de
  lo que hay en la red.
- Reescaneo automático en segundo plano y botón de búsqueda manual.
- Los equipos con radio muestran las bandas que cubren: 433 MHz y 315 MHz. Es un dato
  informativo del modelo, porque el descubrimiento no informa las bandas; la frecuencia
  real de cada control se conoce recién al hacer el barrido.
- Ícono y logo propios.
