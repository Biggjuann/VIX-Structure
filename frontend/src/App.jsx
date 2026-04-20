import { useState, useEffect, useRef, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_URL || ''  // set VITE_API_URL in Vercel env to point at Fly.io backend

// ─── Monitor Status Panel ─────────────────────────────────────────────────────

function MonitorPanel() {
  const [state, setState]       = useState(null)
  const [checking, setChecking] = useState(false)
  const [testing, setTesting]   = useState(false)
  const [msg, setMsg]           = useState(null)

  const loadState = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/signal/state`)
      setState(await r.json())
    } catch { /* silent */ }
  }, [])

  useEffect(() => { loadState() }, [loadState])

  const runCheck = async () => {
    setChecking(true); setMsg(null)
    try {
      const r    = await fetch(`${API_BASE}/api/signal/check`, { method: 'POST' })
      const json = await r.json()
      setState(json.current_state)
      setMsg(json.alert_fired
        ? `Alert fired: ${json.alert_fired.type}`
        : 'Check complete — no transition detected')
    } catch (e) { setMsg(`Error: ${e.message}`) }
    finally { setChecking(false) }
  }

  const sendTest = async () => {
    setTesting(true); setMsg(null)
    try {
      const r    = await fetch(`${API_BASE}/api/signal/test`, { method: 'POST' })
      const json = await r.json()
      const parts = []
      if (json.email_sent) parts.push('email ✓')
      else if (json.configured?.email === false) parts.push('email not configured')
      else parts.push('email failed')
      if (json.configured?.sms) {
        parts.push(json.sms_sent ? 'SMS ✓' : 'SMS failed')
      }
      setMsg(`Test sent — ${parts.join(', ')}`)
    } catch (e) { setMsg(`Error: ${e.message}`) }
    finally { setTesting(false) }
  }

  const fmtUtc = iso => {
    if (!iso) return '—'
    try { return new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true }) + ' ET' }
    catch { return iso }
  }

  const cfg = state?.notifications ?? {}
  const alerts = [...(state?.alerts_sent ?? [])].reverse().slice(0, 5)

  return (
    <section className="card monitor-card">
      <div className="section-hd">DAILY MONITOR</div>
      <div className="section-sub">Fires on BACKWARDATION ↔ CONTANGO transition</div>

      <div className="monitor-grid">
        <div className="monitor-row">
          <span className="monitor-key">Schedule</span>
          <span className="monitor-val">{state?.check_schedule ?? '—'}</span>
        </div>
        <div className="monitor-row">
          <span className="monitor-key">Last check</span>
          <span className="monitor-val">{fmtUtc(state?.last_check_utc)}</span>
        </div>
        <div className="monitor-row">
          <span className="monitor-key">Next check</span>
          <span className="monitor-val">{fmtUtc(state?.next_check_utc)}</span>
        </div>
        <div className="monitor-row">
          <span className="monitor-key">Last signal</span>
          <span className={`monitor-val signal-tag ${state?.last_structure === 'BACKWARDATION' ? 'tag-red' : 'tag-green'}`}>
            {state?.last_structure ?? '—'}
          </span>
        </div>
        <div className="monitor-row">
          <span className="monitor-key">Notifications</span>
          <span className="monitor-val">
            <span className={`notif-chip ${cfg.email ? 'chip-on' : 'chip-off'}`}>EMAIL</span>
            <span className={`notif-chip ${cfg.sms   ? 'chip-on' : 'chip-off'}`}>SMS</span>
          </span>
        </div>
      </div>

      <div className="monitor-actions">
        <button className="btn-action" onClick={runCheck} disabled={checking}>
          {checking ? '⟳ checking...' : '▶ Run Check Now'}
        </button>
        <button className="btn-action btn-test" onClick={sendTest} disabled={testing}>
          {testing ? '⟳ sending...' : '✉ Send Test Alert'}
        </button>
      </div>

      {msg && <div className="monitor-msg">{msg}</div>}

      {alerts.length > 0 && (
        <div className="alert-history">
          <div className="alert-history-hd">ALERT HISTORY</div>
          {alerts.map((a, i) => (
            <div key={i} className={`alert-row ${a.type.includes('BULL') ? 'alert-bull' : 'alert-bear'}`}>
              <span className={`alert-badge ${a.type.includes('BULL') ? 'ab-green' : 'ab-red'}`}>
                {a.type.includes('BULL') ? '↑ BULL' : '↓ BEAR'}
              </span>
              <span className="alert-date">{fmtUtc(a.timestamp)}</span>
              <span className="alert-spread">{a.spread !== null ? `${a.spread >= 0 ? '+' : ''}${a.spread?.toFixed(2)}` : '—'}</span>
              <span className="alert-sent">
                {a.email_sent ? '✉' : ''}
                {a.sms_sent   ? ' 📱' : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {alerts.length === 0 && state?.initialized && (
        <div className="no-alerts">∅ No transitions detected since monitoring began</div>
      )}
    </section>
  )
}

// ─── Term Structure SVG Chart ─────────────────────────────────────────────────

function TermChart({ futures }) {
  if (!futures?.length) return null

  const W = 560
  const H = 180
  const PAD = { top: 28, right: 16, bottom: 40, left: 44 }
  const cW = W - PAD.left - PAD.right
  const cH = H - PAD.top - PAD.bottom

  const prices = futures.map(f => f.price)
  const lo = Math.min(...prices) * 0.982
  const hi = Math.max(...prices) * 1.018
  const range = hi - lo || 1

  const slotW = cW / futures.length
  const barW = slotW * 0.52

  const yS = v => cH - ((v - lo) / range) * cH

  // 4 horizontal grid lines
  const gridVals = [0, 1, 2, 3, 4].map(i => lo + (range / 4) * i)

  const VX1_CLR = '#2d6fff'
  const VX3_CLR = '#ff9500'
  const DIM_CLR = '#172017'
  const DIM_TXT = '#303030'

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          {/* Grid */}
          {gridVals.map((v, i) => (
            <g key={i}>
              <line x1={0} y1={yS(v)} x2={cW} y2={yS(v)}
                stroke="#131313" strokeWidth={1} />
              <text x={-6} y={yS(v)} textAnchor="end"
                fill="#2a2a2a" fontSize={8} dominantBaseline="middle"
                fontFamily="IBM Plex Mono, monospace">
                {v.toFixed(1)}
              </text>
            </g>
          ))}

          {/* Bars */}
          {futures.map((f, i) => {
            const cx = i * slotW + slotW / 2
            const bh = Math.max(((f.price - lo) / range) * cH, 2)
            const x = cx - barW / 2
            const y = yS(f.price)
            const isVX1 = f.label === 'VX1'
            const isVX3 = f.label === 'VX3'
            const barClr = isVX1 ? VX1_CLR : isVX3 ? VX3_CLR : DIM_CLR
            const txtClr = isVX1 ? VX1_CLR : isVX3 ? VX3_CLR : DIM_TXT
            const labelClr = isVX1 || isVX3 ? '#cccccc' : DIM_TXT

            return (
              <g key={f.label}>
                {/* Bar glow for highlighted */}
                {(isVX1 || isVX3) && (
                  <rect x={x - 1} y={y - 1} width={barW + 2} height={bh + 2}
                    fill="none" stroke={barClr} strokeWidth={1} rx={2} opacity={0.35} />
                )}
                <rect x={x} y={y} width={barW} height={bh}
                  fill={barClr} rx={1.5}
                  opacity={isVX1 || isVX3 ? 0.92 : 0.75} />
                {/* Price above bar */}
                <text x={cx} y={y - 6} textAnchor="middle"
                  fill={labelClr} fontSize={8.5}
                  fontFamily="IBM Plex Mono, monospace">
                  {f.price.toFixed(2)}
                </text>
                {/* VX label below */}
                <text x={cx} y={cH + 16} textAnchor="middle"
                  fill={txtClr} fontSize={9.5}
                  fontWeight={isVX1 || isVX3 ? '600' : '400'}
                  fontFamily="IBM Plex Mono, monospace">
                  {f.label}
                </text>
                {/* Month below label */}
                <text x={cx} y={cH + 28} textAnchor="middle"
                  fill={DIM_TXT} fontSize={7}
                  fontFamily="IBM Plex Mono, monospace">
                  {f.month?.replace(' 20', "'")}
                </text>
              </g>
            )
          })}

          {/* Baseline */}
          <line x1={0} y1={cH} x2={cW} y2={cH} stroke="#1c1c1c" strokeWidth={1} />
        </g>
      </svg>
    </div>
  )
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [data, setData]           = useState(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)
  const [chatInput, setChatInput] = useState('')
  const [chatLog, setChatLog]     = useState([])
  const [chatBusy, setChatBusy]   = useState(false)
  const chatEndRef                = useRef(null)

  const fetchData = async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      const url    = refresh ? `${API_BASE}/api/vix/refresh` : `${API_BASE}/api/vix`
      const method = refresh ? 'POST' : 'GET'
      const res    = await fetch(url, { method })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(`Backend unreachable: ${e.message} — run: PYTHONUTF8=1 uvicorn main:app --reload --port 8002`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [])
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [chatLog])

  const sendChat = async (msg) => {
    msg = msg.trim()
    if (!msg || chatBusy) return
    setChatLog(prev => [...prev, { role: 'user', text: msg }])
    setChatInput('')
    setChatBusy(true)
    try {
      const res  = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }),
      })
      let json
      try { json = await res.json() } catch { throw new Error(`Server error (HTTP ${res.status})`) }
      if (!res.ok) throw new Error(json.detail || 'Chat error')
      setChatLog(prev => [...prev, { role: 'assistant', text: json.response }])
    } catch (e) {
      setChatLog(prev => [...prev, { role: 'assistant', text: `Error: ${e.message}` }])
    } finally {
      setChatBusy(false)
    }
  }

  const CHIPS = [
    'What does the current spread signal?',
    'Is this backwardation persistent?',
    'McMillan vs SqueezeMetrics signal',
    'What does contango mean for stocks?',
  ]

  const isBearish   = data?.structure === 'BACKWARDATION'
  const signalLabel = isBearish ? 'BEARISH' : 'NEUTRAL'
  const signalClr   = isBearish ? '#ff3030' : '#00cc66'
  const spread      = data?.spread
  const spreadStr   = spread !== null && spread !== undefined
    ? `${spread >= 0 ? '+' : ''}${spread.toFixed(4)}`
    : '—'

  return (
    <div className="app">

      {/* ── Header ── */}
      <header className="hdr">
        <div className="hdr-left">
          <span className="hdr-prompt">$</span>
          <div>
            <div className="hdr-title">VIX TERM STRUCTURE MONITOR</div>
            <div className="hdr-sub">McMillan VX3−VX1 Signal · CBOE Settlement</div>
          </div>
        </div>
        <div className="hdr-right">
          {data?.as_of && <span className="hdr-date">Settlement {data.as_of}</span>}
          <button className="btn-refresh" onClick={() => fetchData(true)} disabled={loading}>
            {loading ? <span className="spin">⟳</span> : '↻'} REFRESH
          </button>
        </div>
      </header>

      <main>
        {error && <div className="error-bar">⚠ {error}</div>}

        {loading && !data && (
          <div className="loading-msg">
            <span className="blink">█</span>&nbsp; fetching settlement data...
          </div>
        )}

        {data && (<>

          {/* ── Signal Hero ── */}
          <section className="card hero">
            <div className="hero-label">McMillan VX3−VX1 Signal</div>
            <div className="hero-signal" style={{ color: signalClr, textShadow: `0 0 24px ${signalClr}80` }}>
              {signalLabel}
            </div>

            <div className="hero-spread-row">
              <div className="spread-block">
                <span className="spread-key">VX3−VX1</span>
                <span className="spread-num">{spreadStr}</span>
              </div>
              {data.structure && (
                <span className={`badge badge-${data.structure.toLowerCase()}`}>
                  {data.structure}
                </span>
              )}
            </div>

            {data.vix_spot && (
              <div className="hero-spot">
                VIX Spot&nbsp;&nbsp;<strong>{data.vix_spot.toFixed(2)}</strong>
              </div>
            )}
          </section>

          {/* ── Term Structure Chart ── */}
          <section className="card">
            <div className="section-hd">VX FUTURES TERM STRUCTURE</div>
            {data.futures?.length
              ? <TermChart futures={data.futures} />
              : <div className="no-data">No futures data available</div>
            }

            {/* Compact table row */}
            {data.futures?.length > 0 && (
              <div className="fx-grid">
                {data.futures.map(f => (
                  <div key={f.label}
                    className={`fx-item${f.label === 'VX1' ? ' fx-vx1' : f.label === 'VX3' ? ' fx-vx3' : ''}`}>
                    <div className="fx-lbl">{f.label}</div>
                    <div className="fx-px">{f.price.toFixed(2)}</div>
                    <div className="fx-mo">{f.month}</div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── Signal Log ── */}
          <section className="card">
            <div className="section-hd">SIGNAL LOG</div>
            <div className="log">
              <div className="log-row log-sell">
                <span className="log-date">Jan 27 2025</span>
                <span className="log-src">SqueezeMetrics</span>
                <span className="sig-badge sig-sell">SELL</span>
              </div>
              <div className={`log-row ${isBearish ? 'log-sell' : 'log-neutral'}`}>
                <span className="log-date">{data.as_of ?? 'Live'}</span>
                <span className="log-src">McMillan VX3−VX1</span>
                <span className={`sig-badge ${isBearish ? 'sig-sell' : 'sig-neutral'}`}>
                  {signalLabel}
                </span>
              </div>
            </div>
            <div className="no-buy">∅ No buy signal as of Mar 2026</div>
          </section>

          {/* ── McMillan Quote ── */}
          <section className="card quote-card">
            <div className="quote-glyph">"</div>
            <blockquote className="quote-body">
              When VX3−VX1 turns negative, it is time to be negative on stocks
              until this spread returns to a positive status.
            </blockquote>
            <cite className="quote-cite">— Lawrence McMillan</cite>
          </section>

        </>)}

        {/* ── Daily Monitor ── */}
        <MonitorPanel />

        {/* ── Claude Q&A ── */}
        <section className="card chat-card">
          <div className="section-hd">ASK THE ANALYST</div>
          <div className="section-sub">Claude · live VIX data injected into context</div>

          <div className="chips">
            {CHIPS.map(c => (
              <button key={c} className="chip" onClick={() => sendChat(c)} disabled={chatBusy}>
                {c}
              </button>
            ))}
          </div>

          {chatLog.length > 0 && (
            <div className="chat-log">
              {chatLog.map((m, i) => (
                <div key={i} className={`chat-line chat-${m.role}`}>
                  <span className="chat-glyph">{m.role === 'user' ? '>' : '$'}</span>
                  <span className="chat-txt">{m.text}</span>
                </div>
              ))}
              {chatBusy && (
                <div className="chat-line chat-assistant">
                  <span className="chat-glyph">$</span>
                  <span className="chat-txt"><span className="blink">█</span> analyzing...</span>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          )}

          <div className="chat-input-wrap">
            <span className="input-gt">&gt;</span>
            <input
              className="chat-input"
              type="text"
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && sendChat(chatInput)}
              placeholder="ask about volatility structure..."
              disabled={chatBusy}
            />
            <button
              className="btn-send"
              onClick={() => sendChat(chatInput)}
              disabled={chatBusy || !chatInput.trim()}
            >
              SEND
            </button>
          </div>
        </section>
      </main>

      <footer className="footer">
        <span>Data: CBOE Settlement · Yahoo Finance</span>
        {data?.fetched_at && (
          <span>Fetched {new Date(data.fetched_at).toLocaleTimeString()}</span>
        )}
      </footer>
    </div>
  )
}
