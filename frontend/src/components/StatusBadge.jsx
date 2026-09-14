// Estados globales: cargando, backend caido, sin partida, en vivo, etc.
export default function StatusBadge({ backendUp, liveStatus }) {
  if (backendUp === null) return <span className="badge off">Cargando...</span>
  if (backendUp === false) return <span className="badge err">Backend offline</span>
  if (!liveStatus) return <span className="badge off">Estado desconocido</span>
  if (liveStatus.live_client_available)
    return <span className="badge ok">En partida (Live Client)</span>
  if (liveStatus.spectator_active_game)
    return <span className="badge warn">Partida detectada - Live Client no disponible</span>
  return <span className="badge off">Sin partida activa</span>
}
