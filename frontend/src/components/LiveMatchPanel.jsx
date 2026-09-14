import Card from './Card.jsx'

const TEAM_LABELS = {
  ORDER: 'Azul',
  CHAOS: 'Rojo',
}

function formatTime(seconds) {
  if (seconds == null) return '--:--'
  const mins = Math.floor(seconds / 60)
  const secs = String(Math.floor(seconds % 60)).padStart(2, '0')
  return `${mins}:${secs}`
}

function ItemSlots({ items = [] }) {
  const filled = [...items]
  while (filled.length < 6) filled.push(null)
  return (
    <div className="live-items" aria-label="Items">
      {filled.slice(0, 6).map((item, index) => (
        item?.image_url ? (
          <img key={`${item.id}-${index}`} src={item.image_url} alt={item.name || 'item'} title={item.name} />
        ) : (
          <span key={`empty-${index}`} className="item-empty" />
        )
      ))}
    </div>
  )
}

function PlayerRow({ player, isMe }) {
  if (!player) return null
  return (
    <div className={`live-player ${isMe ? 'is-me' : ''}`}>
      {player.champion_image_url ? (
        <img className="champ-icon" src={player.champion_image_url} alt={player.champion} />
      ) : (
        <span className="champ-icon placeholder">?</span>
      )}
      <div className="live-player-main">
        <div className="live-player-name">
          <span>{player.champion}</span>
          {isMe && <span className="pill">tu</span>}
        </div>
        <div className="sub">
          {player.riot_id}
          {player.position ? ` - ${player.position}` : ''}
        </div>
      </div>
      <div className="live-stat">
        <span>KDA</span>
        <b>{player.kills}/{player.deaths}/{player.assists}</b>
      </div>
      <div className="live-stat">
        <span>Niv</span>
        <b>{player.level ?? '-'}</b>
      </div>
      <div className="live-stat">
        <span>CS</span>
        <b>{player.creep_score ?? '-'}</b>
      </div>
      <div className="live-state">
        {player.is_dead ? `muerto ${Math.round(player.respawn_timer || 0)}s` : 'vivo'}
      </div>
      <ItemSlots items={player.items} />
    </div>
  )
}

function TeamBlock({ title, players, me }) {
  return (
    <div className="live-team">
      <div className="live-team-title">{title}</div>
      {players.map((player) => (
        <PlayerRow
          key={`${player.riot_id}-${player.champion}`}
          player={player}
          isMe={me?.riot_id === player.riot_id}
        />
      ))}
    </div>
  )
}

export default function LiveMatchPanel({ liveGame, liveStatus }) {
  if (!liveGame?.in_game) {
    return null
  }

  const me = liveGame.me
  const allies = [me, ...(liveGame.allies || [])].filter(Boolean)
  const enemies = liveGame.enemies || []
  const gold = liveGame.active_player?.current_gold
  const sourceLabel = liveGame.live_signals_available ? 'Live Client activo' : 'Solo composicion'
  const myTeamLabel = TEAM_LABELS[me?.team] || me?.team || 'Tu equipo'

  return (
    <Card title="Partida en vivo">
      <div className="live-summary">
        <span className="pill">{sourceLabel}</span>
        <span>Tiempo {formatTime(liveGame.game_time_seconds)}</span>
        <span>{myTeamLabel}</span>
        {gold != null && <span>Oro actual {Math.floor(gold)}</span>}
      </div>
      {!liveGame.live_signals_available && (
        <div className="notice warn live-note">
          {liveStatus?.message || 'La composicion es real, pero faltan datos locales del Live Client.'}
        </div>
      )}
      <div className="live-board">
        <TeamBlock title="Tu equipo" players={allies} me={me} />
        <TeamBlock title="Enemigos" players={enemies} me={me} />
      </div>
    </Card>
  )
}
