import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, Polyline, CircleMarker, Circle, Tooltip, useMap } from 'react-leaflet'
import L from 'leaflet'

// Collector API address. Set VITE_API_URL at build time on a server (e.g. "/api"
// behind the reverse proxy). Default: same host that served this page, port 8020,
// which works on the Mac (localhost) or from your phone (Mac's IP).
// Map center before any points load: VITE_DEFAULT_CENTER="lat,lng" (default: India).
const DEFAULT_CENTER = (import.meta.env.VITE_DEFAULT_CENTER || '20.59,78.96').split(',').map(Number)
const API = import.meta.env.VITE_API_URL || `http://${location.hostname}:8020`

// --- Data-quality filtering ------------------------------------------------
const ACC_MAX_M = 200       // drop points less accurate than this (metres)
const SPEED_MAX_KMH = 200   // drop points implying a faster-than-plausible jump
const STAY_RADIUS_M = 60    // points within this of each other count as "staying put"
const STAY_MIN_MIN = 5      // a stay must last at least this long to count as a stop

function haversine(a, b) {
  const R = 6371000
  const toRad = (d) => (d * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLng = toRad(b.lng - a.lng)
  const lat1 = toRad(a.lat)
  const lat2 = toRad(b.lat)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h)) // metres
}

// points come time-ascending from the API.
function filterPoints(points) {
  const accurate = points.filter((p) => !(p.acc > ACC_MAX_M)) // keep acc 0 (unknown)
  const kept = []
  for (const p of accurate) {
    const prev = kept[kept.length - 1]
    if (prev) {
      const dt = Math.abs(p.t - prev.t) // seconds
      if (dt > 0) {
        const kmh = (haversine(prev, p) / dt) * 3.6
        if (kmh > SPEED_MAX_KMH) continue // implausible jump -> drop
      }
    }
    kept.push(p)
  }
  return kept
}

// Movement stats over the shown (time-ascending) points.
function computeStats(points) {
  if (points.length === 0) return null
  let distance = 0
  for (let i = 1; i < points.length; i++) distance += haversine(points[i - 1], points[i])

  // Stops: runs of consecutive points staying within STAY_RADIUS_M of the run's anchor.
  let stops = 0
  let longestStay = 0
  let anchor = points[0]
  let runStart = points[0].t
  for (let i = 1; i <= points.length; i++) {
    const p = points[i]
    if (p && haversine(anchor, p) <= STAY_RADIUS_M) continue
    const durMin = ((points[i - 1].t - runStart) / 60)
    if (durMin >= STAY_MIN_MIN) {
      stops += 1
      if (durMin > longestStay) longestStay = durMin
    }
    if (p) { anchor = p; runStart = p.t }
  }

  return {
    distanceKm: distance / 1000,
    first: points[0].t,
    last: points[points.length - 1].t,
    spanMin: (points[points.length - 1].t - points[0].t) / 60,
    stops,
    longestStayMin: longestStay,
  }
}
// ---------------------------------------------------------------------------

function todayStr() {
  const d = new Date()
  const z = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
}

function fmtTime(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function fmtDuration(min) {
  if (min < 60) return `${Math.round(min)} min`
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return m ? `${h} h ${m} min` : `${h} h`
}

// Colour for a point under the chosen scheme.
function colorFor(p, i, n, by) {
  if (by === 'source') return p.src === 'own' ? '#22c55e' : '#f59e0b'
  if (by === 'time') {
    const hour = new Date(p.t * 1000).getHours() + new Date(p.t * 1000).getMinutes() / 60
    return `hsl(${Math.round((hour / 24) * 300)}, 70%, 55%)` // midnight→red … evening→violet
  }
  return i === 0 ? '#22c55e' : i === n - 1 ? '#ef4444' : '#3b82f6' // position
}

// Pan/zoom the map to fit the given points whenever they change.
function FitBounds({ points }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) {
      map.setView([points[0].lat, points[0].lng], 16)
      return
    }
    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lng]))
    map.fitBounds(bounds.pad(0.2), { maxZoom: 16 })
  }, [points, map])
  return null
}

// --- Trace display (shared by the dashboard and the public shared view) ----
// Takes raw points and owns the Filtered/Raw toggle, colour scheme, playback,
// stats bar, map and side list. The surrounding header is the caller's.
function TraceView({ raw, loading, error, emptyText = 'No locations recorded here.' }) {
  const [mode, setMode] = useState('filtered') // 'filtered' | 'raw'
  const [colourBy, setColourBy] = useState('position') // 'position' | 'source' | 'time'
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const timer = useRef(null)

  const filtered = useMemo(() => filterPoints(raw), [raw])
  const points = mode === 'raw' ? raw : filtered
  const dropped = raw.length - filtered.length
  const stats = useMemo(() => computeStats(points), [points])

  // reset playback to "show everything" whenever the shown set changes
  useEffect(() => {
    setPlaying(false)
    setIdx(points.length ? points.length - 1 : 0)
  }, [points])

  // playback ticker
  useEffect(() => {
    if (!playing) return
    if (idx >= points.length - 1) { setPlaying(false); return }
    timer.current = setTimeout(() => setIdx((i) => Math.min(i + 1, points.length - 1)), 500)
    return () => clearTimeout(timer.current)
  }, [playing, idx, points.length])

  // idx can momentarily exceed a shrunken list (e.g. switching Raw->Filtered)
  // before the reset effect runs, so clamp it for all reads this render.
  const maxIdx = Math.max(points.length - 1, 0)
  const curIdx = Math.min(Math.max(idx, 0), maxIdx)
  const atEnd = curIdx >= maxIdx
  const visible = points.slice(0, curIdx + 1)
  const path = useMemo(() => visible.map((p) => [p.lat, p.lng]), [visible])
  const center = points[0] ? [points[0].lat, points[0].lng] : DEFAULT_CENTER

  function togglePlay() {
    if (atEnd) { setIdx(0); setPlaying(true) }
    else setPlaying((p) => !p)
  }

  return (
    <>
      <div className="controls">
        <div className="toggle" role="group" aria-label="Filter mode">
          <button className={mode === 'filtered' ? 'on' : ''} onClick={() => setMode('filtered')}>Filtered</button>
          <button className={mode === 'raw' ? 'on' : ''} onClick={() => setMode('raw')}>Raw</button>
        </div>
        <label className="field">Colour
          <select value={colourBy} onChange={(e) => setColourBy(e.target.value)}>
            <option value="position">Start / end</option>
            <option value="source">Own vs crowdsourced</option>
            <option value="time">Time of day</option>
          </select>
        </label>
        <span className="spacer" />
        <span className="summary">
          {loading ? 'Loading…' : `${points.length} point${points.length === 1 ? '' : 's'}`}
          {mode === 'filtered' && dropped > 0 && ` · ${dropped} filtered out`}
        </span>
      </div>

      {stats && (
        <div className="stats">
          <div className="stat"><span>Distance</span><b>{stats.distanceKm.toFixed(2)} km</b></div>
          <div className="stat"><span>First seen</span><b>{fmtTime(stats.first)}</b></div>
          <div className="stat"><span>Last seen</span><b>{fmtTime(stats.last)}</b></div>
          <div className="stat"><span>Span</span><b>{fmtDuration(stats.spanMin)}</b></div>
          <div className="stat"><span>Stops</span><b>{stats.stops}</b></div>
          <div className="stat"><span>Longest stay</span><b>{stats.longestStayMin ? fmtDuration(stats.longestStayMin) : '—'}</b></div>
          {colourBy === 'source' && (
            <div className="stat legend"><span>Legend</span><b><i className="dot" style={{ background: '#22c55e' }} />own <i className="dot" style={{ background: '#f59e0b' }} />crowd</b></div>
          )}
        </div>
      )}

      <div className="main">
        <div className="map-wrap">
          <MapContainer center={center} zoom={14} scrollWheelZoom>
            <TileLayer
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; OpenStreetMap'
              maxZoom={19}
            />
            {visible.map((p, i) =>
              p.acc > 0 ? (
                <Circle
                  key={`acc-${p.t}-${i}`}
                  center={[p.lat, p.lng]}
                  radius={p.acc}
                  pathOptions={{ color: '#3b82f6', weight: 1, opacity: 0.2, fillColor: '#3b82f6', fillOpacity: 0.06 }}
                />
              ) : null
            )}
            {path.length > 1 && (
              <Polyline positions={path} pathOptions={{ color: '#3b82f6', weight: 4, opacity: 0.85 }} />
            )}
            {visible.map((p, i) => {
              const isCurrent = !atEnd && i === visible.length - 1
              const color = colorFor(p, i, points.length, colourBy)
              const radius = isCurrent ? 10 : (colourBy === 'position' && (i === 0 || i === points.length - 1)) ? 8 : 5
              return (
                <CircleMarker
                  key={`${p.t}-${i}`}
                  center={[p.lat, p.lng]}
                  radius={radius}
                  pathOptions={{ color: isCurrent ? '#fff' : '#ffffffaa', weight: isCurrent ? 3 : 1.5, fillColor: color, fillOpacity: 1 }}
                >
                  <Tooltip>
                    {fmtTime(p.t)} · {p.lat.toFixed(5)}, {p.lng.toFixed(5)}
                    {p.acc > 0 ? ` · ±${Math.round(p.acc)}m` : ''}
                    {p.src ? ` · ${p.src}` : ''}
                  </Tooltip>
                </CircleMarker>
              )
            })}
            <FitBounds points={points} />
          </MapContainer>

          {points.length > 1 && (
            <div className="playback">
              <button onClick={togglePlay}>{playing ? '❚❚' : (atEnd ? '↻' : '▶')}</button>
              <input
                type="range"
                min={0}
                max={points.length - 1}
                value={curIdx}
                onChange={(e) => { setPlaying(false); setIdx(Number(e.target.value)) }}
              />
              <span className="clock">{fmtTime(points[curIdx].t)}</span>
            </div>
          )}
        </div>

        <aside>
          {error ? (
            <div className="msg">{error}</div>
          ) : points.length === 0 ? (
            <div className="empty">
              {loading
                ? 'Loading…'
                : raw.length > 0
                  ? 'Every point was filtered out. Switch to Raw to see them.'
                  : emptyText}
            </div>
          ) : (
            points.map((p, i) => ({ p, i })).reverse().map(({ p, i }) => (
              <div className="row" key={`${p.t}-${i}`} onClick={() => { setPlaying(false); setIdx(i) }}>
                <span className="t">
                  <i className="dot" style={{ background: colorFor(p, i, points.length, colourBy) }} />
                  {fmtTime(p.t)}
                </span>
                <span className="c">
                  {p.lat.toFixed(5)}, {p.lng.toFixed(5)}
                  <div className="s">
                    {p.acc > 0 ? `±${Math.round(p.acc)}m` : ''}{p.src ? ` · ${p.src}` : ''}
                    {i === 0 ? ' · start' : i === points.length - 1 ? ' · latest' : ''}
                  </div>
                </span>
              </div>
            ))
          )}
        </aside>
      </div>
    </>
  )
}

// --- Auth ------------------------------------------------------------------
const TOKEN_KEY = 'tt_token'
function getToken() { try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' } }
function setStoredToken(t) { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ } }

function Login({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      const r = await fetch(`${API}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      if (r.status === 401) { setErr('Wrong email or password.'); return }
      if (!r.ok) { setErr(`Login failed (${r.status}).`); return }
      const j = await r.json()
      onLogin(j.token, j.email)
    } catch {
      setErr(`Can't reach the collector at ${API}. Is it running, and are you on the same Wi-Fi?`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1>Tag Trace</h1>
        <p className="sub">Sign in to view the trace</p>
        <label>Email
          <input type="email" value={email} autoFocus autoComplete="username"
            onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>Password
          <input type="password" value={password} autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {err && <div className="login-err">{err}</div>}
        <button type="submit" className="login-btn" disabled={busy || !email || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

// --- Sharing ---------------------------------------------------------------
const TTL_OPTIONS = [
  { seconds: 900, label: '15 minutes' },
  { seconds: 3600, label: '1 hour' },
  { seconds: 18000, label: '5 hours' },
  { seconds: 86400, label: '1 day' },
  { seconds: 432000, label: '5 days' },
]

function ShareDialog({ token, onClose }) {
  const [from, setFrom] = useState(todayStr())
  const [to, setTo] = useState(todayStr())
  const [ttl, setTtl] = useState(3600)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [link, setLink] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [copied, setCopied] = useState(false)

  async function create() {
    setBusy(true); setErr('')
    try {
      const r = await fetch(`${API}/shares`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ from, to, ttl_seconds: ttl }),
      })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        setErr(j.error || `Couldn't create the link (${r.status}).`)
        return
      }
      const j = await r.json()
      setLink(`${location.origin}${location.pathname}?share=${j.token}`)
      setExpiresAt(j.expires_at)
    } catch {
      setErr(`Can't reach the collector at ${API}.`)
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      const el = document.getElementById('share-link-input')
      if (el) { el.select(); document.execCommand('copy') }
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Share a trace</h2>
          <button className="x" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {!link ? (
          <>
            <p className="sub">Anyone with the link can view this date range — no login — until it expires. After that only signed-in users can see it.</p>
            <div className="field-row">
              <label>From<input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
              <label>To<input type="date" value={to} min={from} max={todayStr()} onChange={(e) => setTo(e.target.value)} /></label>
            </div>
            <div className="ttl">
              <span className="ttl-label">Link expires after</span>
              <div className="ttl-opts">
                {TTL_OPTIONS.map((o) => (
                  <button
                    key={o.seconds}
                    className={ttl === o.seconds ? 'on' : ''}
                    onClick={() => setTtl(o.seconds)}
                  >{o.label}</button>
                ))}
              </div>
            </div>
            {err && <div className="login-err">{err}</div>}
            <button className="login-btn" onClick={create} disabled={busy || !from || !to}>
              {busy ? 'Creating…' : 'Create link'}
            </button>
          </>
        ) : (
          <>
            <p className="sub">Link ready. It expires {new Date(expiresAt).toLocaleString()}.</p>
            <div className="field-row">
              <input id="share-link-input" readOnly value={link} onFocus={(e) => e.target.select()} style={{ flex: 1 }} />
              <button className="login-btn" style={{ marginTop: 0, minWidth: 90 }} onClick={copy}>
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function SharedView({ shareToken }) {
  const [raw, setRaw] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [meta, setMeta] = useState(null) // {from, to, expires_at}

  async function load() {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${API}/shared?token=${encodeURIComponent(shareToken)}`)
      const j = await r.json().catch(() => ({}))
      if (!r.ok) {
        setError(j.error || 'This share link has expired or is invalid.')
        return
      }
      setRaw(j.points || [])
      setMeta({ from: j.from, to: j.to, expires_at: j.expires_at })
    } catch {
      setError(`Can't reach the collector at ${API}.`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [shareToken])

  return (
    <div className="app">
      <header>
        <h1>Tag Trace</h1>
        <span className="badge">Shared · read-only</span>
        {meta && <span className="whoami">{meta.from === meta.to ? meta.from : `${meta.from} → ${meta.to}`}</span>}
        <button onClick={load} disabled={loading}>{loading ? 'Refreshing...' : 'Refresh'}</button>
        <span className="spacer" />
        {meta && <span className="summary">Expires {new Date(meta.expires_at).toLocaleString()}</span>}
      </header>
      {error
        ? <div className="msg">{error}</div>
        : <TraceView raw={raw} loading={loading} error="" emptyText="No locations in this date range." />}
    </div>
  )
}

export default function App() {
  const shareToken = useMemo(() => new URLSearchParams(location.search).get('share') || '', [])
  const [token, setToken] = useState(getToken())
  const [email, setEmail] = useState('')

  function onLogin(tok, mail) { setStoredToken(tok); setEmail(mail || ''); setToken(tok) }
  function onLogout() {
    const t = token
    setStoredToken(''); setToken(''); setEmail('')
    if (t) { try { fetch(`${API}/logout`, { method: 'POST', headers: { Authorization: `Bearer ${t}` } }) } catch { /* ignore */ } }
  }

  // A share link is public: show the shared view without requiring login.
  if (shareToken) return <SharedView shareToken={shareToken} />
  if (!token) return <Login onLogin={onLogin} />
  return <Dashboard token={token} email={email} onLogin={onLogin} onLogout={onLogout} />
}

function Dashboard({ token, email, onLogin, onLogout }) {
  const [date, setDate] = useState(todayStr())
  const [days, setDays] = useState([])
  const [raw, setRaw] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [shareOpen, setShareOpen] = useState(false)

  // Fetch with the session token; a 401 means the session expired -> log out.
  async function authFetch(path) {
    const r = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } })
    if (r.status === 401) { onLogout(); throw new Error('unauthorized') }
    return r
  }

  async function loadDays() {
    try {
      const r = await authFetch('/days')
      const j = await r.json()
      setDays(j.days || [])
    } catch { /* handled by points load / logout */ }
  }

  async function loadPoints(d) {
    setLoading(true)
    setError('')
    try {
      const r = await authFetch(`/locations?date=${d}`)
      const j = await r.json()
      setRaw(j.points || [])
    } catch (e) {
      if (e.message !== 'unauthorized') {
        setError(`Can't reach the collector at ${API}. Is it running, and are you on the same Wi-Fi?`)
      }
      setRaw([])
    } finally {
      setLoading(false)
    }
  }

  // Validate the stored token on mount and pick up the signed-in email.
  useEffect(() => {
    (async () => {
      try {
        const r = await authFetch('/me')
        const j = await r.json()
        if (j.email) onLogin(token, j.email)
      } catch { /* 401 already logged out */ }
    })()
  }, [])

  useEffect(() => { loadDays() }, [])
  useEffect(() => { loadPoints(date) }, [date])

  return (
    <div className="app">
      <header>
        <h1>Tag Trace</h1>
        <input type="date" value={date} max={todayStr()} onChange={(e) => setDate(e.target.value)} />
        {days.length > 0 && (
          <select value={days.some((d) => d.date === date) ? date : ''} onChange={(e) => e.target.value && setDate(e.target.value)}>
            <option value="">Days with data…</option>
            {days.map((d) => (
              <option key={d.date} value={d.date}>{d.date} ({d.count})</option>
            ))}
          </select>
        )}
        <button onClick={() => { loadDays(); loadPoints(date) }}>Refresh</button>
        <button onClick={() => setShareOpen(true)}>Share</button>
        <span className="spacer" />
        {email && <span className="whoami">{email}</span>}
        <button onClick={onLogout}>Log out</button>
      </header>

      <TraceView raw={raw} loading={loading} error={error} emptyText="No locations recorded for this day." />

      {shareOpen && <ShareDialog token={token} onClose={() => setShareOpen(false)} />}
    </div>
  )
}
