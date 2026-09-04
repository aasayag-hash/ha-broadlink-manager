# Changelog

## 0.3.0

**Nuevo**

- Aprendizaje de códigos **IR y RF** con asistente paso a paso: cuenta regresiva en
  pantalla, instrucciones de qué hacer en cada momento, y cancelación en cualquier punto.
- RF hace el barrido de frecuencia y después captura el código. Si el barrido sale bien
  pero falla la captura, se puede reintentar **sin repetir el barrido**.
- Una vez detectada la frecuencia de un control, los siguientes botones del mismo control
  se aprenden salteando el barrido: de unos 30 segundos a unos 3.
- Cada código indica si es **IR, RF 433 o RF 315**, leído del código en sí y no del modo
  que se eligió. Sirve porque un control de RF apuntado al Broadlink también se captura en
  modo IR, y el resultado se ve igual en la planilla.
- Antes de guardar se puede probar el código para confirmar que el aparato responde.
- 315 MHz funciona igual que 433: el barrido recorre todo el rango que soporta el equipo.

**Corregido**

- La captura fallaba al segundo de empezar. El dispositivo responde `ReadError` mientras
  todavía no se apretó ningún botón, y eso se estaba tomando como error en vez de como
  "seguí esperando". Detectado probando con un RM pro real.
- Un dispositivo que deja de responder al broadcast pero sigue vivo en su IP ahora se
  recupera solo, con reintento, en vez de quedar marcado como offline.
- Ya no se muestra "0.0 MHz" ni "None MHz": hay equipos que detectan la señal pero no
  informan la frecuencia. En ese caso se captura igual, porque el Broadlink se acuerda de
  la frecuencia internamente.

**Se sabe que**

- Algunos equipos vendidos como "RM pro" dan falsos positivos en el barrido RF: informan
  que detectaron una señal a los pocos segundos aunque no haya ningún control transmitiendo,
  y después la captura nunca llega. La secuencia que usa el add-on es la misma que la
  herramienta oficial de la librería, así que no es algo que se pueda resolver desde acá.
  Si te pasa, probá aprender ese control en modo IR: el receptor capta varios controles de
  radio por esa vía.

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
