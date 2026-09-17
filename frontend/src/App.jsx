import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api/client.js'
import Dashboard from './views/Dashboard.jsx'

const DEFAULT_REFRESH_MS = 15000
const PUBLIC_DEMO = import.meta.env.VITE_PUBLIC_DEMO === 'true'

export default function App() {
  const [backendUp, setBackendUp] = useState(null)
  const [profile, setProfile] = useState(null)
  const [history, setHistory] = useState([])
  const [liveStatus, setLiveStatus] = useState(null)
  const [liveGame, setLiveGame] = useState(null)
  const [recommendations, setRecommendations] = useState(null)
  const [itemStyle, setItemStyle] = useState('neutral')
  const refreshMs = useRef(DEFAULT_REFRESH_MS)

  const loadStatic = useCallback(async () => {
    try {
      await api.health()
      setBackendUp(true)
    } catch {
      setBackendUp(false)
      return
    }
    const safe = (promise, setter) => promise.then(setter).catch(() => {})
    await Promise.all([
      safe(api.playerProfile(), setProfile),
      safe(api.playerHistory(20), setHistory),
    ])
  }, [])

  const loadLive = useCallback(async () => {
    try {
      const status = await api.liveStatus(!PUBLIC_DEMO)
      setLiveStatus(status)
      setBackendUp(true)
      if (status.refresh_seconds) {
        const configured = status.refresh_seconds * 1000
        refreshMs.current = !PUBLIC_DEMO && status.in_game && !status.live_client_available
          ? Math.min(configured, 3000)
          : configured
      }
      const [game, recs] = await Promise.all([
        api.liveGame().catch(() => null),
        api.liveRecommendations(itemStyle).catch(() => null),
      ])
      setLiveGame(game)
      setRecommendations(recs)
    } catch {
      setBackendUp(false)
    }
  }, [itemStyle])

  useEffect(() => {
    loadStatic()
    loadLive()
    let timer
    const tick = () => {
      timer = setTimeout(async () => {
        await loadLive()
        tick()
      }, refreshMs.current)
    }
    tick()
    return () => clearTimeout(timer)
  }, [loadStatic, loadLive])

  return (
    <Dashboard
      backendUp={backendUp}
      publicDemo={PUBLIC_DEMO}
      profile={profile}
      history={history}
      liveStatus={liveStatus}
      liveGame={liveGame}
      recommendations={recommendations}
      itemStyle={itemStyle}
      onItemStyleChange={setItemStyle}
    />
  )
}
