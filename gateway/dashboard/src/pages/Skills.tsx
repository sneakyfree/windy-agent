import { useState, useMemo } from 'react'
import { api, useApi } from '../hooks/useApi'

interface Skill {
  name: string
  language: string
  risk_level: string
  promoted: boolean
  code?: string
  description?: string
}

export default function Skills() {
  const { data: skillsResp, loading, error, reload } = useApi<{ skills: Skill[] }>('/api/skills')
  const skills = skillsResp?.skills ?? null
  const [expanded, setExpanded] = useState<string | null>(null)
  const [regressionRunning, setRegressionRunning] = useState(false)
  const [regressionResult, setRegressionResult] = useState<string | null>(null)

  const stats = useMemo(() => {
    if (!skills) return { total: 0, promoted: 0 }
    return {
      total: skills.length,
      promoted: skills.filter(s => s.promoted).length,
    }
  }, [skills])

  const handleRegression = async () => {
    setRegressionRunning(true)
    setRegressionResult(null)
    try {
      const res = await api<{ status: string; message?: string }>(
        '/api/skills/regression',
        { method: 'POST' }
      )
      setRegressionResult(res.message || res.status || 'Regression complete')
    } catch (e) {
      setRegressionResult(e instanceof Error ? e.message : 'Regression failed')
    } finally {
      setRegressionRunning(false)
    }
  }

  const riskColor = (level: string) => {
    switch (level.toLowerCase()) {
      case 'low': return 'bg-green-500/10 text-green-400'
      case 'medium': return 'bg-yellow-500/10 text-yellow-400'
      case 'high': return 'bg-red-500/10 text-red-400'
      default: return 'bg-[#1e293b] text-[#94a3b8]'
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Skills</h1>
          {skills && (
            <p className="text-sm text-[#64748b] mt-1">
              <span className="text-[#00d4ff] font-semibold">{stats.total}</span> total
              {' / '}
              <span className="text-[#00d4ff] font-semibold">{stats.promoted}</span> promoted
            </p>
          )}
        </div>
        <button
          onClick={handleRegression}
          disabled={regressionRunning}
          className="px-5 py-2.5 bg-[#00d4ff] text-[#0a0e17] font-medium rounded-lg hover:bg-[#00bfe0] disabled:opacity-40 disabled:cursor-not-allowed transition-colors self-start sm:self-auto"
        >
          {regressionRunning ? 'Running...' : 'Run Regression'}
        </button>
      </div>

      {/* Regression result banner */}
      {regressionResult && (
        <div className="bg-[#111827] border border-[#1e293b] rounded-lg px-4 py-3 text-sm text-[#e2e8f0] flex items-center justify-between">
          <span>{regressionResult}</span>
          <button
            onClick={() => setRegressionResult(null)}
            className="text-[#64748b] hover:text-white ml-3 transition-colors"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Loading / Error */}
      {loading && <p className="text-[#64748b] text-sm">Loading skills...</p>}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={reload} className="text-red-300 hover:text-white ml-3 underline text-xs transition-colors">
            Retry
          </button>
        </div>
      )}

      {/* Skills list */}
      {skills && skills.length === 0 && (
        <p className="text-[#64748b] text-sm">No skills registered yet.</p>
      )}

      {skills && skills.length > 0 && (
        <div className="grid gap-3">
          {skills.map(skill => {
            const isOpen = expanded === skill.name
            return (
              <div
                key={skill.name}
                className="bg-[#111827] border border-[#1e293b] rounded-lg overflow-hidden hover:border-[#00d4ff]/30 transition-colors"
              >
                <button
                  onClick={() => setExpanded(isOpen ? null : skill.name)}
                  className="w-full text-left px-4 py-3.5 flex flex-wrap items-center gap-2 sm:gap-3"
                >
                  <span className="text-[#e2e8f0] font-medium text-sm flex-1 min-w-0 truncate">
                    {skill.name}
                  </span>

                  {skill.promoted && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-[#00d4ff]/10 text-[#00d4ff] font-medium">
                      Promoted
                    </span>
                  )}

                  <span className="text-xs bg-[#1e293b] text-[#94a3b8] px-2 py-0.5 rounded">
                    {skill.language}
                  </span>

                  <span className={`text-xs px-2 py-0.5 rounded ${riskColor(skill.risk_level)}`}>
                    {skill.risk_level}
                  </span>

                  <span className="text-[#64748b] text-xs ml-1">
                    {isOpen ? '▲' : '▼'}
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-[#1e293b] px-4 py-3">
                    {skill.description && (
                      <p className="text-[#94a3b8] text-sm mb-3">{skill.description}</p>
                    )}
                    {skill.code ? (
                      <pre className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-4 text-sm text-[#e2e8f0] overflow-x-auto whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
                        {skill.code}
                      </pre>
                    ) : (
                      <p className="text-[#64748b] text-sm italic">No code available.</p>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
