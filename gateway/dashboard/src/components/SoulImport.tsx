import { useState } from 'react'
import { api } from '../hooks/useApi'

// Soul import from the dashboard (strand P2.21.13). The file goes up as the raw request body; the
// gateway keeps it in a private RAM-backed file and the agent previews it. Nothing is kept until
// Approve. The preview holds text from the export, so it is shown as plain text, never as HTML.
const MAX_MB = 90

interface Approved {
  imported: number
  flagged: number
  skipped: number
  tone?: Record<string, number>
}

export default function SoulImport() {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState<string | null>(null)
  const [preview, setPreview] = useState('')
  const [message, setMessage] = useState('')
  const [done, setDone] = useState<Approved | null>(null)

  const reset = () => { setToken(null); setPreview(''); setFile(null) }

  const upload = async () => {
    if (!file) return
    setMessage(''); setDone(null)
    if (file.size > MAX_MB * 1024 * 1024) {
      setMessage(`That file is over ${MAX_MB} MB. Upload only the conversations.json from the export.`)
      return
    }
    setBusy(true)
    try {
      const res = await fetch('/api/soul/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name).slice(0, 200) },
        body: file,
      })
      const out = await res.json().catch(() => ({})) as { token?: string; preview?: string; error?: string }
      if (out.token && out.preview) { setToken(out.token); setPreview(out.preview) }
      else setMessage(out.error || `The upload failed (${res.status}). Nothing was kept.`)
    } catch {
      setMessage('The upload failed. Nothing was kept.')
    } finally {
      setBusy(false)
    }
  }

  const finish = async (route: 'import' | 'reject') => {
    if (!token) return
    setBusy(true)
    try {
      const out = await api<Approved & { error?: string; rejected?: boolean }>(`/api/soul/${route}`, {
        method: 'POST', body: JSON.stringify({ token }),
      })
      if (out.error) setMessage(out.error)
      else if (route === 'import') setDone(out)
      else setMessage('Cancelled. Nothing was kept, and the file is wiped.')
    } catch {
      setMessage('The agent did not answer. If it restarted, the upload is wiped; upload it again.')
    } finally {
      reset()
      setBusy(false)
    }
  }

  return (
    <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4">
      <p className="text-xs text-[#64748b] mb-3">
        A ChatGPT <code>conversations.json</code>, or a zip of an OpenClaw, Hermes or ChatGPT export, up to {MAX_MB} MB.
        You see what was found first; nothing is kept unless you approve, and the file is wiped either way.
      </p>
      {!token && (
        <div className="flex items-center gap-3 flex-wrap">
          <input type="file" accept=".json,.zip,application/json,application/zip"
                 onChange={e => setFile(e.target.files?.[0] ?? null)} className="text-sm text-[#94a3b8]" />
          <button onClick={upload} disabled={!file || busy}
                  className="px-3 py-1.5 rounded-lg border border-[#1e293b] text-sm hover:border-[#00d4ff] hover:text-[#00d4ff] disabled:opacity-50">
            {busy ? 'Reading…' : 'Upload and preview'}
          </button>
        </div>
      )}
      {token && (
        <div>
          <pre className="whitespace-pre-wrap text-xs text-[#cbd5e1] bg-[#0b1220] rounded-lg p-3 mb-3 max-h-80 overflow-auto">{preview}</pre>
          <div className="flex gap-2">
            <button onClick={() => finish('import')} disabled={busy}
                    className="px-3 py-1.5 rounded-lg border border-[#00d4ff]/60 text-[#00d4ff] text-sm hover:bg-[#00d4ff]/10 disabled:opacity-50">
              Approve
            </button>
            <button onClick={() => finish('reject')} disabled={busy}
                    className="px-3 py-1.5 rounded-lg border border-[#1e293b] text-sm hover:border-[#ef4444] hover:text-[#ef4444] disabled:opacity-50">
              Reject
            </button>
          </div>
        </div>
      )}
      {message && <p className="text-sm text-[#eab308] mt-3">{message}</p>}
      {done && (
        <div className="text-sm text-[#cbd5e1] mt-3 space-y-1">
          <p>Imported {done.imported}. {done.skipped} skipped. The file is wiped.</p>
          {done.flagged > 0 && (
            <p>{done.flagged} sensitive item{done.flagged === 1 ? ' waits' : 's wait'} for your review and
              {done.flagged === 1 ? ' is' : ' are'} not used until you keep {done.flagged === 1 ? 'it' : 'them'}:
              type <code>/soul review</code> in chat.</p>
          )}
          {done.tone && Object.keys(done.tone).length > 0 && (
            <p>The export's tone was {Object.entries(done.tone).map(([k, v]) => `${k} ${v}`).join(', ')}.
              To use it, type <code>/soul apply</code> in chat within 30 minutes.</p>
          )}
        </div>
      )}
    </div>
  )
}
