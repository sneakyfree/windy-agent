import { useState, useEffect } from 'react'
import { api } from '../hooks/useApi'

const NAV_ITEMS = [
  { id: 'home', label: 'Home', icon: '🏠' },
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'personality', label: 'Personality', icon: '🎛️' },
  { id: 'memory', label: 'Memory', icon: '🧠' },
  { id: 'skills', label: 'Skills', icon: '⚡' },
  { id: 'identity', label: 'Identity', icon: '🪪' },
  { id: 'costs', label: 'Costs', icon: '💰' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
]

interface LayoutProps {
  page: string
  setPage: (p: string) => void
  children: React.ReactNode
}

export default function Layout({ page, setPage, children }: LayoutProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [brainConnected, setBrainConnected] = useState(false)

  useEffect(() => {
    const check = () => {
      api<{ brain_connected: boolean }>('/api/health')
        .then(d => setBrainConnected(d.brain_connected))
        .catch(() => setBrainConnected(false))
    }
    check()
    const iv = setInterval(check, 10000)
    return () => clearInterval(iv)
  }, [])

  return (
    <>
      {/* Mobile header */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-50 bg-[#111827] border-b border-[#1e293b] px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🪰</span>
          <span className="font-bold text-[#00d4ff]">Windy Fly</span>
        </div>
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="text-2xl p-1"
        >
          {menuOpen ? '✕' : '☰'}
        </button>
      </div>

      {/* Mobile menu overlay */}
      {menuOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/60"
          onClick={() => setMenuOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed top-0 left-0 bottom-0 w-56 bg-[#111827] border-r border-[#1e293b]
        flex flex-col z-50 transition-transform duration-200
        ${menuOpen ? 'translate-x-0' : '-translate-x-full'}
        md:translate-x-0 md:static md:z-auto
      `}>
        {/* Logo */}
        <div className="px-5 py-5 flex items-center gap-2 border-b border-[#1e293b]">
          <span className="text-2xl">🪰</span>
          <span className="font-bold text-lg text-[#00d4ff]">Windy Fly</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV_ITEMS.map(item => (
            <button
              key={item.id}
              onClick={() => { setPage(item.id); setMenuOpen(false) }}
              className={`
                w-full text-left px-5 py-2.5 flex items-center gap-3 text-sm
                transition-colors hover:bg-[#1a2332]
                ${page === item.id
                  ? 'bg-[#1a2332] text-[#00d4ff] border-r-2 border-[#00d4ff]'
                  : 'text-[#94a3b8]'}
              `}
            >
              <span className="text-base">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>

        {/* Status */}
        <div className="px-5 py-3 border-t border-[#1e293b] text-xs text-[#64748b]">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${brainConnected ? 'bg-[#22c55e]' : 'bg-[#ef4444]'}`} />
            {brainConnected ? 'Brain connected' : 'Brain offline'}
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-h-screen md:ml-0 pt-14 md:pt-0">
        <div className="max-w-6xl mx-auto p-4 md:p-6">
          {children}
        </div>
      </main>
    </>
  )
}
