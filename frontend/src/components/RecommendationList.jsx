import Card from './Card.jsx'

function RecommendationRow({ rec }) {
  const cost = rec.extra?.gold_cost
  const gold = rec.extra?.current_gold
  const remaining = rec.extra?.remaining_gold
  const sources = rec.extra?.sources || []
  const reasons = rec.extra?.reasons || []
  const alternatives = rec.extra?.alternatives || []
  const performanceEvidence = rec.extra?.performance_evidence
  const recommendationRole = rec.extra?.recommendation_role
  const priority = rec.extra?.priority
  const bootWeights = rec.extra?.boot_weights

  return (
    <div className="rec">
      {rec.image_url && <img className="icon" src={rec.image_url} alt="" />}
      <div className="body">
        <div className="title">
          {rec.title}
          {recommendationRole === 'botas' && <span className="pill">botas únicas · 70/30</span>}
          {recommendationRole === 'principal' && <span className="pill">principal #{priority}</span>}
          {recommendationRole === 'situacional' && <span className="pill">situacional</span>}
          <span className={`pill conf-${rec.confidence}`}>{rec.confidence}</span>
          {rec.data_source && <span className="pill">{rec.data_source}</span>}
        </div>
        {rec.detail && <div className="detail">{rec.detail}</div>}
        <div className="explanation">{rec.explanation}</div>
        {(rec.sample_size != null || rec.similarity_label) && (
          <div className="meta">
            {rec.sample_size != null && `muestra: ${rec.sample_size} partidas`}
            {rec.similarity_label && ` - nivel: ${rec.similarity_label}`}
          </div>
        )}
        {(cost != null || gold != null) && (
          <div className="meta">
            {gold != null && `oro actual: ${Math.floor(gold)}`}
            {gold != null && cost != null && ' - '}
            {cost != null && `costo: ${cost}`}
            {remaining != null && ` - faltan: ${remaining}`}
          </div>
        )}
        {sources.length > 0 && (
          <div className="meta">fuentes: {sources.join(', ')}</div>
        )}
        {performanceEvidence && (
          <div className="meta">{performanceEvidence}</div>
        )}
        {bootWeights && (
          <div className="meta">
            peso de botas: 70% victorias históricas · 30% composición rival
          </div>
        )}
        {reasons.length > 0 && (
          <div className="meta">razones: {reasons.slice(0, 2).join('; ')}</div>
        )}
        {alternatives.length > 0 && (
          <div className="alt-items">
            {alternatives.map((item) => (
              <span key={item.name}>
                {item.image_url && <img src={item.image_url} alt="" />}
                {item.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function RecommendationList({ title, recs, emptyText }) {
  return (
    <Card title={title}>
      {recs?.length ? (
        recs.map((rec, i) => <RecommendationRow rec={rec} key={i} />)
      ) : (
        <div className="empty">{emptyText}</div>
      )}
    </Card>
  )
}
