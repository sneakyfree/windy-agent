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
        .then(d => setData({ email: (d.email as string) ?? undefined }))
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

      {/* Embedded mail */}
      <iframe
        src={MAIL_URL}
        className="flex-1 w-full border-0"
        title="Windy Mail"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  )
}
