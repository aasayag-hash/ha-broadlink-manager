# Broadlink Manager

Add-on de Home Assistant para aprender códigos **RF (433/315 MHz) e IR** con dispositivos
Broadlink, guardarlos con nombre, probarlos, organizarlos por equipo y convertirlos en
entidades de Home Assistant. Todo desde una interfaz web, sin YAML y sin Herramientas de
Desarrollo.

## Qué hace

- **Busca solo** todos los Broadlink de la red y muestra qué puede hacer cada uno.
- **Aprende códigos RF e IR** con un asistente paso a paso, incluido el barrido de
  frecuencia que hace falta para RF.
- **Guarda los códigos** en el mismo lugar donde los guarda Home Assistant
  (`.storage/broadlink_remote_<MAC>_codes`), así que `remote.send_command` los usa sin
  configuración extra.
- **Planilla agrupada por equipo** donde se puede probar, renombrar, mover y borrar cada
  código.
- **Crea entidades** (botones e interruptores) por MQTT discovery, sin reiniciar Home
  Assistant.

## Requisitos

- Home Assistant OS o Supervised (los add-ons no existen en HA Container ni Core).
- Un Broadlink **RM pro** o **RM4 pro** para RF. Para IR sirve cualquier RM, incluido el mini.
- Un broker MQTT si querés generar entidades. Si usás el add-on **Mosquitto**, el add-on
  lo detecta solo; si tu broker está en otra máquina o en otro puerto, se pueden cargar los
  datos a mano desde la pestaña Entidades. El aprendizaje de códigos funciona igual sin
  broker.

## Instalación

1. En Home Assistant, andá a **Ajustes → Complementos → Tienda de complementos**.
2. En el menú de los tres puntos (arriba a la derecha) elegí **Repositorios**.
3. Pegá esta URL:

   ```
   https://github.com/aasayag-hash/ha-broadlink-manager
   ```

4. Buscá **Broadlink Manager** en la tienda, instalalo e iniciálo.
5. Abrilo desde la barra lateral.

## Cómo se usa

1. **Dispositivos** — el add-on busca solo. Si tu Broadlink está en otra VLAN o red
   aislada, agregalo a mano por IP.
2. **Aprender** — elegí el dispositivo, el modo (IR o RF), y seguí las instrucciones en
   pantalla. Antes de guardar podés probar el código para confirmar que el aparato
   responde.
3. **Códigos** — la planilla con todo lo aprendido, agrupado por equipo. Desde acá se
   prueba, se renombra, se mueve de equipo y se borra.
4. **Entidades** — convertí un código en un botón, o un par de códigos en un interruptor.
   Aparecen en Home Assistant al instante, agrupados bajo el dispositivo Broadlink.

## Aviso sobre los datos

El add-on escribe en `.storage`, la carpeta interna de Home Assistant. Antes de cada
escritura hace una copia de respaldo en `/config/broadlink_manager/backups/`, y escribe
de forma atómica para que un corte de luz no pueda dejar el archivo a medias.

## Licencia

MIT — ver [LICENSE](LICENSE).
