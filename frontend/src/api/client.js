// Cliente HTTP del frontend. Solo habla con el backend local (/api).
// La API key de Riot vive exclusivamente en el backend: aqui jamas se ve.

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    let detail = body
    try { detail = JSON.parse(body)?.detail || body } catch { /* respuesta no JSON */ }
    throw new Error(`API ${response.status}: ${String(detail).slice(0, 200)}`)
  }
  return response.json()
}

export const api = {
  health: () => request('/api/health'),
  playerProfile: () => request('/api/player/profile'),
  playerHistory: (limit = 20) => request(`/api/player/history?limit=${limit}`),
  liveStatus: (force = false) => request(`/api/live/status${force ? '?force=true' : ''}`),
  liveGame: () => request('/api/live/game'),
  liveRecommendations: (itemStyle = 'neutral') =>
    request(`/api/live/recommendations?item_style=${encodeURIComponent(itemStyle)}`),
  playerLookup: (riotId) =>
    request(`/api/player/lookup?riot_id=${encodeURIComponent(riotId)}`),
  chat: (question) =>
    request('/api/chat', { method: 'POST', body: JSON.stringify({ question }) }),
  proactiveChat: () => request('/api/chat/proactive'),
  reviewMatches: (limit = 10) => request(`/api/review/matches?limit=${limit}`),
  reviewMatch: (matchId) =>
    request(`/api/review/match/${encodeURIComponent(matchId)}`),
  reviewPatterns: (limit = 20) => request(`/api/review/patterns?limit=${limit}`),
  coachOptions: () => request('/api/coach-ai/options'),
  coachEstimate: (options) => request('/api/coach-ai/estimate', {
    method: 'POST', body: JSON.stringify(options),
  }),
  startCoachJob: (options) => request('/api/coach-ai/jobs', {
    method: 'POST', body: JSON.stringify(options),
  }),
  coachJob: (jobId) => request(`/api/coach-ai/jobs/${encodeURIComponent(jobId)}`),
}
