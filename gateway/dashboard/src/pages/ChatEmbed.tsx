import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface DashData {
  matrix_user?: string
}

const CHAT_URL = import.meta.env.DEV
  ? 'http://localhost:8008'
  : 'https://chat.windyword.ai'

export default function ChatEmbed() {
  const [data, setData] = useState<DashData | null>(null)
  const [loading, setLoading] = useState(true)
  const [online, setOnline] = useState<boolean | null>(null)

  useEffect(() => {
    Promise.all([
      api<Record<string, unknown>>('/api/dashboard')
        .then(d => setData({ matrix_user: (d.matrix_user as string) ?? undefined }))
        .catch(() => {}),
      api<{ online: boolean }>('/api/chat/status')
        .then(d => setOnline(d.online))
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
          <span className="text-lg">💬</span>
          <span className="text-sm text-white font-medium">
            {data?.matrix_user || 'No chat identity'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${
            online === true ? 'bg-[#22c55e]' : online === false ? 'bg-[#ef4444]' : 'bg-[#64748b]'
          }`} />
          <span className="text-xs text-[#94a3b8]">
            {online === true ? 'Online' : online === false ? 'Offline' : 'Unknown'}
          </span>
        </div>
      </div>

      {/* Embedded chat */}
      <iframe
        src={CHAT_URL}
        className="flex-1 w-full border-0"
        title="Windy Chat"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  )
}
