import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client.js'
import Card from './Card.jsx'

const SUGGESTIONS = [
  '¿Qué ítem debo comprar ahora?',
  '¿Qué línea debo priorizar?',
  '¿Qué objetivo deberíamos priorizar?',
  '¿Qué es el tempo?',
  '¿Para qué sirven los monstruos del abismo?',
  '¿Quién es Jhin en el lore?',
]

const PROACTIVE_MS = 30000

export default function ChatPanel() {
  const [messages, setMessages] = useState([
    {
      role: 'bot',
      text: 'Hola. Te daré alertas de coach cuando haya datos suficientes. También puedes preguntarme por ítems, líneas, objetivos, conceptos básicos, lore o jugadores por Riot ID.',
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const messagesRef = useRef(null)
  const bottomRef = useRef(null)
  const lastProactive = useRef('')
  const shouldStickToBottom = useRef(true)
  const forceNextScroll = useRef(false)

  useEffect(() => {
    if (!shouldStickToBottom.current && !forceNextScroll.current) return
    bottomRef.current?.scrollIntoView({ behavior: forceNextScroll.current ? 'smooth' : 'auto' })
    forceNextScroll.current = false
  }, [messages])

  function updateStickiness() {
    const el = messagesRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    shouldStickToBottom.current = distanceFromBottom < 56
  }

  function requestManualScroll() {
    forceNextScroll.current = true
    shouldStickToBottom.current = true
  }

  useEffect(() => {
    let alive = true
    async function tick() {
      try {
        const res = await api.proactiveChat()
        if (!alive || !res.data_available || !res.answer || res.answer === lastProactive.current) return
        lastProactive.current = res.answer
        setMessages((m) => [
          ...m,
          {
            role: 'bot',
            text: res.answer,
            sources: res.sources,
            dataAvailable: res.data_available,
            proactive: true,
          },
        ])
      } catch {
        /* silencioso: las preguntas manuales ya reportan backend caido */
      }
    }
    tick()
    const timer = setInterval(tick, PROACTIVE_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  async function send(question) {
    const text = (question ?? input).trim()
    if (!text || busy) return
    requestManualScroll()
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', text }])
    try {
      const res = await api.chat(text)
      setMessages((m) => [
        ...m,
        {
          role: 'bot',
          text: res.answer,
          sources: res.sources,
          dataAvailable: res.data_available,
        },
      ])
    } catch {
      setMessages((m) => [
        ...m,
        { role: 'bot', text: 'No pude contactar al backend. ¿Está corriendo el servidor?' },
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Asistente" className="chat">
      <div className="messages" ref={messagesRef} onScroll={updateStickiness}>
        {messages.map((msg, i) => (
          <div key={i} className={`msg ${msg.role} ${msg.proactive ? 'proactive' : ''}`}>
            {msg.proactive && <span className="src">alerta automatica</span>}
            {msg.text}
            {msg.sources?.length > 0 && (
              <span className="src">fuentes: {[...new Set(msg.sources)].join(', ')}</span>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Escribe tu pregunta..."
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? '...' : 'Enviar'}
        </button>
      </form>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => send(s)} disabled={busy}>
            {s}
          </button>
        ))}
      </div>
    </Card>
  )
}
