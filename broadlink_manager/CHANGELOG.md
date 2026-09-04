# Changelog

## 0.2.0

**Nuevo**

- Planilla de códigos agrupada por equipo, con buscador y contador. Lee el mismo archivo
  que usa Home Assistant, así que también muestra los códigos que hayas aprendido antes
  por otros medios.
- Probar un código desde la planilla: se dispara al instante para confirmar que el aparato
  responde.
- Renombrar y mover códigos entre equipos, y renombrar o borrar un equipo completo.
- Los comandos aprendidos con la opción alternativa (toggle) se marcan como tales y se
  respetan: Home Assistant alterna entre sus dos códigos en cada envío.
- Antes de cada modificación se guarda un respaldo en
  `/config/broadlink_manager/backups/`, y la escritura es atómica: un corte de luz no
  puede dejar el archivo a medias.

**Importante**

- Al renombrar un equipo, las automatizaciones que lo usen en el parámetro `device` de
  `remote.send_command` siguen apuntando al nombre viejo. La app avisa, pero no puede
  reescribir tus automatizaciones.

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
