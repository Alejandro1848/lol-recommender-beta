# LoL Recommender

App local de analítica y recomendaciones para League of Legends: historial ranked, detección de partida en vivo, probabilidad de victoria (ML + heurísticas), recomendaciones explicadas de items/ganks/objetivos y un chatbot que solo responde con datos reales.

Corre 100% en tu máquina (`localhost`). Tu API key de Riot **nunca** sale del backend, nunca se envía al frontend y nunca se empaqueta en el `.exe`.

---

## Arquitectura

```
lol_recommender_app/
  main_orchestrator.py      # coordina flujos (sin lógica pesada)
  app/
    config.py               # Settings desde .env / variables de entorno
    container.py            # composition root (cablea todos los servicios)
    api/                    # FastAPI: server + rutas (player/live/recommendations/chat/model)
    riot/                   # RiotClient, LiveClient, DataDragon, rate limiting
    data/                   # SQLite, cache, schemas, loaders, normalizers, repositorios
    pipelines/              # ingesta historial, live, datos estáticos, dataset ML
    analytics/              # perfil, matchups, campeones, prioridad de líneas, items
    ml/                     # features, baseline, train, eval, registry, inference, estilo
    recommendations/        # motor + similitud + item/gank/objetivo + explicaciones
    chat/                   # chatbot por intents + contexto + prompts (LLM-ready)
    security/               # secrets (env/.env) + auth stub para Google Cloud futuro
  frontend/                 # React + Vite (dashboard oscuro)
  models/                   # modelos versionados (.joblib + .json)
  storage/                  # SQLite, cache ddragon, dataset de entrenamiento
  tests/                    # pytest
```

Diferencias justificadas vs. la estructura pedida:
- `app/container.py`: el orquestador y FastAPI comparten el mismo grafo de dependencias; centralizarlo evita duplicación y facilita tests.
- `app/api/routes_review.py`: la revisión post-partida (`/api/review/*`) reconstruye curvas de probabilidad desde las timelines; es análisis histórico, no live.

## Fuentes de datos (y sus límites)

| Fuente | Uso | Tipo de dato |
|---|---|---|
| Account-V1 | Riot ID → PUUID | histórico |
| Summoner-V4 | nivel, icono | histórico |
| League-V4 | tier/rank/LP | histórico |
| Match-V5 | historial ranked (dataset ML) | histórico |
| Spectator-V5 | detectar partida activa | partida_activa |
| Live Client Data API (`https://127.0.0.1:2999`) | datos en vivo del juego local | live_client |
| Data Dragon | campeones, items, imágenes, atributos | estático |

Cada payload de la API marca `data_source` para que la UI nunca mezcle fuentes en silencio. Lo que Riot no expone (vida/oro de enemigos en vivo, timers exactos de respawn) **no se inventa**: se dice.

---

## Instalación

Requisitos: Python 3.11+, Node 18+ (solo para el frontend).

```powershell
cd lol_recommender_app

# 1. Entorno Python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Configuración
copy .env.example .env
# Edita .env: RIOT_API_KEY, GAME_NAME, TAG_LINE, PLATFORM_ROUTING, REGIONAL_ROUTING

# 3. Frontend (una vez)
cd frontend
npm install
npm run build      # genera frontend/dist, que FastAPI sirve en /
cd ..
```

La API key se obtiene en https://developer.riotgames.com (las development keys expiran cada 24 h). **Nunca la compartas ni la subas a git.**

## Cómo ejecutar cada parte

### App completa (backend + frontend + inferencia periódica)
```powershell
python main_orchestrator.py --mode app
```
Hace todo el flujo: resuelve tu cuenta → ingesta historial → actualiza Data Dragon → entrena el modelo si no existe → levanta FastAPI en `http://127.0.0.1:8000` (abre el navegador solo). El dashboard es la primera pantalla.

### Solo ingesta de historial
```powershell
python main_orchestrator.py --mode ingest-history
```
Descarga tus últimas `MATCH_COUNT` partidas ranked (solo las nuevas) y las guarda en `storage/lol_recommender.db`. Si la DB está vacía, primero carga los CSV seed de `Learning/lan_ranked_match_sample`.

### Entrenar / reentrenar los modelos por campeón
```powershell
python main_orchestrator.py --mode train-champion
```
Entrena (o reutiliza si el parche no cambió) los dos modelos del campeón configurado: `item` (recomendación de builds) y `live` (probabilidad de victoria in-game con timelines). El modelo pregame se eliminó: rendía como una moneda (AUC ~0.53), así que la probabilidad arranca en un 50% fijo y la construye el modelo in-game.

### Diagnóstico de partida en vivo
```powershell
python main_orchestrator.py --mode live-check
```
Verifica Live Client Data API local y Spectator-V5.

### Overlay in-game (coaching de momento)
```powershell
python main_orchestrator.py --mode overlay
```
Abre una ventana ligera **always-on-top** (tkinter, sin dependencias nuevas) que flota sobre el juego y muestra exactamente 3 cosas:

1. **Probabilidad actual de victoria** — la del modelo in-game calibrado (la misma del dashboard).
2. **Próxima acción recomendada según tu rol** (Top/Jungla/Mid/ADC/Soporte) — p. ej. *"Dragón aparece en 45s: adelanta visión en río inferior"*, con la ventaja de oro estimada y la amenaza enemiga más fed como contexto.
3. **Compra prioritaria al volver a base** — el item #1 del recomendador, con el oro que te falta.

Detalles:
- Si el servidor (`--mode app`) ya corre, el modo solo abre la ventana; si no, levanta uno propio.
- Controles: **arrastrar** para mover, **doble clic** compacta (solo probabilidad), se cierra **solo con la ✕** de la esquina (un clic accidental no la mata).
- El juego debe estar en **"Pantalla completa (sin bordes)"** o "Ventana"; en pantalla completa exclusiva Windows tapa cualquier overlay.
- También existe la página `http://127.0.0.1:8000/overlay` (mismo contenido, para navegador/OBS) y el endpoint `GET /api/overlay/state`.
- La lógica vive en `app/coaching/`: `objective_timers.py` deriva los respawns de dragón/barón desde los eventos del Live Client (reglas públicas del parche), `team_performance.py` puntúa el desempeño observable de cada aliado/enemigo (KDA, CS/min vs. su rol) y `coach_engine.py` decide UNA acción por rol con la probabilidad del modelo. La diferencia de oro es una **estimación** desde kills/CS/torres (el oro enemigo real no es visible) y siempre se etiqueta como tal.

### Pruebas sin cliente de LoL abierto
```powershell
python main_orchestrator_test.py
```
Este orquestador alternativo valida el pipeline sin depender del Live Client
local. Es el modo recomendado para probar la app antes de usarla con una
cuenta personal o antes de entrar a partida.

Modos disponibles:

```powershell
# Auto: intenta Spectator-V5 si el jugador configurado esta en partida;
# si no, cae a replay historico.
python main_orchestrator_test.py

# Usa Spectator-V5: partida activa real de una cuenta configurada.
# No requiere que TU cliente este abierto, pero el jugador consultado si debe
# estar en una partida activa.
python main_orchestrator_test.py --source spectator

# Replay historico local: no requiere cliente abierto ni partida activa.
# Usa la DB/CSVs ya ingestado(s) y simula un minuto de partida.
python main_orchestrator_test.py --source replay --minute 15

# Replay de una partida especifica guardada en la DB.
python main_orchestrator_test.py --source replay --match-id LA1_1708686759 --minute 22

# Dashboard completo en modo prueba.
python main_orchestrator_test.py --serve --port 8001
```

Limites importantes:
- `replay` es 100% offline una vez que existe historial local; escala stats
  finales para probar UI, ML y recomendaciones, pero no representa una partida
  viva real.
- `spectator` usa Riot API publica para composiciones reales de una partida
  activa, pero Riot no expone fuera del cliente datos como oro, items, kills o
  niveles en vivo; esos campos se muestran como no disponibles o inferidos.
- Solo el Live Client Data API local puede dar el estado detallado durante tu
  propia partida.

### Frontend en modo desarrollo (hot reload)
```powershell
# terminal 1
python main_orchestrator.py --mode app
# terminal 2
cd frontend
npm run dev        # http://localhost:5173 (proxy /api -> :8000)
```

### Tests
```powershell
pip install pytest
pytest tests/ -v
```

### API directa
Con el server corriendo: `http://127.0.0.1:8000/api/docs` (Swagger).
Endpoints: `/api/player/profile`, `/api/player/history`, `/api/live/status`, `/api/live/game`, `/api/live/recommendations`, `/api/chat` (POST), `/api/review/matches`, `/api/review/match/{id}`, `/api/review/patterns`.

---

## Empaquetar como .exe

```powershell
pip install pyinstaller
cd frontend && npm run build && cd ..      # el .exe incluye el frontend compilado
pyinstaller lol_recommender.spec
```

El ejecutable queda en `dist/LoLRecommender.exe`. Al abrirlo levanta el servidor local, imprime la URL y abre el navegador.

**Importante sobre la API key:** el `.exe` NO contiene tu key. Coloca un archivo `.env` **junto al .exe** (o define `RIOT_API_KEY` como variable de entorno) antes de ejecutarlo. Si compartes el `.exe`, jamás incluyas tu `.env`.

## Cómo funciona el ML (honesto por diseño)

- **Features del modelo entrenado**: solo pregame (atributos oficiales de campeón propio/rival vía Data Dragon, rol, mezcla de daño de ambas composiciones). Son las únicas comparables 1:1 entre historial e inferencia en vivo → sin data leakage.
- **Estado en vivo**: baseline heurístico con lo que la Live Client API sí expone (kills, niveles, CS, torres, dragones, barones por eventos). Pesos conservadores.
- **Mezcla**: la probabilidad final pondera el estado en vivo según el minuto de juego (0% al inicio → 65% máx. al minuto 25).
- **Similitud**: jerarquía de 5 niveles (campeón+rival+rol+cola+patch → … → global por rol), siempre reportando nivel usado, muestra y confianza (alta ≥20, media ≥8, baja <8).
- **Estilo de jugador**: KMeans k=3 con muestra ≥15, si no heurística por umbrales; siempre con evidencia.
- Toda recomendación incluye explicación legible con su evidencia, y advertencia explícita cuando la muestra es chica.

## Preparación para Google Cloud (futuro)

- `security/secrets.py`: punto único de secretos; listo para conectar Secret Manager.
- `security/auth_stub.py`: middleware ya enganchado (passthrough). Para activar Google OAuth: implementar `verify_token` y `AUTH_ENABLED=true`. No se implementó OAuth completo a propósito; la arquitectura queda lista.

## Cumplimiento de políticas de Riot

- Solo se usan APIs oficiales (Riot API, Live Client Data API, Data Dragon).
- Rate limiting local (20/s, 100/2min) + respeto de `Retry-After`.
- No expone información que el juego no muestre a un jugador (nada de vida/oro enemigo oculto, cooldowns del rival, etc.). No otorga ventajas injustas.
- Las recomendaciones son opciones razonadas con evidencia, no decisiones absolutas.
- Este proyecto no está avalado por Riot Games.

## Limitaciones actuales

1. **Muestra chica**: con los 10 matches seed el modelo entrena en modo demostración; se necesitan cientos de partidas para métricas estables (la app lo advierte en cada estimación).
2. Live Client Data API no expone oro/vida de enemigos ni timers de respawn de objetivos → esas señales no se usan.
3. El orden de compra de items (timeline de Match-V5) no se ingesta aún; las recomendaciones de items combinan el **modelo por campeón** (`item_model`, entrenado con builds reales del campeón en la liga objetivo — es la fuente con más peso), el árbol de compra de Data Dragon, counters por tipo de daño enemigo e items finales de victorias similares. Los items no comprables (línea de misión de soporte: Brújula Rúnica y sus evoluciones, que se mejoran solas) se filtran vía el flag `purchasable` de Data Dragon.
4. Sin partida activa, el rival directo no puede identificarse (Spectator-V5 no trae roles confiables).
5. El chatbot es determinista por intents (sin LLM); `chat/prompt_builder.py` deja la integración lista.
6. Development key de Riot expira cada 24 h; hay que renovarla.
7. El scout del rival descarga máx. 10 partidas (rate limits) y se cachea 10 min.

## Mejoras futuras

- Ingesta de timeline (Match-V5 `/timeline`) para orden de compras y métricas tempranas (oro@10, xp@10).
- Gradient Boosting (LightGBM/XGBoost) y calibración de probabilidades cuando haya muestra suficiente.
- Modelo de ranking para items/lanes (learning-to-rank) en lugar de frecuencias.
- Integración LLM opcional en el chat (Claude/local) usando `prompt_builder`.
- OAuth con Google + despliegue Cloud Run + Secret Manager.
- Descarga masiva de historial (paginación >100 partidas) y auto-reentrenamiento programado.
- ~~Overlay opcional en partida (respetando políticas de Riot)~~ → implementado: `--mode overlay`.
