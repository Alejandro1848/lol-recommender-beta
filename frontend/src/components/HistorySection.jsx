import Card from './Card.jsx'

export default function HistorySection({ history }) {
  return (
    <Card title="Historial reciente (ranked)">
      {history?.length ? (
        <div style={{ overflowX: 'auto' }}>
          <table className="history">
            <thead>
              <tr>
                <th>Campeón</th>
                <th>Rol</th>
                <th>Rival</th>
                <th>Resultado</th>
                <th>KDA</th>
                <th>CS/min</th>
                <th>Oro/min</th>
                <th>Daño/min</th>
                <th>KP</th>
                <th>Duración</th>
              </tr>
            </thead>
            <tbody>
              {history.map((m) => (
                <tr key={m.match_id}>
                  <td>
                    {m.champion_image_url && <img src={m.champion_image_url} alt="" />}
                    {m.champion}
                  </td>
                  <td>{m.role || '—'}</td>
                  <td>{m.opponent_champion || '—'}</td>
                  <td className={m.win ? 'result-win' : 'result-loss'}>
                    {m.win ? 'Victoria' : 'Derrota'}
                  </td>
                  <td>
                    {m.kills}/{m.deaths}/{m.assists} ({m.kda})
                  </td>
                  <td>{m.cs_per_min ?? '—'}</td>
                  <td>{m.gold_per_min ?? '—'}</td>
                  <td>{m.damage_per_min ?? '—'}</td>
                  <td>{m.kill_participation != null ? `${Math.round(m.kill_participation * 100)}%` : '—'}</td>
                  <td>{m.duration_min != null ? `${m.duration_min}m` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          Sin historial local. Ejecuta la ingesta: <code>python main_orchestrator.py --mode ingest-history</code>
        </div>
      )}
    </Card>
  )
}
