import StatusBadge from './StatusBadge.jsx'

function ChampFace({ player, fallbackLabel }) {
  if (!player) {
    return (
      <div className="champ-face">
        <div className="placeholder">?</div>
        <div>
          <div className="name">{fallbackLabel}</div>
          <div className="sub">no identificado</div>
        </div>
      </div>
    )
  }
  const dmg = player.damage_profile
  return (
    <div className="champ-face">
      {player.champion_image_url ? (
        <img src={player.champion_image_url} alt={player.champion} />
      ) : (
        <div className="placeholder">?</div>
      )}
      <div>
        <div className="name">{player.champion}</div>
        <div className="sub">
          {player.riot_id}
          {player.position ? ` - ${player.position}` : ''}
        </div>
        {dmg && (
          <div className="sub">
            <span className="dmg-physical">{Math.round(dmg.physical * 100)}% fis</span>
            {' / '}
            <span className="dmg-magic">{Math.round(dmg.magic * 100)}% mag</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default function TopPanel({ backendUp, profile, liveStatus, liveGame }) {
  const me = liveGame?.me
  const rival = liveGame?.direct_rival
  const ranked = profile?.ranked_entries?.find((e) => e.queue_type === 'RANKED_SOLO_5x5')

  return (
    <header className="top-panel">
      <div>
        <div className="brand">
          LoL <span>Recommender</span>
        </div>
        <div className="sub" style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
          {profile?.riot_id || '...'}
          {ranked && ` - ${ranked.tier} ${ranked.rank} (${ranked.league_points} LP)`}
          {profile?.main_role && ` - ${profile.main_role}`}
        </div>
      </div>

      {liveGame?.in_game ? (
        <>
          <ChampFace player={me} fallbackLabel="Tu campeon" />
          <div className="vs-divider">VS</div>
          <ChampFace player={rival} fallbackLabel="Rival directo" />
        </>
      ) : (
        <div style={{ color: 'var(--text-faint)', fontSize: '0.85rem' }}>
          Abre una partida para ver tu campeon y rival directo
        </div>
      )}

      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
        {liveGame?.in_game && liveGame.game_time_seconds != null && (
          <span className="pill">
            {Math.floor(liveGame.game_time_seconds / 60)}:
            {String(Math.floor(liveGame.game_time_seconds % 60)).padStart(2, '0')}
          </span>
        )}
        <StatusBadge backendUp={backendUp} liveStatus={liveStatus} />
      </div>
    </header>
  )
}
