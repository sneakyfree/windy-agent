import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface DailyData { daily_spend?: number; _offline?: boolean }
interface MonthlyData {
  month?: string
  total_usd?: number
  by_model?: Record<string, number>
  _offline?: boolean
}

export default function Costs() {
  const [daily, setDaily] = useState<DailyData | null>(null)
  const [monthly, setMonthly] = useState<MonthlyData | null>(null)
  const [budget, setBudget] = useState(5)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api<DailyData>('/api/cost/daily').then(setDaily).catch(() => {}),
      api<MonthlyData>('/api/cost/monthly').then(setMonthly).catch(() => {}),
      api<{ daily_budget?: number }>('/api/dashboard')
        .then(d => { if (d.daily_budget) setBudget(d.daily_budget) })
        .catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  const spend = daily?.daily_spend ?? 0
  const monthTotal = monthly?.total_usd ?? 0
  const models = monthly?.by_model ?? {}
  const modelEntries = Object.entries(models).sort((a, b) => b[1] - a[1])
  const maxModel = modelEntries.length > 0 ? modelEntries[0][1] : 1

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Costs</h1>

      {/* Overview cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Today</div>
          <div className="text-3xl font-bold text-white">${spend.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">of ${budget.toFixed(2)} budget</div>
          <div className="mt-3 h-2.5 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{
                width: `${Math.min((spend / budget) * 100, 100)}%`,
                background: spend / budget > 0.8 ? '#ef4444' : spend / budget > 0.5 ? '#eab308' : '#00d4ff',
              }}
            />
          </div>
          <div className="text-xs text-[#64748b] mt-1">{((spend / budget) * 100).toFixed(0)}% used</div>
        </div>

        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">This Month</div>
          <div className="text-3xl font-bold text-white">${monthTotal.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">{monthly?.month || 'Current month'}</div>
          <div className="text-xs text-[#64748b] mt-3">
            ~${(monthTotal / Math.max(new Date().getDate(), 1)).toFixed(2)}/day avg
          </div>
        </div>

        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Daily Budget</div>
          <div className="text-3xl font-bold text-white">${budget.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">per day limit</div>
          <div className="text-xs text-[#64748b] mt-3">~${(budget * 30).toFixed(0)}/month max</div>
        </div>
      </div>

      {/* Model breakdown with bar chart */}
      <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5 mb-6">
        <h2 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-4">Cost by Model</h2>
        {modelEntries.length === 0 ? (
          <div className="text-center py-8">
            <div className="text-3xl mb-2">📊</div>
            <p className="text-[#64748b] text-sm">No model usage yet. Start chatting to see cost breakdown.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {modelEntries.map(([model, cost]) => {
              const pct = maxModel > 0 ? (cost / maxModel) * 100 : 0
              const colors = ['#00d4ff', '#22c55e', '#eab308', '#ef4444', '#a855f7', '#f97316']
              const color = colors[modelEntries.findIndex(e => e[0] === model) % colors.length]
              return (
                <div key={model}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-white flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: color }} />
                      {model}
                    </span>
                    <span className="font-mono" style={{ color }}>${cost.toFixed(4)}</span>
                  </div>
                  <div className="h-3 bg-[#1e293b] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${pct}%`, background: color, opacity: 0.8 }}
                    />
                  </div>
                </div>
              )
            })}
            <div className="border-t border-[#1e293b] pt-3 mt-3 flex justify-between text-sm">
              <span className="text-[#64748b]">Total</span>
              <span className="text-white font-mono font-bold">${monthTotal.toFixed(4)}</span>
            </div>
          </div>
        )}
      </div>

      {/* Budget tip */}
      <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4 text-sm text-[#64748b]">
        💡 Adjust your daily budget in <code className="text-[#00d4ff]">windyfly.toml</code> under <code className="text-[#00d4ff]">[costs] daily_budget_usd</code>
      </div>
    </div>
  )
}
