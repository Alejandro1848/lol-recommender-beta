import Card from './Card.jsx'

function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value ?? '-'}</div>
    </div>
  )
}

export function PlayerMetrics({ profile }) {
  const style = profile?.style
  return (
    <Card title="Tus metricas recientes">
      {profile ? (
        <>
          <div className="stat-grid">
            <Stat
              label="Winrate reciente"
              value={
                profile.recent_winrate != null
                  ? `${Math.round(profile.recent_winrate * 100)}%`
                  : null
              }
            />
            <Stat label="Partidas" value={profile.recent_games || null} />
            <Stat label="Rol principal" value={profile.main_role} />
            <Stat label="Estilo" value={style?.label} />
          </div>
          {profile.top_champions?.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-faint)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Campeones mas jugados
              </div>
              {profile.top_champions.map((c) => (
                <div className="rec" key={c.name}>
                  {c.image_url && <img className="icon" src={c.image_url} alt={c.name} />}
                  <div className="body">
                    <div className="title">{c.name}</div>
                    <div className="detail">
                      {c.games} partidas - winrate {Math.round(c.winrate * 100)}% - KDA {c.kda}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {profile.warnings?.map((w, i) => (
            <div key={i} className="notice warn" style={{ marginTop: 8 }}>{w}</div>
          ))}
        </>
      ) : (
        <div className="loading-shimmer" />
      )}
    </Card>
  )
}

export function SimilaritySection({ similarity }) {
  return (
    <Card title="Similitud historica">
      {similarity && similarity.sample_size > 0 ? (
        <>
          <div className="stat-grid">
            <Stat label="Partidas similares" value={similarity.sample_size} />
            <Stat label="Nivel usado" value={similarity.level} />
            <Stat label="Confianza" value={similarity.confidence} />
          </div>
          <div style={{ marginTop: 10, fontSize: '0.8rem', color: 'var(--text-dim)' }}>
            {similarity.level_label}
          </div>
          {similarity.match_ids?.length > 0 && (
            <div style={{ marginTop: 8, fontSize: '0.7rem', color: 'var(--text-faint)' }}>
              muestra: {similarity.match_ids.slice(0, 5).join(', ')}
              {similarity.match_ids.length > 5 && '...'}
            </div>
          )}
        </>
      ) : (
        <div className="empty">
          Sin partida activa o sin partidas similares en el historial local.
        </div>
      )}
    </Card>
  )
}
