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

const PRESETS: Record<string, Record<string, number>> = {
  companion: { humor: 8, formality: 2, proactivity: 7, verbosity: 6, reasoning_depth: 5, autonomy: 4, epistemic_strictness: 4, warmth: 9, creativity: 7, assertiveness: 4 },
  focused: { humor: 2, formality: 6, proactivity: 3, verbosity: 3, reasoning_depth: 9, autonomy: 5, epistemic_strictness: 8, warmth: 4, creativity: 4, assertiveness: 6 },
  neutral: { humor: 5, formality: 5, proactivity: 5, verbosity: 5, reasoning_depth: 5, autonomy: 5, epistemic_strictness: 5, warmth: 5, creativity: 5, assertiveness: 5 },
}

export default function Personality() {
  const [sliders, setSliders] = useState<Record<string, number>>({})
  const [info, setInfo] = useState<Record<string, SliderInfo>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    Promise.all([
      api<{ sliders: Record<string, number> }>('/api/sliders')
        .then(d => {
          setSliders(d.sliders || d)
          setOffline(!!(d as Record<string, unknown>)._offline)
        })
        .catch(() => setOffline(true)),
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
    </div>
  )
}
