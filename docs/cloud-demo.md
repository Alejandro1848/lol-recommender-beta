# Demo de LoL Recommender en Cloud Run

Preparacion para `lol-recommender-beta-1848`, region `us-central1`, bucket privado
`lol-recommender-beta-1848-data`, repositorio Docker `demo` e identidad
`lol-demo-runtime@lol-recommender-beta-1848.iam.gserviceaccount.com`.

Esta fase prepara el codigo; no despliega recursos, no sube datos ni configura
facturacion. La primera construccion Linux debe validarse en Cloud Build porque
Docker no esta instalado en el equipo local usado para preparar estos cambios.

## Comportamiento

- Un proceso sirve React compilado y FastAPI en `0.0.0.0:$PORT`.
- `PUBLIC_DEMO=true` evita cargar `.env` y deshabilita la clave de Riot.
- El paquete privado se descarga una vez por arranque usando la identidad de
  Cloud Run, con comprobacion SHA-256 y generacion de GCS. No requiere claves JSON.
- SQLite y modelos se extraen a un directorio temporal; se descartan al parar.
- Se prepara un replay al minuto 15 a partir de estadisticas finales. Es una
  simulacion, no una reconstruccion exacta ni una partida actual.
- Se cargan los modelos existentes: no se entrena ni ingesta al arrancar.
- Todos los visitantes comparten el mismo escenario. No hay cuentas personales.
- Las rutas `/api/coach-ai/*` quedan bloqueadas; se ocultan entrenamiento y
  busqueda de jugadores en el frontend de demo.
- Hay limites compartidos de 30 peticiones de chat y 300 peticiones API/minuto,
  por proceso. No son un limite monetario ni sustituyen las cuotas del LLM.
- La app local conserva su modo habitual si se ejecuta con el orquestador normal
  y se compila el frontend sin `VITE_PUBLIC_DEMO=true`.

## Exportacion local

Desde la raiz del proyecto, usando el entorno de entrenamiento:

```powershell
..\venv\Scripts\python.exe scripts/export_demo_assets.py --champion Diana --tier GOLD --output storage/cloud-export/diana-gold-demo-v1.zip
```

El script lee la base original sin modificarla y crea una base nueva de hasta
500 partidas con Diana, priorizando partidas con timelines. Conserva todos los
participantes de esas partidas. Cambia PUUID por hashes y nombres por alias de
demo. Es seudonimizacion, no una garantia de anonimizacion irreversible.

Incluye los ultimos modelos `item` y `live` de Diana/GOLD con sus metadatos, y
el cache de campeones/items de Data Dragon. No incluye otros modelos, datos de
autenticacion, `.env`, logs ni caches de consultas a Riot. No sobrescribe ZIPs.
Los modelos pueden pertenecer a un parche anterior: no se reentrenan implicitamente.

La muestra mas pequena reduce memoria/latencia, pero cambia el soporte historico
de las recomendaciones frente a la base completa. El entrenamiento original no cambia.

La salida muestra el SHA-256 necesario para desplegar. El ZIP esta ignorado en Git.
Los joblib se deben generar en un entorno de confianza: cargarlos ejecuta
deserializacion Python. El checksum verifica integridad, no convierte un modelo
de terceros en confiable.

## Verificacion local

```powershell
..\venv\Scripts\python.exe -m pip install -r requirements-test.txt
$env:VITE_PUBLIC_DEMO = 'true'
Push-Location frontend
npm.cmd run build
Pop-Location
..\venv\Scripts\python.exe scripts/check_cloud_demo.py storage/cloud-export/diana-gold-demo-v1.zip
..\venv\Scripts\python.exe -m pytest tests -q
```

El chequeo bloquea la red de requests, prueba rutas con los datos y modelos reales,
comprueba cinco solicitudes concurrentes y rechaza entrenamiento. No llama Gemini.
La carga de una imagen Linux y la latencia real en GCP se validan posteriormente.

## Transferencia y construccion (pasos posteriores)

1. Revisar los cambios y subir el codigo a GitHub. No incluir el ZIP ni secretos.
2. Clonar/actualizar ese commit en Cloud Shell. El repositorio incluye Dockerfile,
   `.dockerignore`, `.gcloudignore`, `requirements-cloud.txt` y `cloudbuild.yaml`.
3. Subir el ZIP desde el PC con la opcion de Cloud Shell **Subir archivo**.
4. Copiarlo a un nombre versionado en GCS:

```bash
gcloud storage cp diana-gold-demo-v1.zip gs://lol-recommender-beta-1848-data/releases/diana-gold-demo-v1.zip --if-generation-match=0
```

5. Crear/verificar la identidad de Cloud Build. Necesita escribir imagenes en
   `demo`, escribir logs y leer el bucket de fuentes de la construccion. El usuario
   que ejecuta el build necesita poder usar esa identidad. Estos permisos son
   distintos de la cuenta runtime, que solo lee el bucket de datos.
6. Enviar `cloudbuild.yaml` con la cuenta de construccion elegida. La imagen usa
   `$BUILD_ID` como etiqueta para identificar exactamente la construccion.
7. Validar el build antes de desplegar; revisar el tamano de la imagen y no
   acumular imagenes antiguas. No habilitar analisis automaticos de pago.

## Configuracion futura del servicio

Cloud Run, facturacion por solicitudes, 1 vCPU, 2 GiB, concurrencia 5, un worker,
minimo 0 y maximo 1. Sin balanceador adicional, VPC connector, GPU ni Cloud SQL.
La cuenta runtime usa ADC y sus permisos de lectura del bucket.

Variables obligatorias:

```text
PUBLIC_DEMO=true
DEMO_ASSETS_URI=gs://lol-recommender-beta-1848-data/releases/diana-gold-demo-v1.zip
DEMO_ASSETS_SHA256=<sha256 mostrado por el exportador>
```

El build ya fija `VITE_PUBLIC_DEMO=true`. El contenedor ignora `.env` y no contiene
datos/modelos. Para probar solo la interfaz y ML se puede dejar `LLM_PROVIDER`
sin configurar. El siguiente paso habilita Gemini con `LLM_API_KEY` desde Secret
Manager y un modelo explicito; revisar sus cuotas y precio antes de activarlo.

No configurar RIOT_API_KEY en esta demo. La funcionalidad de Riot en vivo requiere
otra arquitectura y la autorizacion apropiada del proveedor.

## Encendido y apagado

Solo despues de crear el servicio:

```bash
gcloud run services update lol-demo --project=lol-recommender-beta-1848 --region=us-central1 --scaling=auto --min=0 --max=1
gcloud run services update lol-demo --project=lol-recommender-beta-1848 --region=us-central1 --scaling=0
```

Son comandos alternativos: el primero habilita visitas; el segundo deshabilita
el servicio al terminar. Mantener una revision sin etiquetas de trafico adicionales.
Las imagenes y archivos conservados pueden generar almacenamiento incluso apagado.
No se crean Jobs ni entrenamiento cloud en esta primera fase. Si se requiere,
se preparara un job separado con escritura en GCS y ejecucion manual.

Fuentes: [contrato de Cloud Run](https://docs.cloud.google.com/run/docs/container-contract),
[escalado manual](https://docs.cloud.google.com/run/docs/configuring/services/manual-scaling),
[precios](https://cloud.google.com/run/pricing).
