import { useState, useCallback } from 'react'
import { api, useApi } from '../hooks/useApi'

interface SearchResult {
  id: string
  content: string
  relevance: number
  timestamp: string
  type?: string
}

interface Moment {
  id: string
  summary: string
  participants?: string[]
  timestamp: string
  emotion?: string
}

interface Intent {
  id: string
  goal: string
  status: string
  created_at: string
  confidence?: number
}

interface DashboardResponse {
  dashboard: {
    memory: { total_nodes: number; total_episodes: number }
  }
}

interface MomentsResponse {
  moments: Moment[]
}

interface IntentsResponse {
  intents: Intent[]
}

export default function Memory() {
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)

  const { data: momentsResp, loading: momentsLoading } = useApi<MomentsResponse>('/api/moments?limit=20')
  const { data: intentsResp, loading: intentsLoading } = useApi<IntentsResponse>('/api/intents')
  const { data: dashResp } = useApi<DashboardResponse>('/api/dashboard')

  const moments = momentsResp?.moments ?? null
  const intents = intentsResp?.intents ?? null
  const dashboard = dashResp?.dashboard

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return
    setSearching(true)
    setSearchError(null)
    try {
      const data = await api<{ results: SearchResult[] } | SearchResult[]>(
        `/api/memory/search?q=${encodeURIComponent(query.trim())}&limit=20`
      )
      setResults(Array.isArray(data) ? data : data.results ?? [])
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }, [query])

  const handleDelete = (id: string) => {
    if (!confirm('Delete this memory? This cannot be undone.')) return
    api(`/api/memory/${id}`, { method: 'DELETE' })
      .then(() => setResults(prev => prev ? prev.filter(r => r.id !== id) : null))
      .catch(() => {})
  }

  return (
    <div className="space-y-6">
      {/* Header + stats */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <h1 className="text-2xl font-bold text-white">Memory</h1>
        {dashboard && (
          <div className="flex gap-4 text-sm text-[#94a3b8]">
            <span>
              <span className="text-[#00d4ff] font-semibold">{dashboard.memory.total_episodes}</span> episodes
            </span>
            <span>
              <span className="text-[#00d4ff] font-semibold">{dashboard.memory.total_nodes}</span> knowledge nodes
            </span>
          </div>
        )}
      </div>

      {/* Search */}
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          placeholder="Search memories..."
          className="flex-1 bg-[#111827] border border-[#1e293b] rounded-lg px-4 py-2.5 text-white placeholder-[#64748b] focus:outline-none focus:border-[#00d4ff] transition-colors"
        />
        <button
          onClick={handleSearch}
          disabled={searching || !query.trim()}
          className="px-5 py-2.5 bg-[#00d4ff] text-[#0a0e17] font-medium rounded-lg hover:bg-[#00bfe0] disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
        >
          {searching ? 'Searching...' : 'Search'}
        </button>
      </div>

      {/* Search results */}
      {searchError && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 rounded-lg px-4 py-3 text-sm">
          {searchError}
        </div>
      )}

      {results && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-white">
            Search Results
            <span className="ml-2 text-sm text-[#64748b] font-normal">({results.length})</span>
          </h2>
          {results.length === 0 ? (
            <p className="text-[#64748b] text-sm">No memories found for "{query}"</p>
          ) : (
            <div className="grid gap-3">
              {results.map(r => (
                <div
                  key={r.id}
                  className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 hover:border-[#00d4ff]/30 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-[#e2e8f0] text-sm leading-relaxed flex-1">
                      {r.content}
                    </p>
                    <button
                      onClick={() => handleDelete(r.id)}
                      className="text-[#64748b] hover:text-red-400 text-xs shrink-0 transition-colors"
                      title="Delete memory"
                    >
                      Delete
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3 text-xs text-[#64748b]">
                    <span>
                      Relevance: <span className="text-[#00d4ff]">{(r.relevance * 100).toFixed(0)}%</span>
                    </span>
                    {r.type && <span className="bg-[#1e293b] px-2 py-0.5 rounded">{r.type}</span>}
                    <span>{new Date(r.timestamp).toLocaleString()}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recent Moments */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-white">Recent Moments</h2>
        {momentsLoading ? (
          <p className="text-[#64748b] text-sm">Loading moments...</p>
        ) : !moments || moments.length === 0 ? (
          <p className="text-[#64748b] text-sm">No moments recorded yet.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {moments.map(m => (
              <div
                key={m.id}
                className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 hover:border-[#00d4ff]/30 transition-colors"
              >
                <p className="text-[#e2e8f0] text-sm">{m.summary}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#64748b]">
                  {m.emotion && (
                    <span className="bg-[#1e293b] px-2 py-0.5 rounded">{m.emotion}</span>
                  )}
                  {m.participants && m.participants.length > 0 && (
                    <span>{m.participants.join(', ')}</span>
                  )}
                  <span className="ml-auto">{new Date(m.timestamp).toLocaleDateString()}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Active Goals */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-white">Active Goals</h2>
        {intentsLoading ? (
          <p className="text-[#64748b] text-sm">Loading goals...</p>
        ) : !intents || intents.length === 0 ? (
          <p className="text-[#64748b] text-sm">No active goals detected.</p>
        ) : (
          <div className="grid gap-3">
            {intents.map(intent => (
              <div
                key={intent.id}
                className="bg-[#111827] border border-[#1e293b] rounded-lg p-4 flex flex-col sm:flex-row sm:items-center gap-2 hover:border-[#00d4ff]/30 transition-colors"
              >
                <div className="flex-1">
                  <p className="text-[#e2e8f0] text-sm font-medium">{intent.goal}</p>
                  <p className="text-xs text-[#64748b] mt-1">
                    {new Date(intent.created_at).toLocaleDateString()}
                    {intent.confidence != null && (
                      <span className="ml-2">Confidence: {(intent.confidence * 100).toFixed(0)}%</span>
                    )}
                  </p>
                </div>
                <span
                  className={`text-xs px-2.5 py-1 rounded-full font-medium self-start sm:self-auto ${
                    intent.status === 'active'
                      ? 'bg-[#00d4ff]/10 text-[#00d4ff]'
                      : intent.status === 'completed'
                        ? 'bg-green-500/10 text-green-400'
                        : 'bg-[#1e293b] text-[#94a3b8]'
                  }`}
                >
                  {intent.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
