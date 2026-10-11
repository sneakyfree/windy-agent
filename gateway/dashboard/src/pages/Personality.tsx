import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface SliderInfo {
  name: string
  label?: string
  description: string
  low_label?: string
  high_label?: string
  impact_low?: string
  impact_high?: string
}

interface DriftSlider {
  name: string
  old: number
  new: number
  source?: string
}

interface Drift {
  detected: boolean
  drifted_sliders: DriftSlider[]
  drift_source?: string | null
  baseline_at?: string | null
}

interface HistoryRow {
  id: string
  key?: string | null
  old_value: string | null
  new_value: string | null
  changed_by: string | null
  created_at: string
}

// soul_history timestamps are SQLite UTC "YYYY-MM-DD HH:MM:SS".
const fmtUtc = (ts?: string | null) =>
  ts ? new Date(ts.replace(' ', 'T') + 'Z').toLocaleString() : ''

const sliderLabel = (key?: string | null) =>
  (key || '').replace(/^slider_/, '').replace(/_/g, ' ')

// Preset quick-sets. Every key MUST be a real slider name returned by
// /api/sliders — "assertiveness" was previously listed but is not a
// slider, so applying any preset fired a PUT /api/sliders/assertiveness
// that the brain rejected (silent console noise + a no-op write).
const PRESETS: Record<string, Record<string, number>> = {
  companion: { humor: 8, formality: 2, proactivity: 7, verbosity: 6, reasoning_depth: 5, autonomy: 4, epistemic_strictness: 4, warmth: 9, creativity: 7 },
  focused: { humor: 2, formality: 6, proactivity: 3, verbosity: 3, reasoning_depth: 9, autonomy: 5, epistemic_strictness: 8, warmth: 4, creativity: 4 },
  neutral: { humor: 5, formality: 5, proactivity: 5, verbosity: 5, reasoning_depth: 5, autonomy: 5, epistemic_strictness: 5, warmth: 5, creativity: 5 },
}

export default function Personality() {
  const [sliders, setSliders] = useState<Record<string, number>>({})
  const [info, setInfo] = useState<Record<string, SliderInfo>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)
  const [drift, setDrift] = useState<Drift | null>(null)
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [rollingBack, setRollingBack] = useState(false)

  const loadDriftAndHistory = () => Promise.all([
    api<{ dashboard?: { drift?: Drift } }>('/api/dashboard')
      .then(d => setDrift(d.dashboard?.drift ?? null))
      .catch(() => {}),
    api<{ history: HistoryRow[] }>('/api/personality/history?limit=20&sliders_only=true')
      .then(d => setHistory(Array.isArray(d.history) ? d.history : []))
      .catch(() => {}),
  ])

  const loadSliders = () =>
    api<{ sliders: Record<string, number> }>('/api/sliders')
      .then(d => {
        setSliders(d.sliders || d)
        setOffline(!!(d as Record<string, unknown>)._offline)
      })
      .catch(() => setOffline(true))

  // Restore every slider to its value as of `when` (owner action — the
  // brain marks restored values as set by the owner and clears the alert).
  const rollback = async (when: string) => {
    if (!confirm(`Restore all personality sliders to how they were at ${fmtUtc(when)}?`)) return
    setRollingBack(true)
    try {
      await api('/api/personality/rollback', {
        method: 'POST',
        body: JSON.stringify({ snapshot_date: when }),
      })
    } catch { /* ignore — reload shows the real state */ }
    await Promise.all([loadSliders(), loadDriftAndHistory()])
    setRollingBack(false)
  }

  useEffect(() => {
    Promise.all([
      loadDriftAndHistory(),
      loadSliders(),
      api<{ sliders: SliderInfo[] }>('/api/sliders/info')
        .then(d => {
          const map: Record<string, SliderInfo> = {}
          if (Array.isArray(d.sliders)) {
            d.sliders.forEach(s => { map[s.name] = s })
          } else if (d.sliders) {
            Object.assign(map, d.sliders)
          }
          setInfo(map)
        })
        .catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  const handleChange = async (name: string, value: number) => {
    setSliders(prev => ({ ...prev, [name]: value }))
    setSaving(name)
    try {
      await api(`/api/sliders/${name}`, {
        method: 'PUT',
        body: JSON.stringify({ value }),
      })
    } catch { /* ignore */ }
    setSaving(null)
  }

  const applyPreset = (preset: string) => {
    const values = PRESETS[preset]
    if (!values) return
    Object.entries(values).forEach(([name, value]) => {
      handleChange(name, value)
    })
  }

  const sliderNames = Object.keys(sliders).length > 0
    ? Object.keys(sliders)
    : ['humor', 'formality', 'proactivity', 'verbosity', 'reasoning_depth', 'autonomy', 'epistemic_strictness', 'warmth', 'creativity', 'assertiveness']

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Personality</h1>
      <p className="text-[#64748b] text-sm mb-6">Fine-tune how your agent thinks, speaks, and behaves.</p>

      {offline && (
        <div className="bg-[#eab308]/10 border border-[#eab308]/30 rounded-lg p-3 mb-4 text-sm text-[#eab308]">
          ⚠️ Agent offline — showing default values. Changes will apply when the agent reconnects.
        </div>
      )}

      {drift?.detected && drift.drifted_sliders.length > 0 && (
        <div className="bg-[#eab308]/10 border border-[#eab308]/30 rounded-lg p-4 mb-4 text-sm text-[#eab308]">
          <div className="font-medium mb-1">Personality drift: these sliders moved without you</div>
          <ul className="mb-2 space-y-0.5">
            {drift.drifted_sliders.map(d => (
              <li key={d.name}>
                <span className="capitalize">{d.name.replace(/_/g, ' ')}</span>: {d.old} → {d.new}
                {d.source && <span className="text-[#64748b]"> (by {d.source})</span>}
              </li>
            ))}
          </ul>
          {drift.baseline_at && (
            <button
              onClick={() => rollback(drift.baseline_at as string)}
              disabled={rollingBack}
              className="px-3 py-1.5 rounded-lg border border-[#eab308]/50 hover:bg-[#eab308]/10 disabled:opacity-50"
            >
              Restore values from {fmtUtc(drift.baseline_at)}
            </button>
          )}
          <span className="ml-2 text-xs text-[#64748b]">Or keep them by moving the slider yourself.</span>
        </div>
      )}

      {/* Presets */}
      <div className="flex gap-2 mb-6 flex-wrap">
        {Object.keys(PRESETS).map(p => (
          <button
            key={p}
            onClick={() => applyPreset(p)}
            className="px-4 py-2 rounded-lg bg-[#111827] border border-[#1e293b] text-sm capitalize
              hover:border-[#00d4ff] hover:text-[#00d4ff] transition-colors"
          >
            {p}
          </button>
        ))}
      </div>

      {/* Sliders */}
      <div className="space-y-4">
        {sliderNames.map(name => {
          const value = sliders[name] ?? 5
          const meta = info[name]
          return (
            <div key={name} className="bg-[#111827] rounded-xl border border-[#1e293b] p-4">
              <div className="flex justify-between items-center mb-2">
                <div>
                  <span className="text-white font-medium text-sm capitalize">
                    {name.replace(/_/g, ' ')}
                  </span>
                  {saving === name && (
                    <span className="ml-2 text-[#00d4ff] text-xs">saving...</span>
                  )}
                </div>
                <span className="text-[#00d4ff] font-mono text-sm font-bold">{value}</span>
              </div>
              {meta?.description && (
                <p className="text-xs text-[#64748b] mb-2">{meta.description}</p>
              )}
              <div className="flex items-center gap-3">
                {(meta?.low_label || meta?.impact_low) && (
                  <span className="text-xs text-[#64748b] w-16 text-right truncate" title={meta.impact_low || meta.low_label}>
                    {meta.low_label || 'Low'}
                  </span>
                )}
                <input
                  type="range"
                  min={0}
                  max={10}
                  value={value}
                  onChange={e => handleChange(name, Number(e.target.value))}
                  className="flex-1"
                />
                {(meta?.high_label || meta?.impact_high) && (
                  <span className="text-xs text-[#64748b] w-16 truncate" title={meta.impact_high || meta.high_label}>
                    {meta.high_label || 'High'}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* History + rollback */}
      <h2 className="text-lg font-semibold text-white mt-8 mb-2">History</h2>
      {history.length === 0 ? (
        <p className="text-[#64748b] text-sm">No personality changes recorded yet. Changes are recorded once a day.</p>
      ) : (
        <div className="bg-[#111827] rounded-xl border border-[#1e293b] divide-y divide-[#1e293b]">
          {history.map(h => (
            <div key={h.id} className="flex items-center gap-3 p-3 text-sm">
              <span className="text-[#64748b] text-xs w-40 shrink-0">{fmtUtc(h.created_at)}</span>
              <span className="text-white capitalize flex-1">
                {sliderLabel(h.key)}
                {h.old_value !== h.new_value
                  ? <span className="font-mono text-[#00d4ff] ml-2">{h.old_value} → {h.new_value}</span>
                  : <span className="font-mono text-[#00d4ff] ml-2">{h.new_value}</span>}
                {h.changed_by && <span className="text-[#64748b] text-xs ml-2 normal-case">({h.changed_by})</span>}
              </span>
              <button
                onClick={() => rollback(h.created_at)}
                disabled={rollingBack}
                className="px-3 py-1 rounded-lg border border-[#1e293b] text-xs hover:border-[#00d4ff] hover:text-[#00d4ff] disabled:opacity-50"
              >
                Restore to here
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
