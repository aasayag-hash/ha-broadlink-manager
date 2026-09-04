<p align="center">
  <img src="logo.png" alt="Broadlink Manager" width="250">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencia-MIT-blue.svg" alt="Licencia MIT"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5.svg" alt="Add-on de Home Assistant">
  <img src="https://img.shields.io/badge/arquitecturas-amd64%20%7C%20aarch64%20%7C%20armv7-lightgrey.svg" alt="Arquitecturas">
</p>

Add-on de Home Assistant para aprender códigos **RF (433/315 MHz) e IR** con dispositivos
Broadlink, guardarlos con nombre, probarlos, organizarlos por equipo y convertirlos en
entidades de Home Assistant. Todo desde una interfaz web, sin YAML y sin Herramientas de
Desarrollo.

---

## El problema que resuelve

Aprender un código RF con un Broadlink en Home Assistant, hoy, es así:

1. Ir a Herramientas de Desarrollo y llamar a `remote.learn_command` a mano.
2. Hacer el barrido de frecuencia a ciegas, sin saber si va bien.
3. Adivinar si el código quedó guardado.
4. Escribir a mano un script o un template en YAML para poder usarlo.
5. Reiniciar Home Assistant.

Los códigos terminan enterrados en `.storage/broadlink_remote_<MAC>_codes`, un archivo que
Home Assistant pide expresamente no editar a mano.

Este add-on hace los cinco pasos desde una pantalla.

## Qué hace

| | |
|---|---|
| 🔍 **Busca solo** | Encuentra todos los Broadlink de la red, incluso en otras interfaces. Alta manual por IP para VLANs y redes aisladas. |
| 🎛️ **Muestra qué puede cada uno** | Un RM pro aprende IR y RF; un RM mini solo IR; un enchufe no tiene nada que aprender — y lo dice. |
| 📡 **Aprende IR y RF** | Asistente paso a paso con cuenta regresiva. El barrido de frecuencia con instrucciones claras en cada fase. |
| 🧩 **Plantillas por equipo** | Elegís "televisor" o "portón" y te va pidiendo los botones esperados, ya nombrados y agrupados. |
| 🏷️ **Identifica el tipo** | Cada código indica si es IR, RF 433 o RF 315, leído del código en sí. |
| 📱 **Control remoto virtual** | Los botones de un equipo en grande, para disparar de un toque desde el celular. |
| 📋 **Planilla por equipo** | Probar, renombrar, mover entre equipos y borrar. Muestra también lo que aprendiste antes por otros medios. |
| 🔌 **Crea entidades** | Botones e interruptores que aparecen solos en Home Assistant, sin reiniciar ni tocar YAML. |
| 📤 **Exporta e importa** | Respaldo en JSON legible, o llevar un mando aprendido a otra instalación. |
| 💾 **Cuida tus datos** | Respaldo antes de cada modificación y escritura atómica. |

## Requisitos

- **Home Assistant OS o Supervised.** Los add-ons no existen en HA Container ni Core.
- **Un Broadlink RM pro o RM4 pro** para RF. Para IR sirve cualquier RM, incluido el mini.
- **Un broker MQTT** solo si querés generar entidades. Si usás el add-on Mosquitto, se
  detecta solo. El aprendizaje de códigos funciona igual sin broker.

## Instalación

1. En Home Assistant: **Ajustes → Complementos → Tienda de complementos**.
2. Menú de tres puntos (arriba a la derecha) → **Repositorios**.
3. Pegá esta URL:

   ```
   https://github.com/aasayag-hash/ha-broadlink-manager
   ```

4. Buscá **Broadlink Manager** en la tienda, instalalo e iniciálo.
5. Abrilo desde la barra lateral.

## La idea general

Cinco solapas, en el orden en que se usan: primero encontrás el hardware, después le
enseñás los códigos, y a partir de ahí los usás.

```
   ───────── se configura una vez ─────────    ──── se usa siempre ────

   ① DISPOSITIVOS   →    ② APRENDER     ─┬─→   ③ CONTROL     probar a mano
   ¿qué hardware         capturar los    │
   hay en la red?        códigos del     ├─→   ④ CÓDIGOS     ordenar, corregir
                         control         │
                                         └─→   ⑤ ENTIDADES   automatizar en HA
```

El único orden obligatorio es el principio: sin un dispositivo elegido no se puede
aprender, y sin códigos guardados las otras tres solapas no tienen nada que mostrar. Después
de eso vas y venís libremente.

### Qué hace cada solapa

| Solapa | Para qué es | Qué necesita antes | Qué deja hecho | Dónde queda |
|---|---|---|---|---|
| **① Dispositivos** | Encontrar los Broadlink de la red y ver qué puede cada uno | Nada | Un dispositivo elegido | `/data/devices.json` |
| **② Aprender** | Capturar los códigos de un control, de a uno o con plantilla | Un dispositivo que aprenda (familia RM) | Códigos guardados con nombre y equipo | `.storage` de HA |
| **③ Control** | Disparar los códigos de un toque, desde el celular | Códigos guardados | Nada: solo envía | — |
| **④ Códigos** | Ordenar: renombrar, mover, borrar, exportar, importar | Códigos guardados | Los mismos códigos, ordenados | `.storage` de HA |
| **⑤ Entidades** | Que HA los use en automatizaciones y tableros | Códigos guardados + broker MQTT | Botones e interruptores en HA | MQTT + `/data/entities.json` |

**Regla que atraviesa todo:** los códigos viven en un solo lugar —
`.storage/broadlink_remote_<MAC>_codes`, el archivo de Home Assistant. No hay copia propia.
Lo que aprendés acá lo ve HA, y lo que ya tenías aprendido por otros medios aparece acá.

## Cómo se usa

### 1. Dispositivos

El add-on busca solo al arrancar y cada dos minutos. Cada equipo muestra qué puede hacer:

```
RM pro       192.168.1.158   ● online
  ✓ Aprender IR   ✓ Enviar IR   ✓ Aprender RF   ✓ Enviar RF
  Bandas RF: 433 MHz (433,05-434,79) y 315 MHz (314,95-315,25)

RM mini 3    192.168.1.41    ● online
  ✓ Aprender IR   ✓ Enviar IR   ✗ Este modelo no tiene radio: solo infrarrojo

SP4          192.168.1.55    ● online   ·  Estado: encendido
  ✗ Es un enchufe: no tiene códigos para aprender. Home Assistant ya lo controla.

A1 sensor    192.168.1.60    ● online   ·  Temperatura: 24.3 °C · Humedad: 47 %
  ✗ Es un sensor ambiental: no emite ni recibe códigos. Se muestran sus lecturas.
```

La lógica de esta solapa:

```
Abrís la solapa
   │
   ├─ Aparece tu Broadlink?
   │     SÍ → tocalo para elegirlo. Listo, seguí en Aprender.
   │     NO ↓
   │
   ├─ Tocá "Buscar" (rebarre todas las interfaces)
   │     Aparece? → elegilo
   │     NO ↓
   │
   ├─ Está en otra VLAN, red de invitados o WiFi aislada?
   │     → agregalo por IP con el formulario de la izquierda
   │
   └─ Aparece pero dice "no reconoce este modelo"?
         → botón Modelo → elegí el modelo equivalente
           (pasa con clones y revisiones nuevas de hardware)
```

**Qué reconoce.** El descubrimiento automático encuentra por igual todas las familias, no
solo los RM: las 27 familias y 137 modelos que soporta
[python-broadlink](https://github.com/mjg59/python-broadlink) — controles RM, enchufes SP,
sensores ambientales A1, zapatillas MP1, lámparas LB1/LB2, termostatos Hysen, aires HVAC,
motores de cortina Dooya/Wser, hubs S3 y más.

Solo la familia RM aprende códigos; del resto se muestran las lecturas que la librería
expone (temperatura, humedad, encendido, consumo, consigna, posición de la cortina), en
modo **solo lectura** — Home Assistant ya los controla nativamente, y dos procesos peleando
por el mismo socket es una causa conocida de desconexiones.

**Si tu equipo no aparece bien identificado.** Cuando la librería no reconoce un modelo lo
muestra como desconocido y no se le puede hacer nada; pasa con clones y con revisiones
nuevas de hardware. El botón **Modelo** de cada fila permite elegir a mano el modelo
equivalente, y el add-on lo trata como ese. La elección se guarda y sobrevive a los
reescaneos.

### 2. Aprender

La lógica de esta solapa:

```
Tenés que aprender...
   │
   ├─ ¿un control entero (TV, aire, portón)?
   │     → elegí la plantilla, ponele nombre al equipo,
   │       y te va pidiendo los botones uno por uno
   │
   └─ ¿un botón suelto?
         → Aprender IR   o   Aprender RF
                │                  │
                │                  ├─ FASE 1: mantené apretado
                │                  │   → detecta la frecuencia
                │                  │
                │                  └─ FASE 2: soltá y apretá una vez
                │                      → captura el código
                │                        (si falla, reintentás sin
                │                         repetir el barrido)
                │
                └─ apuntá y apretá → captura el código
                            │
                            ▼
                    ┌─────────────────────┐
                    │  Probar el código   │ ← el aparato reacciona?
                    └─────────────────────┘
                       SÍ ↓         NO → Descartar y reintentar
                    Nombrar y guardar
```

Elegí el dispositivo y el modo. **IR** es un solo paso: apuntás el control y apretás.
**RF** son dos fases: primero mantenés el botón apretado para que encuentre la frecuencia,
después lo soltás y das una pulsación corta.

Si la segunda fase falla, se reintenta **sin repetir el barrido**. Y una vez detectada la
frecuencia de un control, los demás botones de ese mismo control se aprenden salteando el
barrido — de unos 30 segundos a unos 3.

Antes de guardar podés **probar el código** para confirmar que el aparato responde.

**Con plantilla, más rápido.** Si vas a aprender un control entero, elegí qué tipo de equipo
es (televisor, aire, portón, ventilador, luces RF), ponele nombre, y la app te va pidiendo
los botones uno por uno — ya nombrados y agrupados. Marca en verde lo hecho y resalta el
que sigue, pero podés ir en cualquier orden, salir y volver más tarde. Los botones
opcionales están señalados: con los básicos ya tenés el equipo funcionando.

Las plantillas son archivos JSON en [`broadlink_manager/templates/`](broadlink_manager/templates/),
así que agregar una es un archivo y no un cambio de código:

```json
{
  "id": "mi_equipo",
  "name": "Mi equipo",
  "icon": "🎵",
  "note": "Un aviso que conviene leer antes de empezar (opcional).",
  "buttons": [
    { "command": "power", "label": "Encender / apagar", "hint": "El botón rojo." },
    { "command": "extra", "label": "Algo que no todos tienen", "optional": true }
  ]
}
```

### 3. Control

El control remoto virtual: elegís el equipo y sus botones aparecen en grande para disparar
de un toque. Es la solapa para usar todos los días, y anda bien desde el celular.

```
Control remoto      [ TV Living (6) ▾ ]

┌──────────┐ ┌──────────┐ ┌──────────┐
│  power   │ │  vol_up  │ │ vol_down │
│    IR    │ │    IR    │ │    IR    │
└──────────┘ └──────────┘ └──────────┘
```

Cada botón se pone en verde cuando el código salió, o en rojo si falló — el equipo no
informa nada, así que sin eso una pulsación que no hizo nada se vería igual que una que
funcionó. Un doble toque tampoco dispara dos veces: en un portón eso sería abrirlo y
cerrarlo enseguida.

### 4. Códigos

La planilla, agrupada por equipo y con buscador:

```
▾ TV Living                                      3 códigos   [Renombrar] [Borrar]
    power       IR       JgBQAAABKZMTEhM3…       [Probar] [Editar] [Borrar]
    vol_up      IR       JgBQAAABKZMTEhM3…       [Probar] [Editar] [Borrar]
    vol_down    IR       JgBQAAABKZMTEhM4…       [Probar] [Editar] [Borrar]

▾ Portón                                          1 código   [Renombrar] [Borrar]
    abrir       RF 433   sgAyAHFwcXBxcHFw…       [Probar] [Editar] [Borrar]
```

Lee y escribe el mismo archivo que usa Home Assistant, así que `remote.send_command`
funciona sin configuración extra:

```yaml
action: remote.send_command
target:
  entity_id: remote.broadlink_rm_pro
data:
  device: Portón
  command: abrir
```

Desde la barra de esa pestaña podés **exportar** todo a un JSON legible (respaldo aparte del
automático, o para llevar el mando a otra instalación) e **importar** un archivo. Antes de
importar te muestra qué códigos son nuevos y cuáles ya existen, y para los repetidos elegís
si dejar los tuyos, guardar los dos o reemplazarlos.

La lógica de esta solapa:

```
¿Qué querés hacer?
   │
   ├─ Ver si un código funciona          → Probar
   ├─ Le puse un nombre confuso          → Editar
   ├─ Lo guardé en el equipo equivocado  → Editar → cambiar el equipo
   ├─ Ya no lo uso                       → Borrar
   ├─ Todo un equipo quedó mal nombrado  → Renombrar (en la fila del grupo)
   │      ⚠ tus automatizaciones siguen usando el nombre viejo
   │
   ├─ Guardar un respaldo / llevarlo a otro HA   → Exportar
   └─ Traer códigos de otra instalación          → Importar
            │
            └─ ¿hay nombres repetidos?
                  → elegís: dejar los tuyos / guardar los dos / reemplazar
```

### 5. Entidades

Convertí un código en un **botón**, o un par de códigos en un **interruptor**. Aparecen al
instante en Home Assistant, agrupados bajo el dispositivo Broadlink, sin reiniciar nada.

Si tu broker no es el que informa Home Assistant, podés cargar servidor, puerto, usuario,
contraseña y SSL a mano. Dejando el servidor vacío vuelve a la detección automática.

La lógica de esta solapa:

```
¿Dice "MQTT conectado"?
   │
   NO → abrí "Configuración del broker MQTT"
   │      ├─ ¿tenés el add-on Mosquitto? → se detecta solo, revisá el error
   │      └─ ¿tu broker está en otra máquina o puerto?
   │            → cargá servidor, puerto, usuario y contraseña
   │              (al guardar reconecta y te dice si funcionó)
   SÍ ↓
   │
   └─ ¿qué querés crear?
         │
         ├─ Un código, una acción (abrir el portón, apagar la TV)
         │     → Botón
         │
         └─ Dos códigos, encender y apagar (una luz, un ventilador)
               → Interruptor
                  ⓘ va en modo optimista: un control no informa su estado,
                    así que HA muestra lo último que se le pidió
```

## Qué pasa por dentro

Dos recorridos, que explican por qué las cosas están donde están.

**Aprender un código:**

```
  El control remoto
        │ señal IR o RF
        ▼
  El Broadlink  ── el add-on le pide "entrá en modo aprendizaje" y le
        │           pregunta cada medio segundo si ya llegó algo
        ▼
  backend/learning.py  ── máquina de estados; en RF son dos fases
        │
        ▼
  backend/storage.py   ── respaldo → escritura atómica → merge
        │
        ▼
  /config/.storage/broadlink_remote_<MAC>_codes
        │
        └──→ lo lee también Home Assistant, sin configuración extra
```

**Enviar un código** (desde el control virtual, la planilla o una entidad):

```
  Vos, o una automatización de HA
        │
        ▼
  El add-on  ── nunca le habla directo al Broadlink
        │
        ▼
  remote.send_command de Home Assistant
        │
        ▼
  El Broadlink → el aparato
```

Ese desvío por Home Assistant es a propósito: **HA es el único que mantiene la sesión con
el Broadlink**. Dos procesos turnándose el mismo socket es una causa conocida de
desconexiones, así que el add-on aprende (cuando HA no está usando el equipo) pero delega
el envío.

La única excepción es el botón **Probar** del código recién capturado, que va directo al
hardware — todavía no está guardado, así que `remote.send_command` no tendría qué
referenciar.

## Cómo guarda los datos

Los códigos van a `/config/.storage/broadlink_remote_<MAC>_codes`, el mismo archivo que usa
Home Assistant. No hay duplicación: lo que aprendés acá lo ve HA, y lo que ya tenías
aprendido aparece acá.

Ese archivo Home Assistant pide no editarlo a mano, así que cada modificación:

- Guarda un respaldo en `/config/broadlink_manager/backups/` (se conservan los 20 últimos).
- Escribe de forma atómica, para que un corte de luz no pueda dejarlo a medias.
- Relee el archivo justo antes de escribir, para no pisar códigos que HA haya agregado.
- Se niega a tocar nada si la versión del formato no es la esperada.

## Limitaciones conocidas

**Algunos equipos vendidos como "RM pro" dan falsos positivos en el barrido RF**: informan
que detectaron señal a los pocos segundos aunque no haya ningún control transmitiendo, y
después la captura nunca llega. La secuencia que usa el add-on es la misma que la
herramienta oficial de `python-broadlink`, así que no es algo que se pueda resolver desde
el software. Si te pasa, probá aprender ese control **en modo IR**: el receptor capta
varios controles de radio por esa vía.

**El descubrimiento no informa las bandas de radio.** El protocolo solo devuelve modelo,
MAC e IP. Las bandas que se muestran son una propiedad del modelo; la frecuencia real de un
control se conoce recién al hacer el barrido.

**Renombrar un equipo no actualiza tus automatizaciones.** `remote.send_command` referencia
el equipo por nombre en el parámetro `device`: si lo cambiás, hay que actualizarlas a mano.

## Para desarrollar

```bash
python -m venv .venv
.venv/bin/pip install -r broadlink_manager/backend/requirements.txt pytest

# Tests
python -m pytest

# Correr fuera del contenedor
cd broadlink_manager
BROADLINK_MANAGER_DATA_DIR=/tmp/blm-data \
BROADLINK_MANAGER_CONFIG_DIR=/tmp/blm-config \
  python -m uvicorn backend.main:app --port 8099
```

Las dos variables de entorno redirigen `/data` y `/config`, que solo existen dentro del
add-on.

```
broadlink_manager/
├── backend/
│   ├── discovery.py       # Descubrimiento multi-interfaz y capacidades por modelo
│   ├── learning.py        # Máquinas de estado de captura IR y RF
│   ├── storage.py         # El .storage de HA, con respaldo y escritura atómica
│   ├── entities.py        # MQTT discovery
│   └── main.py            # FastAPI
├── templates/             # Plantillas de equipo, en JSON
└── frontend/              # HTML + JS + CSS, sin build
```

## Créditos

- [python-broadlink](https://github.com/mjg59/python-broadlink) de Matthew Garrett, la
  librería que hace posible todo esto (y la misma que usa Home Assistant por dentro).
- [broadlinkmanager-docker](https://github.com/t0mer/broadlinkmanager-docker) y
  [HAIR](https://github.com/DAB-LABS/HAIR), que resuelven partes de este problema y de los
  que salieron varias ideas.

## Licencia

MIT — ver [LICENSE](LICENSE).
