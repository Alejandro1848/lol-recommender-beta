// Revision post-partida: "¿donde se perdio?".
// Reconstruye la curva de probabilidad de victoria de cada partida jugada
// (misma mezcla que la inferencia en vivo), marca los puntos de inflexion
// y muestra el resumen de 5 lineas + patrones semanales agregados.
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client.js'
import Card from './Card.jsx'

const CHART = { w: 640, h: 220, left: 40, right: 14, top: 14, bottom: 26 }

function scales(curve) {
  const maxMinute = Math.max(20, ...curve.map((p) => p.minute))
  const x = (minute) =>
    CHART.left + ((CHART.w - CHART.left - CHART.right) * minute) / maxMinute
  const y = (prob) =>
    CHART.top + (CHART.h - CHART.top - CHART.bottom) * (1 - prob)
  return { x, y, maxMinute }
}

function CurveChart({ curve, turningPoints }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const { x, y, maxMinute } = useMemo(() => scales(curve), [curve])

  if (!curve?.length) return null

  const line = curve
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.minute).toFixed(1)},${y(p.probability).toFixed(1)}`)
    .join(' ')
  const area =
    `${line} L${x(curve[curve.length - 1].minute).toFixed(1)},${y(0).toFixed(1)}` +
    ` L${x(curve[0].minute).toFixed(1)},${y(0).toFixed(1)} Z`
  const minuteTicks = []
  for (let m = 0; m <= maxMinute; m += 5) minuteTicks.push(m)

  function onMove(event) {
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const px = ((event.clientX - rect.left) / rect.width) * CHART.w
    let nearest = curve[0]
    for (const p of curve) {
      if (Math.abs(x(p.minute) - px) < Math.abs(x(nearest.minute) - px)) nearest = p
    }
    setHover(nearest)
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${CHART.w} ${CHART.h}`}
      className="review-chart"
      role="img"
      aria-label="Curva de probabilidad de victoria por minuto"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      {/* rejilla recesiva + eje Y en % */}
      {[0, 0.25, 0.5, 0.75, 1].map((p) => (
        <g key={p}>
          <line
            x1={CHART.left} x2={CHART.w - CHART.right} y1={y(p)} y2={y(p)}
            stroke="var(--card-border)" strokeWidth="1"
            strokeDasharray={p === 0.5 ? '5 4' : undefined}
            opacity={p === 0.5 ? 0.9 : 0.45}
          />
          <text x={CHART.left - 6} y={y(p) + 3} textAnchor="end" className="tick">
            {Math.round(p * 100)}%
          </text>
        </g>
      ))}
      {minuteTicks.map((m) => (
        <text key={m} x={x(m)} y={CHART.h - 8} textAnchor="middle" className="tick">
          {m}
        </text>
      ))}

      <path d={area} fill="rgba(39, 121, 224, 0.12)" />
      <path d={line} fill="none" stroke="var(--blue)" strokeWidth="2" strokeLinejoin="round" />

      {/* puntos de inflexion: caida en rojo (▼), subida en verde (▲) */}
      {turningPoints?.map((tp, i) => {
        const drop = tp.direction === 'caida'
        const color = drop ? 'var(--red)' : 'var(--green)'
        const cx = x(tp.end_minute)
        const cy = y(tp.to_probability)
        return (
          <g key={i}>
            <circle cx={cx} cy={cy} r="6" fill={color} stroke="var(--card)" strokeWidth="2">
              <title>{tp.description}</title>
            </circle>
            <text x={cx} y={cy - 10} textAnchor="middle" className="tp-mark" fill={color}>
              {drop ? '▼' : '▲'}
            </text>
          </g>
        )
      })}

      {/* crosshair + tooltip al pasar el cursor */}
      {hover && (
        <g pointerEvents="none">
          <line
            x1={x(hover.minute)} x2={x(hover.minute)}
            y1={CHART.top} y2={CHART.h - CHART.bottom}
            stroke="var(--text-faint)" strokeWidth="1" strokeDasharray="3 3"
          />
          <circle cx={x(hover.minute)} cy={y(hover.probability)} r="4"
                  fill="var(--blue)" stroke="var(--card)" strokeWidth="2" />
          <g transform={`translate(${Math.min(x(hover.minute) + 8, CHART.w - 120)}, ${CHART.top + 2})`}>
            <rect width="112" height="22" rx="6" fill="var(--bg-elevated)"
                  stroke="var(--card-border)" />
            <text x="8" y="15" className="tooltip-text">
              min {hover.minute} · {Math.round(hover.probability * 100)}%
            </text>
          </g>
        </g>
      )}
    </svg>
  )
}

function PatternsPanel({ patterns, loading, onLoad }) {
  if (!patterns && !loading) {
    return (
      <button type="button" className="btn" onClick={onLoad}>
        Analizar patrones (últimas 20 partidas)
      </button>
    )
  }
  if (loading) return <div className="loading-shimmer" style={{ width: '60%' }} />
  if (!patterns.available) {
    return <div className="notice warn">{patterns.reason || 'Sin datos para patrones.'}</div>
  }
  return (
    <div className="patterns">
      <div className="patterns-stats">
        <span>
          <b>{patterns.games}</b> partidas · winrate{' '}
          <b>{Math.round((patterns.winrate ?? 0) * 100)}%</b>
        </span>
        <span className="result-loss"><b>{patterns.throws}</b> throw(s)</span>
        <span className="result-win"><b>{patterns.comebacks}</b> comeback(s)</span>
        {patterns.buckets?.map((b) => (
          <span key={b.label}>
            {b.label}: {b.games} partidas
            {b.winrate != null ? ` (${Math.round(b.winrate * 100)}% WR)` : ''}
          </span>
        ))}
      </div>
      <ul className="review-summary">
        {patterns.insights?.map((text, i) => <li key={i}>{text}</li>)}
      </ul>
      {patterns.warnings?.map((w, i) => (
        <div key={i} className="notice warn">{w}</div>
      ))}
    </div>
  )
}

export default function PostGameReview() {
  const [matches, setMatches] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [review, setReview] = useState(null)
  const [loadingReview, setLoadingReview] = useState(false)
  const [patterns, setPatterns] = useState(null)
  const [loadingPatterns, setLoadingPatterns] = useState(false)

  useEffect(() => {
    api.reviewMatches(10).then(setMatches).catch(() => setMatches([]))
  }, [])

  async function openReview(matchId) {
    if (loadingReview || matchId === selectedId) return
    setSelectedId(matchId)
    setLoadingReview(true)
    setReview(null)
    try {
      setReview(await api.reviewMatch(matchId))
    } catch {
      setReview({ available: false, reason: 'Error consultando la revisión al backend.' })
    } finally {
      setLoadingReview(false)
    }
  }

  async function loadPatterns() {
    setLoadingPatterns(true)
    try {
      setPatterns(await api.reviewPatterns(20))
    } catch {
      setPatterns({ available: false, reason: 'Error consultando los patrones al backend.' })
    } finally {
      setLoadingPatterns(false)
    }
  }

  return (
    <Card title="Revisión post-partida — ¿dónde se perdió?">
      {matches === null && <div className="loading-shimmer" style={{ width: '50%' }} />}
      {matches?.length === 0 && (
        <div className="empty">
          Sin partidas tuyas en el historial local. Ejecuta:{' '}
          <code>python main_orchestrator.py --mode ingest-history</code>
        </div>
      )}

      {matches?.length > 0 && (
        <>
          <div className="review-chips">
            {matches.map((m) => (
              <button
                key={m.match_id}
                type="button"
                className={`review-chip ${m.win ? 'win' : 'loss'} ${
                  m.match_id === selectedId ? 'active' : ''
                }`}
                onClick={() => openReview(m.match_id)}
                title={`${m.champion}${m.opponent_champion ? ` vs ${m.opponent_champion}` : ''}${
                  m.has_timeline ? '' : ' · descargará la timeline al abrirla'
                }`}
              >
                {m.champion_image_url && <img src={m.champion_image_url} alt="" />}
                <span>{m.champion}</span>
                <span className={m.win ? 'result-win' : 'result-loss'}>
                  {m.win ? 'V' : 'D'}
                </span>
              </button>
            ))}
          </div>

          {loadingReview && (
            <div className="review-loading">
              <div className="loading-shimmer" style={{ width: '80%' }} />
              <div className="loading-shimmer" style={{ width: '55%' }} />
              <span className="hint">
                Reconstruyendo la curva (si falta la timeline, se descarga de Riot: unos segundos)…
              </span>
            </div>
          )}

          {review && !review.available && (
            <div className="notice warn">{review.reason}</div>
          )}

          {review?.available && (
            <div className="review-body">
              <div className="review-header">
                <b>
                  {review.champion}
                  {review.opponent_champion ? ` vs ${review.opponent_champion}` : ''}
                </b>{' '}
                — {review.win ? 'Victoria' : 'Derrota'} · {review.kda} KDA
                {review.duration_min ? ` · ${review.duration_min} min` : ''}
                <span className="hint"> · método: {review.method}</span>
              </div>
              <CurveChart curve={review.curve} turningPoints={review.turning_points} />
              <ol className="review-summary">
                {review.summary_lines.map((line, i) => <li key={i}>{line}</li>)}
              </ol>
              {review.turning_points?.length > 1 && (
                <div className="tp-list">
                  {review.turning_points.map((tp, i) => (
                    <div key={i} className="tp-item">
                      <span className={tp.direction === 'caida' ? 'result-loss' : 'result-win'}>
                        {tp.direction === 'caida' ? '▼' : '▲'}
                      </span>{' '}
                      {tp.description}
                    </div>
                  ))}
                </div>
              )}
              {review.warnings?.map((w, i) => (
                <div key={i} className="notice">{w}</div>
              ))}
            </div>
          )}

          <div className="review-patterns-block">
            <h3>Patrones semanales</h3>
            <PatternsPanel
              patterns={patterns}
              loading={loadingPatterns}
              onLoad={loadPatterns}
            />
          </div>
        </>
      )}
    </Card>
  )
}
