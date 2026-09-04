# Changelog

## 0.5.0

**Nuevo**

- **Exportar e importar códigos** en un archivo JSON legible. Sirve de respaldo aparte del
  automático, y para llevar un mando ya aprendido a otra instalación. Se puede exportar
  todo o solo algunos equipos.
- Antes de importar se muestra **qué va a pasar**: cuántos códigos son nuevos y cuáles ya
  existen con ese nombre. Si hay conflictos podés elegir entre dejar los tuyos, guardar los
  dos (al nuevo se le agrega un número) o reemplazarlos.
- Se rechazan los archivos que no son un export de este add-on, los de una versión más
  nueva y los que traen códigos mal formados, antes de tocar nada.

- **Cambiar el modelo de un dispositivo a mano.** Si el add-on no reconoce tu equipo (lo
  muestra como desconocido) o lo reconoce como algo con menos funciones de las que tiene,
  podés elegir el modelo equivalente de la lista y se va a tratar como ese. La elección
  queda guardada y sobrevive a los reinicios y a los reescaneos.

**Corregido**

- Faltaba leer el estado de nueve familias que la librería sí expone: aires acondicionados
  (`hvac`), lámparas (`lb1`, `lb2`), enchufes con panel (`bg1`, `ehc31`), hubs (`s3`) y
  **motores de cortina** (`dooya`, `dooya2`, `wser`, que informan qué tan abiertos están).
  Aparecían en la lista sin ninguna lectura, como si no respondieran.
- La explicación de los motores de cortina decía que no se les puede leer nada, y era
  falso: no tienen códigos para aprender porque se manejan con comandos directos, pero su
  posición sí se lee.

**Detalle**

- Quedan cubiertas las **27 familias y 137 modelos** que reconoce la librería: cada una
  declara exactamente lo que se le puede leer o aprender, verificado con tests.
- El descubrimiento automático funciona igual para todas: los que no son de la familia RM
  también se detectan solos, con su modelo, su IP y sus lecturas.

## 0.4.0

**Nuevo**

- Crear **entidades en Home Assistant** desde los códigos aprendidos: botones (un código)
  e interruptores (un par encender/apagar). Aparecen solas, sin reiniciar ni tocar YAML, y
  quedan agrupadas bajo el dispositivo Broadlink al que pertenecen.
- Las entidades sobreviven a los reinicios de Home Assistant y del add-on.
- Borrarlas desde la app las saca también de Home Assistant.
- **Configuración del broker MQTT**: se detecta solo el que usa Home Assistant, y se puede
  cambiar servidor, puerto, usuario, contraseña y SSL si tu broker está en otra máquina o
  en un puerto distinto. Dejando el servidor vacío se vuelve a la detección automática.
  Al guardar se reconecta y te dice en el momento si funcionó.
- Sin broker MQTT el add-on sigue sirviendo para buscar dispositivos, aprender códigos y
  administrar la planilla: lo único que no vas a poder hacer es crear entidades.

**Detalle**

- Los interruptores se crean en modo optimista, porque un control remoto no informa su
  estado: Home Assistant muestra lo último que se le pidió.

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
