import Card from './Card.jsx'

export default function WinProbability({ winProbability, inGame }) {
  if (!inGame || !winProbability || winProbability.probability == null) {
    return (
      <Card title="Probabilidad de victoria">
        <div className="empty">
          Sin partida activa no hay probabilidad que estimar.
        </div>
      </Card>
    )
  }
  const pct = Math.round(winProbability.probability * 100)
  return (
    <Card title="Probabilidad de victoria" className="winprob">
      <div className="value" style={{ color: pct >= 50 ? 'var(--green)' : 'var(--red)' }}>
        {pct}%
      </div>
      <div className="bar">
        <div style={{ width: `${pct}%` }} />
      </div>
      <div style={{ marginBottom: 8 }}>
        <span className={`pill conf-${winProbability.confidence}`}>
          confianza {winProbability.confidence}
        </span>
      </div>
      <div style={{ fontSize: '0.72rem', color: 'var(--text-faint)', marginBottom: 8 }}>
        {winProbability.method}
      </div>
      {winProbability.top_factors?.slice(0, 5).map((f, i) => (
        <div className="factor" key={i}>
          <span>{f.name || f.feature}</span>
          <b style={{ color: (f.contribution ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
            {(f.contribution ?? 0) >= 0 ? '+' : ''}
            {(f.contribution ?? 0).toFixed(2)}
          </b>
        </div>
      ))}
      {winProbability.warnings?.map((w, i) => (
        <div key={i} className="notice warn" style={{ marginTop: 8 }}>{w}</div>
      ))}
    </Card>
  )
}
