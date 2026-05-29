import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

export default function Settings() {
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [ecosystem, setEcosystem] = useState<Record<string, string>>({})
  const [status, setStatus] = useState('')

  useEffect(() => {
    // /api/dashboard returns { dashboard: { config, ecosystem, ... } }.
    // The old code read these off the top level (always undefined), so
    // every field fell back to a hardcoded default ("not set"/0.7/etc.).
    api<{ dashboard?: { config?: Record<string, unknown>; ecosystem?: Record<string, string> } }>('/api/dashboard')
      .then(d => {
        setConfig(d.dashboard?.config ?? {})
        setEcosystem(d.dashboard?.ecosystem ?? {})
      })
      .catch(() => {})
  }, [])

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Settings</h1>

      {/* Agent config */}
      <Section title="Agent Configuration">
        <InfoRow label="Model" value={String(config.model || 'not set')} />
        <InfoRow label="Temperature" value={String(config.temperature ?? '0.7')} />
        <InfoRow label="Max Tokens" value={String(config.max_tokens ?? '2000')} />
        <InfoRow label="Daily Budget" value={`$${config.daily_budget ?? '5.00'}`} />
        <p className="text-xs text-[#64748b] mt-3">
          Edit <code className="text-[#00d4ff]">windyfly.toml</code> to change these settings.
        </p>
      </Section>

      {/* Ecosystem URLs */}
      <Section title="Ecosystem URLs">
        <InfoRow label="Eternitas" value={ecosystem.eternitas_url || 'not set'} />
        <InfoRow label="Windy Mail" value={ecosystem.windy_mail_url || 'not set'} />
        <InfoRow label="Matrix" value={ecosystem.matrix_homeserver || 'not set'} />
        <InfoRow label="Windy Cloud" value={ecosystem.windy_cloud_url || 'not set'} />
        <InfoRow label="Windy Word" value={ecosystem.windy_pro_url || 'not set'} />
      </Section>

      {/* Updates */}
      <Section title="Updates">
        <div className="flex items-center justify-between py-2">
          <div>
            <div className="text-sm text-white">Check for updates</div>
            <div className="text-xs text-[#64748b]">Checks PyPI for new versions on startup</div>
          </div>
          <button
            onClick={async () => {
              setStatus('Checking...')
              try {
                const r = await api<{ response: string }>('/api/chat', {
                  method: 'POST',
                  body: JSON.stringify({ message: '/update', session_id: 'settings' }),
                })
                setStatus(r.response || 'Done')
              } catch {
                setStatus('Check failed')
              }
            }}
            className="px-4 py-2 bg-[#00d4ff] text-black rounded-lg text-sm font-semibold hover:bg-[#00b8d4] transition-colors"
          >
            Check Now
          </button>
        </div>
        {status && <p className="text-sm text-[#64748b] mt-2">{status}</p>}
      </Section>

      {/* Export/Import */}
      <Section title="Data">
        <div className="flex gap-3 flex-wrap">
          <button className="px-4 py-2 bg-[#111827] border border-[#1e293b] rounded-lg text-sm hover:border-[#00d4ff] transition-colors">
            Export Agent Data
          </button>
          <button className="px-4 py-2 bg-[#111827] border border-[#1e293b] rounded-lg text-sm hover:border-[#00d4ff] transition-colors">
            Import Agent Data
          </button>
        </div>
      </Section>

      {/* Danger zone */}
      <div className="bg-[#111827] rounded-xl border border-[#ef4444]/30 p-5 mt-6">
        <h2 className="text-sm font-semibold text-[#ef4444] uppercase tracking-wide mb-3">Danger Zone</h2>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-white">Reset Agent</div>
              <div className="text-xs text-[#64748b]">Reset configuration but keep memories</div>
            </div>
            <button
              onClick={() => { if (confirm('Reset agent configuration? Memories will be preserved.')) alert('Run: windy reset --soft') }}
              className="px-4 py-2 border border-[#ef4444]/50 text-[#ef4444] rounded-lg text-sm hover:bg-[#ef4444]/10 transition-colors"
            >
              Soft Reset
            </button>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-white">Delete All Memories</div>
              <div className="text-xs text-[#64748b]">Permanently erase all agent data</div>
            </div>
            <button
              onClick={() => { if (confirm('DELETE all memories? This cannot be undone.')) alert('Run: windy reset --hard') }}
              className="px-4 py-2 border border-[#ef4444]/50 text-[#ef4444] rounded-lg text-sm hover:bg-[#ef4444]/10 transition-colors"
            >
              Hard Reset
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-5 mb-4">
      <h2 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-3">{title}</h2>
      {children}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2 border-b border-[#1e293b] last:border-0">
      <span className="text-sm text-[#94a3b8]">{label}</span>
      <span className="text-sm text-white font-mono truncate max-w-[200px]">{value}</span>
    </div>
  )
}
