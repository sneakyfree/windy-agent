import { useEffect, useState } from 'react'
import { api } from '../hooks/useApi'

type Band = 'exceptional' | 'good' | 'fair' | 'poor' | 'critical' | 'unknown'
type Clearance = 'registered' | 'verified' | 'cleared' | 'top_secret' | 'eternal' | 'unknown'

interface TrustBanner {
  status: string
  band: Band
  clearance_level: Clearance
  tier_multiplier: number
  integrity_score: number
  allowed_actions: string[]
  denied_actions: string[]
  dimensions: Record<string, number>
  band_multipliers: Record<string, number>
  clearance_unlocks: Record<string, string[]>
  passport: string
  cache_status: string
}

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
  trust_banner?: TrustBanner
}

export default function Identity() {
  const [data, setData] = useState<DashData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api<Record<string, unknown>>('/api/dashboard')
      .then(resp => {
        const flat: DashData = {}
        const src = resp as Record<string, unknown>
        const dashboard = (src.dashboard as Record<string, unknown>) || src
        flat.agent_name = (src.agent_name as string) ?? undefined
        flat.passport_id = (src.passport_id as string) ?? undefined
        flat.passport_status = (src.passport_status as string) ?? undefined
        flat.trust_score = (src.trust_score as number) ?? undefined
        flat.email = (src.email as string) ?? undefined
        flat.phone = (src.phone as string) ?? undefined
        flat.matrix_user = (src.matrix_user as string) ?? undefined
        flat.certificate_number = (src.certificate_number as string) ?? undefined
        flat.neural_fingerprint = (src.neural_fingerprint as string) ?? undefined
        flat.trust_banner = (dashboard.trust_banner as TrustBanner) ?? undefined
        setData(flat)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
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

      {/* Trust banner — live from Eternitas */}
      {data?.trust_banner && <TrustPanel banner={data.trust_banner} />}

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

const BAND_ORDER: Band[] = ['exceptional', 'good', 'fair', 'poor', 'critical']
const CLEARANCE_ORDER: Clearance[] = ['registered', 'verified', 'cleared', 'top_secret', 'eternal']

const BAND_COLOR: Record<Band, { bar: string; chip: string; text: string }> = {
  exceptional: { bar: 'bg-[#22c55e]', chip: 'bg-[#22c55e]/20', text: 'text-[#22c55e]' },
  good:        { bar: 'bg-[#00d4ff]', chip: 'bg-[#00d4ff]/20', text: 'text-[#00d4ff]' },
  fair:        { bar: 'bg-[#eab308]', chip: 'bg-[#eab308]/20', text: 'text-[#eab308]' },
  poor:        { bar: 'bg-[#f97316]', chip: 'bg-[#f97316]/20', text: 'text-[#f97316]' },
  critical:    { bar: 'bg-[#ef4444]', chip: 'bg-[#ef4444]/20', text: 'text-[#ef4444]' },
  unknown:     { bar: 'bg-[#64748b]', chip: 'bg-[#64748b]/20', text: 'text-[#64748b]' },
}

const ACTION_LABEL: Record<string, string> = {
  read: 'Read',
  send: 'Send (email, chat, uploads)',
  execute: 'Run commands',
  dm_bots: 'DM other bots',
  install_packages: 'Install packages',
  commit_push: 'Commit + push',
  broadcast: 'Broadcast',
  mention_strangers: 'Mention strangers',
  bypass_rate_caps: 'Bypass rate caps',
}

function TrustPanel({ banner }: { banner: TrustBanner }) {
  const currentBand = (banner.band || 'unknown') as Band
  const currentClearance = (banner.clearance_level || 'unknown') as Clearance
  const effectiveMultiplier = banner.tier_multiplier ?? 0
  const integrity = Math.max(0, Math.min(1000, banner.integrity_score ?? 0))
  const color = BAND_COLOR[currentBand] ?? BAND_COLOR.unknown
  const allowed = new Set(banner.allowed_actions ?? [])

  return (
    <div className="bg-gradient-to-br from-[#111827] to-[#0f172a] rounded-2xl border border-[#1e293b] p-6 mb-6">
      {/* Headline */}
      <div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
        <div>
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Trust band</div>
          <div className="flex items-baseline gap-3">
            <span className={`text-3xl font-bold ${color.text} capitalize`}>{currentBand}</span>
            <span className="text-sm text-[#64748b]">status: {banner.status || 'unknown'}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-[#64748b] uppercase tracking-wide mb-1">Tier multiplier</div>
          <div className="text-3xl font-bold text-white">×{effectiveMultiplier.toFixed(1)}</div>
          <div className="text-xs text-[#64748b]">lower of band + clearance</div>
        </div>
      </div>

      {/* Integrity score */}
      <div className="mb-6">
        <div className="flex justify-between items-baseline mb-2">
          <span className="text-xs text-[#64748b] uppercase tracking-wide">Integrity score</span>
          <span className="text-white font-bold">{integrity} / 1000</span>
        </div>
        <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${color.bar}`} style={{ width: `${(integrity / 1000) * 100}%` }} />
        </div>
      </div>

      {/* 5 dimensions */}
      {banner.dimensions && Object.keys(banner.dimensions).length > 0 && (
        <div className="mb-6 grid grid-cols-5 gap-2">
          {['honesty', 'reliability', 'compliance', 'safety', 'reputation'].map(dim => {
            const v = banner.dimensions[dim] ?? 0
            return (
              <div key={dim} className="bg-[#0a0e17] rounded-lg p-2 text-center">
                <div className="text-[10px] text-[#64748b] uppercase tracking-wide mb-1">{dim}</div>
                <div className="text-sm font-bold text-white">{v}</div>
                <div className="h-1 bg-[#1e293b] rounded-full mt-1 overflow-hidden">
                  <div className={`h-full ${color.bar}`} style={{ width: `${(v / 1000) * 100}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Band ladder */}
      <div className="mb-6">
        <div className="text-xs text-[#64748b] uppercase tracking-wide mb-2">Band ladder</div>
        <div className="grid grid-cols-5 gap-2">
          {BAND_ORDER.map(b => {
            const mult = banner.band_multipliers?.[b] ?? 0
            const active = b === currentBand
            const c = BAND_COLOR[b]
            return (
              <div
                key={b}
                className={`rounded-lg p-3 text-center border ${
                  active ? `${c.chip} border-current ${c.text}` : 'bg-[#0a0e17] border-[#1e293b] text-[#64748b]'
                }`}
              >
                <div className="text-[11px] capitalize mb-1">{b}</div>
                <div className="text-lg font-bold">×{mult.toFixed(1)}</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Clearance ladder — each tier and what it adds */}
      <div className="mb-6">
        <div className="text-xs text-[#64748b] uppercase tracking-wide mb-2">Clearance ladder — what each tier unlocks</div>
        <div className="space-y-2">
          {CLEARANCE_ORDER.map((tier, i) => {
            const cumulative = banner.clearance_unlocks?.[tier] ?? []
            const previous = i === 0 ? [] : banner.clearance_unlocks?.[CLEARANCE_ORDER[i - 1]] ?? []
            const added = cumulative.filter(a => !previous.includes(a))
            const active = tier === currentClearance
            return (
              <div
                key={tier}
                className={`rounded-lg p-3 border flex items-center gap-3 ${
                  active ? 'bg-[#00d4ff]/10 border-[#00d4ff]/60' : 'bg-[#0a0e17] border-[#1e293b]'
                }`}
              >
                <div className={`w-24 text-xs font-semibold capitalize ${active ? 'text-[#00d4ff]' : 'text-[#64748b]'}`}>
                  {tier.replace('_', ' ')}
                </div>
                <div className="flex flex-wrap gap-1.5 flex-1">
                  {added.length === 0 ? (
                    <span className="text-xs text-[#64748b] italic">(no new actions added)</span>
                  ) : (
                    added.map(a => (
                      <span
                        key={a}
                        className="text-[11px] px-2 py-0.5 rounded bg-[#1e293b] text-white font-mono"
                      >
                        +{a}
                      </span>
                    ))
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Per-action allow/deny right now */}
      <div>
        <div className="text-xs text-[#64748b] uppercase tracking-wide mb-2">What this agent can do right now</div>
        <div className="flex flex-wrap gap-2">
          {Object.keys(ACTION_LABEL).map(action => {
            const can = allowed.has(action)
            return (
              <span
                key={action}
                className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                  can ? 'bg-[#22c55e]/15 text-[#22c55e]' : 'bg-[#ef4444]/10 text-[#ef4444] line-through opacity-70'
                }`}
                title={`${action} — ${can ? 'allowed' : 'denied'} at current band+clearance`}
              >
                {can ? '✓' : '✗'} {ACTION_LABEL[action] || action}
              </span>
            )
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="mt-4 pt-3 border-t border-[#1e293b] flex justify-between text-[11px] text-[#64748b]">
        <span>Passport: <span className="font-mono text-[#94a3b8]">{banner.passport || '—'}</span></span>
        <span>Cache: {banner.cache_status === 'ok' ? '✓ warm' : '○ empty'}</span>
      </div>
    </div>
  )
}
