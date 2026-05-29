import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface DashData {
  email?: string
}

const MAIL_URL = import.meta.env.DEV
  ? 'http://localhost:5173'
  : 'https://windymail.ai'

export default function Email() {
  const [data, setData] = useState<DashData | null>(null)
  const [loading, setLoading] = useState(true)
  const [delegated, setDelegated] = useState<boolean | null>(null)

  useEffect(() => {
    Promise.all([
      api<Record<string, unknown>>('/api/dashboard')
        .then(d => {
          const dash = (d.dashboard as Record<string, unknown>) || d
          const id = (dash.identity as Record<string, unknown>) || {}
          setData({ email: (id.email as string) ?? undefined })
        })
        .catch(() => {}),
      api<{ delegated: boolean }>('/api/email/delegation')
        .then(d => setDelegated(d.delegated))
        .catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] md:h-screen">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#111827] border-b border-[#1e293b] shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg">📧</span>
          <span className="text-sm text-white font-medium">
            {data?.email || 'No email provisioned'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${
            delegated === true ? 'bg-[#22c55e]' : delegated === false ? 'bg-[#ef4444]' : 'bg-[#64748b]'
          }`} />
          <span className="text-xs text-[#94a3b8]">
            {delegated === true
              ? 'Delegation active'
              : delegated === false
                ? 'No delegation'
                : 'Unknown'}
          </span>
        </div>
      </div>

      {/* Embedded mail — only when provisioned. Otherwise the iframe loads
          the external mail host and surfaces its raw auth/error page. */}
      {data?.email || delegated === true ? (
        <iframe
          src={MAIL_URL}
          className="flex-1 w-full border-0"
          title="Windy Mail"
          allow="clipboard-read; clipboard-write"
        />
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-4">
          <div className="text-5xl">📧</div>
          <div className="text-[#e2e8f0] text-base font-medium">No email provisioned yet</div>
          <div className="text-[#64748b] text-sm max-w-md">
            Windy Mail gives your agent its own inbox. Run{' '}
            <code className="bg-[#1e293b] px-1.5 py-0.5 rounded text-[#94a3b8]">windy go</code>{' '}
            to hatch an identity and provision email.
          </div>
        </div>
      )}
    </div>
  )
}
