// Primera pantalla = dashboard funcional (no landing page).
import TopPanel from '../components/TopPanel.jsx'
import WinProbability from '../components/WinProbability.jsx'
import RecommendationList from '../components/RecommendationList.jsx'
import LiveMatchPanel from '../components/LiveMatchPanel.jsx'
import ChatPanel from '../components/ChatPanel.jsx'
import HistorySection from '../components/HistorySection.jsx'
import PlayerLookup from '../components/PlayerLookup.jsx'
import PostGameReview from '../components/PostGameReview.jsx'
import { PlayerMetrics, SimilaritySection } from '../components/MetricsSection.jsx'
import CoachAISettings from '../components/CoachAISettings.jsx'

export default function Dashboard({
  backendUp,
  publicDemo = false,
  profile,
  history,
  liveStatus,
  liveGame,
  recommendations,
  itemStyle,
  onItemStyleChange,
}) {
  const inGame = Boolean(recommendations?.in_game)
  const styles = [
    ['defensivo', 'Defensivo'],
    ['conservador', 'Conservador'],
    ['neutral', 'Neutral'],
    ['agresivo', 'Agresivo'],
  ]

  return (
    <div className="layout">
      <div className="main-column">
        {publicDemo && (
          <div className="notice">
            Demo pública: partida histórica simulada y compartida entre visitantes.
            Los datos no corresponden a una partida en vivo. El entrenamiento está deshabilitado.
          </div>
        )}
        <TopPanel
          backendUp={backendUp}
          profile={profile}
          liveStatus={liveStatus}
          liveGame={liveGame}
        />

        {backendUp === false && (
          <div className="notice err">
            {publicDemo ? 'El servidor no está disponible. Intenta recargar en unos momentos.' : (
              <>No hay conexion con el backend local. Ejecuta: <code>python main_orchestrator.py --mode app</code></>
            )}
          </div>
        )}

        {backendUp && liveStatus?.message && (
          <div className={liveStatus.live_client_available ? 'notice' : 'notice warn'}>
            {liveStatus.message}
          </div>
        )}

        {recommendations?.message && (
          <div className="notice">{recommendations.message}</div>
        )}
        {recommendations?.warnings?.map((w, i) => (
          <div key={i} className="notice warn">{w}</div>
        ))}

        <LiveMatchPanel liveGame={liveGame} liveStatus={liveStatus} />

        <div className="style-strip">
          <span>Estilo de compra</span>
          {styles.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={itemStyle === value ? 'active' : ''}
              onClick={() => onItemStyleChange?.(value)}
            >
              {label}
            </button>
          ))}
        </div>
        {!publicDemo && <CoachAISettings />}

        {/* Panel central: recomendaciones principales */}
        <div className="card-grid">
          <WinProbability
            winProbability={recommendations?.win_probability}
            inGame={inGame}
          />
          <RecommendationList
            title="Items recomendados"
            recs={recommendations?.items}
            emptyText="Sin recomendaciones de items por ahora."
          />
        </div>
        <div className="card-grid">
          <RecommendationList
            title="Lineas para gank"
            recs={recommendations?.ganks}
            emptyText="Se necesita partida activa para priorizar lineas."
          />
          <RecommendationList
            title="Objetivos"
            recs={recommendations?.objectives}
            emptyText="Se necesita partida activa para recomendar objetivos."
          />
        </div>

        {/* Analiticas de cualquier jugador (no requiere partida activa) */}
        {!publicDemo && <PlayerLookup />}

        {/* Revision post-partida: curva de probabilidad + puntos de inflexion */}
        <PostGameReview />

        {/* Secciones inferiores */}
        <div className="card-grid">
          <PlayerMetrics profile={profile} />
          <SimilaritySection similarity={recommendations?.similarity} />
        </div>
        <HistorySection history={history} />
      </div>

      {/* Panel lateral: chatbot */}
      <aside className="side-column">
        <ChatPanel />
      </aside>
    </div>
  )
}
