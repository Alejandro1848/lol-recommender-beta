import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client.js'

const EMPTY_OPTIONS = {
  champion: '', target_tier: 'GOLD', ingest_ladder: false,
  ingest_timelines: true, train_models: true, force_retrain: true,
  target_records: 20000, per_player: 20, days: 30,
  max_players: 300, timeline_matches: 300,
}

function Check({ checked, onChange, title, detail }) {
  return (
    <label className="coach-check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span><b>{title}</b><small>{detail}</small></span>
    </label>
  )
}

function NumberField({ label, value, min, max, onChange, hint }) {
  return (
    <label className="coach-field">
      <span>{label}</span>
      <input
        type="number" value={value} min={min} max={max}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {hint && <small>{hint}</small>}
    </label>
  )
}

export default function CoachAISettings() {
  const [open, setOpen] = useState(false)
  const [catalog, setCatalog] = useState({ champions: [], tiers: [], riot_api_configured: false })
  const [options, setOptions] = useState(EMPTY_OPTIONS)
  const [championText, setChampionText] = useState('')
  const [showChampions, setShowChampions] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [estimate, setEstimate] = useState(null)
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')

  const setOption = (key, value) => setOptions((current) => ({ ...current, [key]: value }))

  useEffect(() => {
    if (!open) return
    api.coachOptions().then((data) => {
      setCatalog(data)
      setOptions((current) => ({ ...current, ...data.defaults }))
      if (data.active_job) setJob(data.active_job)
    }).catch((err) => setError(err.message))
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const timer = setTimeout(() => {
      api.coachEstimate(options).then(setEstimate).catch(() => {})
    }, 250)
    return () => clearTimeout(timer)
  }, [open, options])

  useEffect(() => {
    if (!job?.id || !['queued', 'running'].includes(job.status)) return undefined
    const timer = setInterval(() => {
      api.coachJob(job.id).then(setJob).catch((err) => setError(err.message))
    }, 2000)
    return () => clearInterval(timer)
  }, [job?.id, job?.status])

  const filteredChampions = useMemo(() => {
    const prefix = championText.trim().toLocaleLowerCase('es')
    const values = catalog.champions || []
    return (prefix
      ? values.filter((champion) => champion.name.toLocaleLowerCase('es').startsWith(prefix))
      : values
    ).slice(0, 30)
  }, [catalog.champions, championText])

  const chooseChampion = (champion) => {
    setChampionText(champion.name)
    setOption('champion', champion.value)
    setShowChampions(false)
  }

  const changeChampionText = (value) => {
    setChampionText(value)
    const exact = (catalog.champions || []).find(
      (champion) => champion.name.toLocaleLowerCase('es') === value.toLocaleLowerCase('es'),
    )
    setOption('champion', exact?.value || '')
    setShowChampions(true)
  }

  const start = async () => {
    setError('')
    try {
      const started = await api.startCoachJob(options)
      setJob(started)
    } catch (err) {
      setError(err.message)
    }
  }

  const running = ['queued', 'running'].includes(job?.status)
  const selectedSteps = ['ingest_ladder', 'ingest_timelines', 'train_models'].filter((key) => options[key]).length
  const progress = job?.steps?.length
    ? Math.round(((job.status === 'completed' ? job.steps.length : Math.max(0, job.step_index - 1)) / job.steps.length) * 100)
    : 0

  return (
    <>
      <button type="button" className="coach-launch" onClick={() => setOpen(true)}>
        Ajuste de couch AI
      </button>
      {open && (
        <div className="coach-backdrop" role="presentation" onMouseDown={() => setOpen(false)}>
          <section className="coach-modal" role="dialog" aria-modal="true" aria-labelledby="coach-title" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <div>
                <h2 id="coach-title">Ajuste de couch AI</h2>
                <p>Prepara datos y modelos sin usar comandos.</p>
              </div>
              <button className="coach-close" type="button" onClick={() => setOpen(false)} aria-label="Cerrar">×</button>
            </header>

            {!catalog.riot_api_configured && (
              <div className="notice warn">La ingesta necesita una RIOT_API_KEY vigente en .env. El entrenamiento con datos locales sí puede ejecutarse.</div>
            )}

            <div className="coach-grid">
              <label className="coach-field champion-picker">
                <span>Campeón</span>
                <input
                  value={championText} placeholder="Escribe el nombre de tu campéon"
                  autoComplete="off" onFocus={() => setShowChampions(true)}
                  onBlur={() => setTimeout(() => setShowChampions(false), 150)}
                  onChange={(e) => changeChampionText(e.target.value)}
                />
                {showChampions && (
                  <div className="champion-options">
                    {filteredChampions.length ? filteredChampions.map((champion) => (
                      <button type="button" key={champion.value} onMouseDown={() => chooseChampion(champion)}>
                        {champion.image_url && <img src={champion.image_url} alt="" />}
                        {champion.name}
                      </button>
                    )) : <div className="empty">No hay campeones con ese prefijo.</div>}
                  </div>
                )}
              </label>
              <label className="coach-field">
                <span>Liga objetivo</span>
                <select value={options.target_tier} onChange={(e) => setOption('target_tier', e.target.value)}>
                  {(catalog.tiers || []).map((tier) => <option key={tier}>{tier}</option>)}
                </select>
              </label>
            </div>

            <div className="coach-processes">
              <Check
                checked={options.ingest_ladder} onChange={(value) => setOption('ingest_ladder', value)}
                title="Actualizar datos de liga" detail="Descarga una muestra homogénea; es el paso más lento."
              />
              <Check
                checked={options.ingest_timelines} onChange={(value) => setOption('ingest_timelines', value)}
                title="Descargar datos in-game" detail="Obtiene timelines del campeón para el modelo en vivo."
              />
              <Check
                checked={options.train_models} onChange={(value) => setOption('train_models', value)}
                title="Reentrenar modelos" detail="Actualiza objetos y probabilidad in-game."
              />
            </div>

            <button type="button" className="advanced-toggle" onClick={() => setAdvanced((value) => !value)}>
              {advanced ? 'Ocultar ajustes avanzados' : 'Ajustes avanzados'}
            </button>
            {advanced && (
              <div className="advanced-panel">
                <NumberField label="Registros de participantes" value={options.target_records} min={10} max={200000} onChange={(v) => setOption('target_records', v)} hint="20,000 ≈ 2,000 partidas completas." />
                <NumberField label="Partidas por jugador" value={options.per_player} min={1} max={100} onChange={(v) => setOption('per_player', v)} />
                <NumberField label="Ventana (días)" value={options.days} min={1} max={90} onChange={(v) => setOption('days', v)} />
                <NumberField label="Máximo de jugadores" value={options.max_players} min={1} max={2000} onChange={(v) => setOption('max_players', v)} />
                <NumberField label="Timelines máximas" value={options.timeline_matches} min={1} max={3000} onChange={(v) => setOption('timeline_matches', v)} />
                <Check checked={options.force_retrain} onChange={(value) => setOption('force_retrain', value)} title="Forzar reentrenamiento" detail="Reemplaza el modelo aunque el parche actual ya esté cubierto." />
              </div>
            )}

            <div className="coach-estimate">
              <b>Tiempo estimado: {estimate?.label || 'calculando…'}</b>
              <span>{estimate?.note}</span>
              {estimate?.parts?.length > 0 && (
                <div>{estimate.parts.map((part) => `${part.step}: ${part.minutes_low}-${part.minutes_high} min`).join(' · ')}</div>
              )}
            </div>

            {job && (
              <div className={`coach-job ${job.status}`}>
                <div><b>{job.current_step}</b><span>{job.message}</span></div>
                <div className="coach-progress"><span style={{ width: `${progress}%` }} /></div>
                <small>{progress}% · {job.elapsed_seconds || 0} s transcurridos</small>
              </div>
            )}
            {error && <div className="notice err">{error}</div>}

            <footer>
              <button type="button" className="btn secondary" onClick={() => setOpen(false)}>Cerrar</button>
              <button type="button" className="btn" disabled={running || !options.champion || selectedSteps === 0} onClick={start}>
                {running ? 'Proceso en curso…' : 'Iniciar preparación'}
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  )
}
