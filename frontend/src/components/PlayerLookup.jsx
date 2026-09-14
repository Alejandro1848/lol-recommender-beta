// Analíticas de CUALQUIER jugador por Riot ID (estilo porofessor.gg):
// no requiere que el jugador esté jugando en esta máquina; el backend
// descarga sus partidas recientes de la Riot API bajo demanda.
import { useState } from 'react'
import { api } from '../api/client.js'
import Card from './Card.jsx'

function Stat({ label, value }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value ?? '—'}</div>
    </div>
  )
}

export default function PlayerLookup() {
  const [riotId, setRiotId] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function search(e) {
    e?.preventDefault()
    const query = riotId.trim()
    if (!query || busy) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await api.playerLookup(query)
      if (res.available) {
        setResult(res)
      } else {
        setError(res.reason || 'Jugador no encontrado.')
      }
    } catch {
      setError('No pude contactar al backend. ¿Está corriendo el servidor?')
    } finally {
      setBusy(false)
    }
  }

  const solo = result?.ranked_entries?.find((e) => e.queue_type === 'RANKED_SOLO_5x5')
  const form = result?.recent_form

  return (
    <Card title="Buscar jugador (analíticas de cualquier invocador)">
      <form className="inline-form" onSubmit={search}>
        <input
          value={riotId}
          onChange={(e) => setRiotId(e.target.value)}
          placeholder="Riot ID, ej. Faker#KR1"
          disabled={busy}
        />
        <button type="submit" className="btn" disabled={busy || !riotId.trim()}>
          {busy ? 'Buscando…' : 'Buscar'}
        </button>
      </form>
      <div style={{ marginTop: 8, fontSize: '0.72rem', color: 'var(--text-faint)' }}>
        Funciona para cualquier jugador de tu región, esté o no jugando en esta
        máquina: sus partidas se descargan de la Riot API.
      </div>

      {error && <div className="notice err" style={{ marginTop: 12 }}>{error}</div>}

      {result && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            {result.profile_icon_url && (
              <img
                src={result.profile_icon_url}
                alt=""
                style={{ width: 48, height: 48, borderRadius: 10, border: '2px solid var(--accent)' }}
              />
            )}
            <div>
              <div style={{ fontWeight: 600 }}>{result.riot_id}</div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>
                {result.summoner_level != null && `Nivel ${result.summoner_level} · `}
                {solo
                  ? `${solo.tier} ${solo.rank} (${solo.league_points} LP) · ${solo.wins}W/${solo.losses}L`
                  : 'Sin ranked solo/dúo visible'}
              </div>
            </div>
          </div>

          <div className="stat-grid">
            <Stat label="Partidas (muestra)" value={result.games || null} />
            <Stat
              label="Winrate reciente"
              value={form?.winrate != null ? `${Math.round(form.winrate * 100)}%` : null}
            />
            <Stat label="KDA reciente" value={form?.kda} />
            <Stat label="Rol principal" value={result.main_role} />
            <Stat label="Estilo" value={result.style?.label} />
          </div>

          {result.top_champions?.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-faint)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Campeones más jugados
              </div>
              {result.top_champions.map((c) => (
                <div className="rec" key={c.name}>
                  {c.image_url && <img className="icon" src={c.image_url} alt={c.name} />}
                  <div className="body">
                    <div className="title">{c.name}</div>
                    <div className="detail">
                      {c.games} partidas · winrate {Math.round(c.winrate * 100)}% · KDA {c.kda}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {result.warnings?.map((w, i) => (
            <div key={i} className="notice warn" style={{ marginTop: 8 }}>{w}</div>
          ))}
        </div>
      )}
    </Card>
  )
}
