import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface DashboardData {
  agent_name?: string
  status?: string
  uptime?: string
  model?: string
  daily_spend?: number
  daily_budget?: number
  monthly_spend?: number
  episodes_count?: number
  nodes_count?: number
  skills_count?: number
  intents_count?: number
  last_message?: string
  passport_id?: string
  email?: string
  phone?: string
  matrix_user?: string
  _offline?: boolean
}

export default function Home() {
  const [dash, setDash] = useState<DashboardData | null>(null)
  const [health, setHealth] = useState<{ brain_connected: boolean } | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    Promise.all([
      api<DashboardData>('/api/dashboard').then(setDash).catch(() => {}),
      api<{ brain_connected: boolean }>('/api/health').then(setHealth).catch(() => {}),
    ]).finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [])

  const connected = health?.brain_connected ?? false
  const spend = dash?.daily_spend ?? 0
  const budget = dash?.daily_budget ?? 5
  const offline = dash?._offline ?? !connected

  const ecosystemServices = [
    { name: 'Eternitas', ok: !!dash?.passport_id },
    { name: 'Mail', ok: !!dash?.email },
    { name: 'Chat', ok: !!dash?.matrix_user },
    { name: 'Phone', ok: !!dash?.phone },
  ]

  if (loading && !dash) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      {/* Offline banner */}
      {offline && (
        <div className="bg-[#ef4444]/10 border border-[#ef4444]/30 rounded-xl p-4 mb-6 flex items-center gap-3">
          <span className="text-[#ef4444] text-lg">⚠️</span>
          <div>
            <div className="text-[#ef4444] font-medium text-sm">Agent Offline</div>
            <div className="text-[#ef4444]/70 text-xs">Start the agent with <code className="bg-[#ef4444]/10 px-1 rounded">windy start</code> to connect</div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <div className="w-14 h-14 rounded-2xl bg-[#111827] border border-[#1e293b] flex items-center justify-center text-3xl">
          🪰
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white">{dash?.agent_name || 'Windy Fly'}</h1>
          <div className="flex items-center gap-2 text-sm text-[#64748b]">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-[#22c55e]' : 'bg-[#ef4444]'}`} />
            {connected ? 'Running' : 'Offline'}
            {dash?.uptime && <span>· {dash.uptime}</span>}
          </div>
        </div>
      </div>

      {/* Status cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Card label="Status" value={connected ? '🟢 Running' : '🔴 Offline'} />
        <Card label="Brain" value={dash?.model || 'Not set'} />
        <Card label="Cost Today" value={`$${spend.toFixed(2)} / $${budget.toFixed(2)}`} />
        <Card label="Episodes" value={String(dash?.episodes_count ?? 0)} />
      </div>

      {/* Budget bar */}
      <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4 mb-6">
        <div className="flex justify-between text-sm mb-2">
          <span className="text-[#64748b]">Daily Budget</span>
          <span className="text-white">${spend.toFixed(2)} / ${budget.toFixed(2)}</span>
        </div>
        <div className="w-full h-2.5 bg-[#1e293b] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${Math.min((spend / budget) * 100, 100)}%`,
              background: spend / budget > 0.8 ? '#ef4444' : spend / budget > 0.5 ? '#eab308' : '#00d4ff',
            }}
          />
        </div>
      </div>

      {/* Ecosystem status */}
      <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4 mb-6">
        <h2 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-3">Ecosystem</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {ecosystemServices.map(svc => (
            <div key={svc.name} className="flex items-center gap-2 text-sm">
              <div className={`w-2 h-2 rounded-full ${svc.ok ? 'bg-[#22c55e]' : 'bg-[#64748b]'}`} />
              <span className={svc.ok ? 'text-white' : 'text-[#64748b]'}>{svc.name}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card label="Knowledge Nodes" value={String(dash?.nodes_count ?? 0)} />
        <Card label="Skills" value={String(dash?.skills_count ?? 0)} />
        <Card label="Active Goals" value={String(dash?.intents_count ?? 0)} />
        <Card label="Passport" value={dash?.passport_id ? dash.passport_id.slice(0, 12) : 'None'} />
      </div>
    </div>
  )
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4">
      <div className="text-xs text-[#64748b] mb-1">{label}</div>
      <div className="text-white font-semibold text-sm truncate">{value}</div>
    </div>
  )
}
