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

  useEffect(() => {
    api<DailyData>('/api/cost/daily').then(setDaily).catch(() => {})
    api<MonthlyData>('/api/cost/monthly').then(setMonthly).catch(() => {})
    api<{ daily_budget?: number }>('/api/dashboard')
      .then(d => { if (d.daily_budget) setBudget(d.daily_budget) })
      .catch(() => {})
  }, [])

  const spend = daily?.daily_spend ?? 0
  const monthTotal = monthly?.total_usd ?? 0
  const models = monthly?.by_model ?? {}
  const modelEntries = Object.entries(models).sort((a, b) => b[1] - a[1])

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Costs</h1>

      {/* Overview cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Today</div>
          <div className="text-3xl font-bold text-white">${spend.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">of ${budget.toFixed(2)} budget</div>
          <div className="mt-3 h-2 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min((spend / budget) * 100, 100)}%`,
                background: spend / budget > 0.8 ? '#ef4444' : '#00d4ff',
              }}
            />
          </div>
        </div>

        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">This Month</div>
          <div className="text-3xl font-bold text-white">${monthTotal.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">{monthly?.month || 'Current month'}</div>
        </div>

        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Daily Budget</div>
          <div className="text-3xl font-bold text-white">${budget.toFixed(2)}</div>
          <div className="text-sm text-[#64748b]">per day limit</div>
        </div>
      </div>

      {/* Model breakdown */}
      <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5 mb-6">
        <h2 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-4">Cost by Model</h2>
        {modelEntries.length === 0 ? (
          <p className="text-[#64748b] text-sm">No model usage data available.</p>
        ) : (
          <div className="space-y-3">
            {modelEntries.map(([model, cost]) => (
              <div key={model}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-white">{model}</span>
                  <span className="text-[#00d4ff] font-mono">${cost.toFixed(4)}</span>
                </div>
                <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[#00d4ff]/60"
                    style={{ width: `${monthTotal > 0 ? (cost / monthTotal) * 100 : 0}%` }}
                  />
                </div>
              </div>
            ))}
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
