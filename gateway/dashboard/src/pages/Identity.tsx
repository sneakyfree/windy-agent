import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

interface DashData {
  agent_name?: string
  passport_id?: string
  passport_status?: string
  trust_score?: number
  email?: string
  phone?: string
  matrix_user?: string
  certificate_number?: string
  birth_certificate_path?: string
  neural_fingerprint?: string
}

export default function Identity() {
  const [data, setData] = useState<DashData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api<DashData>('/api/dashboard').then(setData).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-[#00d4ff] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Identity</h1>

      {/* Passport card */}
      <div className="bg-gradient-to-br from-[#111827] to-[#0f172a] rounded-2xl border border-[#1e293b] p-6 mb-6">
        <div className="text-center mb-6">
          <div className="text-5xl mb-3">🪰</div>
          <h2 className="text-xl font-bold text-white">{data?.agent_name || 'Windy Fly'}</h2>
          <p className="text-[#64748b] text-sm">Eternitas Digital Identity</p>
        </div>

        {data?.passport_id ? (
          <div className="bg-[#0a0e17] rounded-xl p-4 text-center mb-4">
            <div className="text-xs text-[#64748b] mb-1 uppercase tracking-wide">Passport Number</div>
            <div className="text-2xl font-mono font-bold text-[#00d4ff] tracking-wider">
              {data.passport_id}
            </div>
          </div>
        ) : (
          <div className="bg-[#0a0e17] rounded-xl p-4 text-center mb-4">
            <div className="text-[#64748b]">No passport issued</div>
            <div className="text-xs text-[#64748b] mt-1">Run <code className="text-[#00d4ff]">windy go</code> to hatch</div>
          </div>
        )}

        {/* Status badge */}
        <div className="flex justify-center">
          <span className={`px-3 py-1 rounded-full text-xs font-semibold ${
            data?.passport_status === 'active'
              ? 'bg-[#22c55e]/20 text-[#22c55e]'
              : 'bg-[#64748b]/20 text-[#64748b]'
          }`}>
            {data?.passport_status || 'pending'}
          </span>
        </div>
      </div>

      {/* Trust score */}
      {data?.trust_score !== undefined && (
        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4 mb-6">
          <h3 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-3">Trust Score</h3>
          <div className="flex items-center gap-4">
            <div className="flex-1 h-3 bg-[#1e293b] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-[#00d4ff]"
                style={{ width: `${data.trust_score}%` }}
              />
            </div>
            <span className="text-white font-bold">{data.trust_score}/100</span>
          </div>
        </div>
      )}

      {/* Contact info */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <InfoCard icon="📧" label="Windy Mail" value={data?.email} placeholder="Not provisioned" />
        <InfoCard icon="📱" label="Phone" value={data?.phone} placeholder="Not configured" />
        <InfoCard icon="💬" label="Windy Chat" value={data?.matrix_user} placeholder="Not connected" />
        <InfoCard icon="📜" label="Certificate" value={data?.certificate_number} placeholder="Not generated" />
      </div>

      {/* Neural fingerprint */}
      {data?.neural_fingerprint && (
        <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4">
          <h3 className="text-sm font-semibold text-[#64748b] uppercase tracking-wide mb-2">Neural Fingerprint</h3>
          <code className="text-xs text-[#00d4ff] break-all font-mono">{data.neural_fingerprint}</code>
        </div>
      )}
    </div>
  )
}

function InfoCard({ icon, label, value, placeholder }: {
  icon: string; label: string; value?: string; placeholder: string
}) {
  return (
    <div className="bg-[#111827] rounded-xl border border-[#1e293b] p-4">
      <div className="flex items-center gap-2 mb-1">
        <span>{icon}</span>
        <span className="text-xs text-[#64748b] uppercase tracking-wide">{label}</span>
      </div>
      <div className={`text-sm font-medium ${value ? 'text-white' : 'text-[#64748b]'}`}>
        {value || placeholder}
      </div>
    </div>
  )
}
