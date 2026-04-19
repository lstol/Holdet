'use client'
import { useEffect, useState, useCallback } from 'react'
import { createClient, Stage, Rider, ProbSnapshot, GameState, RiderAdjustment } from '@/lib/supabase'
import { AlertTriangle, Zap, RefreshCw, Play, ChevronDown, ChevronUp } from 'lucide-react'

const RACE = 'giro_2026'
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function fmt(v: number | null, pct = false) {
  if (v == null) return '—'
  return pct ? `${(v * 100).toFixed(0)}%` : v.toLocaleString('da-DK')
}

function fmtK(v: number | null) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${(v / 1000).toFixed(0)}k`
}

function SourceBadge({ source }: { source: string }) {
  const cls: Record<string, string> = {
    model:        'bg-zinc-700 text-zinc-300',
    odds:         'bg-blue-900 text-blue-300',
    intelligence: 'bg-purple-900 text-purple-300',
    manual:       'bg-orange-900 text-orange-300',
    adjusted:     'bg-orange-900 text-orange-300',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${cls[source] ?? cls.model}`}>
      {source.slice(0, 3).toUpperCase()}
    </span>
  )
}

function StageBadge({ type }: { type: string }) {
  const colours: Record<string, string> = {
    flat:     'bg-green-900 text-green-300',
    hilly:    'bg-yellow-900 text-yellow-300',
    mountain: 'bg-red-900 text-red-300',
    itt:      'bg-purple-900 text-purple-300',
    ttt:      'bg-blue-900 text-blue-300',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-semibold uppercase ${colours[type] ?? 'bg-zinc-700 text-zinc-300'}`}>
      {type}
    </span>
  )
}

type IntelligenceResult = {
  stage_summary: string
  rider_adjustments: RiderAdjustment[]
  dns_risks: string[]
  stage_notes: string
  sources_used: string[]
}

type ProfileRec = {
  transfers: { action: string; rider_id: string; rider_name: string; value: number; fee: number; reasoning: string }[]
  captain: string
  captain_name: string
  expected_value: number
  upside_90pct: number
  downside_10pct: number
  transfer_cost: number
  reasoning: string
}

type BriefResult = {
  stage_number: number
  stage_type: string
  start_location: string
  finish_location: string
  current_team_ev: number
  stages_remaining: number
  captain: string
  suggested_profile: string | null
  suggested_profile_reason: string
  profiles: Record<string, ProfileRec>
  team_sims: { holdet_id: string; name: string; expected_value: number; downside_10pct: number; upside_90pct: number; is_captain: boolean }[]
  dns_alerts: { name: string; status: string }[]
}

const PROFILE_LABELS: Record<string, string> = {
  anchor:     'ANCHOR',
  balanced:   'BALANCED',
  aggressive: 'AGGRESSIVE',
  all_in:     'ALL-IN',
}

const PROFILE_COLOURS: Record<string, string> = {
  anchor:     'text-blue-400',
  balanced:   'text-green-400',
  aggressive: 'text-orange-400',
  all_in:     'text-red-400',
}

export default function BriefingPage() {
  const sb = createClient()
  const [stage, setStage] = useState<Stage | null>(null)
  const [gs, setGs] = useState<GameState | null>(null)
  const [riders, setRiders] = useState<Rider[]>([])
  const [probs, setProbs] = useState<Record<string, ProbSnapshot>>({})
  const [intelligence, setIntelligence] = useState<IntelligenceResult | null>(null)
  const [intelligenceLoading, setIntelligenceLoading] = useState(false)
  const [intelligenceError, setIntelligenceError] = useState<string | null>(null)
  const [accepted, setAccepted] = useState<Set<string>>(new Set())

  // FastAPI actions
  const [lookAhead, setLookAhead] = useState(5)
  const [captainOverride, setCaptainOverride] = useState('')
  const [briefLoading, setBriefLoading] = useState(false)
  const [ingestLoading, setIngestLoading] = useState(false)
  const [briefResult, setBriefResult] = useState<BriefResult | null>(null)
  const [briefError, setBriefError] = useState<string | null>(null)
  const [ingestMsg, setIngestMsg] = useState<string | null>(null)
  const [showProfiles, setShowProfiles] = useState(false)

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      if (!user) return

      const [stagesRes, gsRes, ridersRes] = await Promise.all([
        sb.from('stages').select('*').eq('race', RACE).order('number'),
        sb.from('game_state').select('*').eq('user_id', user.id).eq('race', RACE).single(),
        sb.from('riders').select('*').eq('user_id', user.id).eq('race', RACE),
      ])

      const gameState = gsRes.data as GameState | null
      setGs(gameState)
      const currentStage = gameState?.current_stage ?? 1
      const stageData = (stagesRes.data as Stage[])?.find(s => s.number === currentStage)
      setStage(stageData ?? null)
      setRiders((ridersRes.data as Rider[]) ?? [])

      if (gameState) {
        const { data: probData } = await sb
          .from('prob_snapshots').select('*')
          .eq('user_id', user.id).eq('race', RACE).eq('stage_number', currentStage)
        const map: Record<string, ProbSnapshot> = {}
        for (const p of (probData ?? []) as ProbSnapshot[]) map[p.rider_id] = p
        setProbs(map)
      }
    }
    load()
  }, [])

  const refreshRiders = useCallback(async () => {
    setIngestLoading(true)
    setIngestMsg(null)
    try {
      const res = await fetch(`${API}/ingest`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Ingest failed')
      setIngestMsg(`✓ ${d.riders_count} riders refreshed. Bank: ${Number(d.bank).toLocaleString('da-DK')} kr${d.dns_alerts?.length ? ` · ⚠ ${d.dns_alerts.map((a: {name:string}) => a.name).join(', ')} DNS` : ''}`)
    } catch (e: unknown) {
      setIngestMsg(`✗ ${e instanceof Error ? e.message : 'Server not running? Start with: bash scripts/start_api.sh'}`)
    } finally {
      setIngestLoading(false)
    }
  }, [])

  const runBriefing = useCallback(async () => {
    if (!stage) return
    setBriefLoading(true)
    setBriefError(null)
    setBriefResult(null)
    try {
      const res = await fetch(`${API}/brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stage: stage.number,
          look_ahead: lookAhead,
          captain_override: captainOverride || null,
        }),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Brief failed')
      setBriefResult(d)
      setShowProfiles(true)
    } catch (e: unknown) {
      setBriefError(e instanceof Error ? e.message : 'Server not running? Start with: bash scripts/start_api.sh')
    } finally {
      setBriefLoading(false)
    }
  }, [stage, lookAhead, captainOverride])

  const gatherIntelligence = useCallback(async () => {
    if (!stage || !gs) return
    setIntelligenceLoading(true)
    setIntelligenceError(null)
    setIntelligence(null)
    try {
      const myTeamNames = riders
        .filter(r => gs.my_team?.includes(r.holdet_id))
        .map(r => `${r.name} (${r.team})`)
        .join(', ')
      const res = await fetch('/api/intelligence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stage_number: stage.number,
          start: stage.start_location,
          finish: stage.finish_location,
          stage_type: stage.stage_type,
          distance_km: stage.distance_km,
          profile_score: stage.profile_score,
          gradient_final_km: stage.gradient_final_km,
          my_team: myTeamNames,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setIntelligence(data)
      const { data: { user } } = await sb.auth.getUser()
      if (user) {
        await sb.from('intelligence_log').insert({
          user_id: user.id, race: RACE, stage_number: stage.number, ...data,
        })
      }
    } catch (e: unknown) {
      setIntelligenceError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setIntelligenceLoading(false)
    }
  }, [stage, gs, riders])

  const acceptAdjustment = (name: string) => {
    setAccepted(prev => { const s = new Set(prev); s.add(name); return s })
    const adj = intelligence?.rider_adjustments.find(a => a.name === name)
    if (!adj) return
    const rider = riders.find(r => r.name?.toLowerCase().includes(name.toLowerCase()))
    if (!rider) return
    setProbs(prev => ({
      ...prev,
      [rider.holdet_id]: {
        ...(prev[rider.holdet_id] ?? { rider_id: rider.holdet_id } as ProbSnapshot),
        p_win: adj.p_win_suggested, p_top3: adj.p_top3_suggested,
        p_top15: adj.p_top15_suggested, p_dnf: adj.p_dnf_suggested,
        source: 'intelligence',
      },
    }))
  }

  const dnsDNFTeamRiders = riders.filter(
    r => gs?.my_team?.includes(r.holdet_id) && (r.status === 'dns' || r.status === 'dnf')
  )

  const teamRiders = riders.filter(r => gs?.my_team?.includes(r.holdet_id))

  const displayRiders = riders
    .filter(r => gs?.my_team?.includes(r.holdet_id) || !!probs[r.holdet_id])
    .sort((a, b) => (probs[b.holdet_id]?.p_win ?? 0) - (probs[a.holdet_id]?.p_win ?? 0))

  if (!stage) {
    return (
      <div className="text-zinc-500 mt-12 text-center">
        No stage data — run <code className="text-orange-400">sync_to_supabase.py</code> after ingest.
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* Stage header */}
      <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <span className="text-2xl font-bold text-white">Stage {stage.number}</span>
          <StageBadge type={stage.stage_type} />
          {stage.date && <span className="text-zinc-400 text-sm">{stage.date}</span>}
        </div>
        <p className="text-zinc-300 text-lg">
          {stage.start_location} → {stage.finish_location}
          <span className="text-zinc-500 text-sm ml-2">{stage.distance_km?.toFixed(0)}km</span>
        </p>
        <div className="flex gap-4 mt-2 text-xs text-zinc-500">
          {stage.profile_score != null && <span>ProfileScore: <b className="text-zinc-300">{stage.profile_score}</b></span>}
          {stage.gradient_final_km != null && <span>Final km gradient: <b className="text-zinc-300">{stage.gradient_final_km}%</b></span>}
          {gs && <span>Bank: <b className="text-green-400">{fmt(gs.bank)} kr</b></span>}
        </div>
      </div>

      {/* Stage profile image */}
      {stage.image_url && (
        <img src={stage.image_url} alt={`Stage ${stage.number} profile`}
          className="w-full rounded-xl border border-zinc-800 max-h-48 object-cover" />
      )}

      {/* DNS alert */}
      {dnsDNFTeamRiders.length > 0 && (
        <div className="bg-red-950 border border-red-700 rounded-xl p-3 flex items-start gap-2">
          <AlertTriangle className="text-red-400 mt-0.5 shrink-0" size={18} />
          <div>
            <p className="font-semibold text-red-300">DNS / DNF Alert</p>
            {dnsDNFTeamRiders.map(r => (
              <p key={r.holdet_id} className="text-red-200 text-sm">
                {r.name} ({r.team_abbr}) — {r.status.toUpperCase()}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Actions panel */}
      <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800 space-y-4">
        <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide">Actions</h2>

        {/* Ingest */}
        <div className="flex flex-wrap items-center gap-3">
          <button onClick={refreshRiders} disabled={ingestLoading}
            className="flex items-center gap-2 px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-zinc-200 rounded-lg text-sm font-medium transition-colors">
            <RefreshCw size={14} className={ingestLoading ? 'animate-spin' : ''} />
            {ingestLoading ? 'Refreshing…' : 'Refresh Riders'}
          </button>
          {ingestMsg && (
            <span className={`text-xs ${ingestMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
              {ingestMsg}
            </span>
          )}
        </div>

        {/* Briefing config */}
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="text-zinc-500 text-xs block mb-1">Look-ahead stages</label>
            <input type="number" min={1} max={21} value={lookAhead}
              onChange={e => setLookAhead(Number(e.target.value))}
              className="w-16 bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-sm" />
          </div>
          <div>
            <label className="text-zinc-500 text-xs block mb-1">Captain override</label>
            <select value={captainOverride} onChange={e => setCaptainOverride(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-sm">
              <option value="">Optimizer picks</option>
              {teamRiders.map(r => (
                <option key={r.holdet_id} value={r.holdet_id}>{r.name}</option>
              ))}
            </select>
          </div>
          <button onClick={runBriefing} disabled={briefLoading}
            className="flex items-center gap-2 px-4 py-1.5 bg-orange-700 hover:bg-orange-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
            <Play size={14} />
            {briefLoading ? 'Running…' : 'Run Briefing'}
          </button>
        </div>

        {briefError && (
          <p className="text-red-400 text-xs">{briefError}</p>
        )}
      </div>

      {/* Briefing result — 4-profile table */}
      {briefResult && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4 space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <h2 className="font-semibold text-white">
                Stage {briefResult.stage_number} Briefing
              </h2>
              <p className="text-zinc-400 text-sm">
                Current team EV: <span className="text-orange-400 font-medium">{fmtK(briefResult.current_team_ev)}</span>
                {' · '}{briefResult.stages_remaining} stages remaining
              </p>
            </div>
            {briefResult.suggested_profile && (
              <span className={`text-sm font-bold px-2 py-0.5 rounded bg-zinc-800 ${PROFILE_COLOURS[briefResult.suggested_profile] ?? 'text-zinc-300'}`}>
                Suggested: {PROFILE_LABELS[briefResult.suggested_profile] ?? briefResult.suggested_profile}
              </span>
            )}
          </div>

          {briefResult.suggested_profile_reason && (
            <p className="text-zinc-500 text-xs italic">{briefResult.suggested_profile_reason}</p>
          )}

          {/* DNS alerts from briefing */}
          {briefResult.dns_alerts.length > 0 && (
            <div className="bg-red-950 border border-red-800 rounded-lg p-2 text-sm text-red-300">
              ⚠ DNS: {briefResult.dns_alerts.map(a => a.name).join(', ')}
            </div>
          )}

          {/* 4-profile table */}
          <button onClick={() => setShowProfiles(p => !p)}
            className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200">
            {showProfiles ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {showProfiles ? 'Hide' : 'Show'} 4-profile comparison
          </button>

          {showProfiles && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-zinc-700 text-zinc-500">
                    <th className="text-left py-1.5 pr-3">Profile</th>
                    <th className="text-right py-1.5 px-2">EV</th>
                    <th className="text-right py-1.5 px-2">Upside (p90)</th>
                    <th className="text-right py-1.5 px-2">Floor (p10)</th>
                    <th className="text-right py-1.5 px-2">Fee</th>
                    <th className="text-left py-1.5 pl-2">Captain</th>
                  </tr>
                </thead>
                <tbody>
                  {['anchor', 'balanced', 'aggressive', 'all_in'].map(pk => {
                    const rec = briefResult.profiles[pk]
                    if (!rec) return null
                    const isSuggested = briefResult.suggested_profile === pk
                    return (
                      <tr key={pk} className={`border-b border-zinc-800/50 ${isSuggested ? 'bg-zinc-800/40' : ''}`}>
                        <td className={`py-2 pr-3 font-bold ${PROFILE_COLOURS[pk] ?? 'text-zinc-300'}`}>
                          {PROFILE_LABELS[pk]}
                          {isSuggested && <span className="ml-1 text-zinc-500">◀</span>}
                        </td>
                        <td className="py-2 px-2 text-right tabular-nums text-zinc-200">{fmtK(rec.expected_value)}</td>
                        <td className="py-2 px-2 text-right tabular-nums text-green-400">{fmtK(rec.upside_90pct)}</td>
                        <td className={`py-2 px-2 text-right tabular-nums ${rec.downside_10pct < 0 ? 'text-red-400' : 'text-green-400'}`}>
                          {fmtK(rec.downside_10pct)}
                        </td>
                        <td className="py-2 px-2 text-right tabular-nums text-zinc-500">
                          {rec.transfer_cost ? fmtK(-rec.transfer_cost) : '—'}
                        </td>
                        <td className="py-2 pl-2 text-zinc-300">{rec.captain_name ?? rec.captain}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Per-profile transfer details */}
          {showProfiles && (
            <div className="space-y-3">
              {['anchor', 'balanced', 'aggressive', 'all_in'].map(pk => {
                const rec = briefResult.profiles[pk]
                if (!rec || rec.transfers.length === 0) return null
                return (
                  <div key={pk} className="text-xs space-y-1">
                    <p className={`font-semibold ${PROFILE_COLOURS[pk]}`}>{PROFILE_LABELS[pk]} transfers</p>
                    {rec.transfers.map((t, i) => (
                      <div key={i} className="flex gap-2 text-zinc-400">
                        <span className={t.action === 'buy' ? 'text-green-400' : 'text-red-400'}>
                          {t.action === 'buy' ? '▲ BUY' : '▼ SELL'}
                        </span>
                        <span className="text-zinc-200">{t.rider_name}</span>
                        <span>{(t.value / 1e6).toFixed(1)}M</span>
                        {t.fee ? <span className="text-zinc-600">fee {fmtK(-t.fee)}</span> : null}
                      </div>
                    ))}
                    <p className="text-zinc-600 italic">{rec.reasoning}</p>
                  </div>
                )
              })}
            </div>
          )}

          {/* Team sim summary */}
          {briefResult.team_sims.length > 0 && (
            <div className="overflow-x-auto">
              <p className="text-xs text-zinc-500 mb-1 uppercase tracking-wide">Team simulation (current squad)</p>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-zinc-700 text-zinc-500">
                    <th className="text-left py-1">Rider</th>
                    <th className="text-right py-1 px-2">EV</th>
                    <th className="text-right py-1 px-2">Floor p10</th>
                    <th className="text-right py-1 px-2">Ceiling p90</th>
                  </tr>
                </thead>
                <tbody>
                  {[...briefResult.team_sims]
                    .sort((a, b) => b.expected_value - a.expected_value)
                    .map(s => (
                      <tr key={s.holdet_id} className="border-b border-zinc-800/40">
                        <td className="py-1 text-zinc-200">
                          {s.is_captain && <span className="text-yellow-400 mr-1">★</span>}
                          {s.name}
                        </td>
                        <td className="py-1 px-2 text-right tabular-nums text-zinc-300">{fmtK(s.expected_value)}</td>
                        <td className={`py-1 px-2 text-right tabular-nums ${s.downside_10pct < 0 ? 'text-red-400' : 'text-green-400'}`}>
                          {fmtK(s.downside_10pct)}
                        </td>
                        <td className="py-1 px-2 text-right tabular-nums text-green-400">{fmtK(s.upside_90pct)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Gather Intelligence */}
      <div className="space-y-3">
        <button onClick={gatherIntelligence} disabled={intelligenceLoading}
          className="flex items-center gap-2 px-4 py-2 bg-purple-800 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg font-medium text-sm transition-colors">
          <Zap size={16} />
          {intelligenceLoading ? 'Gathering intelligence…' : 'Gather Intelligence'}
        </button>

        {intelligenceError && <p className="text-red-400 text-sm">{intelligenceError}</p>}

        {intelligence && (
          <div className="bg-zinc-900 border border-purple-900 rounded-xl p-4 space-y-4">
            <p className="text-zinc-200 text-sm">{intelligence.stage_summary}</p>
            {intelligence.dns_risks.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {intelligence.dns_risks.map(name => (
                  <span key={name} className="px-2 py-1 bg-yellow-900 text-yellow-300 rounded text-xs font-medium">
                    ⚠ {name} — doubtful
                  </span>
                ))}
              </div>
            )}
            {intelligence.stage_notes && (
              <p className="text-zinc-400 text-xs italic">{intelligence.stage_notes}</p>
            )}
            <div className="space-y-2">
              {intelligence.rider_adjustments.map(adj => (
                <div key={adj.name} className="bg-zinc-800 rounded-lg p-3 text-sm">
                  <div className="flex items-start justify-between gap-2 flex-wrap">
                    <div>
                      <span className="font-semibold text-white">{adj.name}</span>
                      <span className="ml-2 text-zinc-400 text-xs">
                        p_win: {fmt(adj.p_win_suggested, true)} · p_top3: {fmt(adj.p_top3_suggested, true)} · p_top15: {fmt(adj.p_top15_suggested, true)}
                      </span>
                      <span className={`ml-2 text-xs font-bold ${adj.confidence === 'high' ? 'text-green-400' : adj.confidence === 'medium' ? 'text-yellow-400' : 'text-zinc-500'}`}>
                        {adj.confidence.toUpperCase()}
                      </span>
                    </div>
                    {accepted.has(adj.name) ? (
                      <span className="text-green-400 text-xs">✓ Accepted</span>
                    ) : (
                      <div className="flex gap-2">
                        <button onClick={() => acceptAdjustment(adj.name)}
                          className="px-2 py-0.5 bg-green-800 hover:bg-green-700 text-green-200 rounded text-xs">Accept</button>
                        <button className="px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded text-xs">Ignore</button>
                      </div>
                    )}
                  </div>
                  <p className="text-zinc-400 text-xs mt-1">{adj.reasoning}</p>
                </div>
              ))}
            </div>
            {intelligence.rider_adjustments.length > 1 && (
              <div className="flex gap-2">
                <button onClick={() => intelligence.rider_adjustments.forEach(a => acceptAdjustment(a.name))}
                  className="px-3 py-1 bg-green-800 hover:bg-green-700 text-green-200 rounded text-xs">Accept All</button>
                <button onClick={() => setIntelligence(null)}
                  className="px-3 py-1 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded text-xs">Ignore All</button>
              </div>
            )}
            {intelligence.sources_used.length > 0 && (
              <p className="text-zinc-600 text-xs">Sources: {intelligence.sources_used.join(', ')}</p>
            )}
          </div>
        )}
      </div>

      {/* Probability table */}
      {displayRiders.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
                <th className="text-left px-3 py-2">Rider</th>
                <th className="text-left px-3 py-2 hidden sm:table-cell">Team</th>
                <th className="text-right px-3 py-2">Value</th>
                <th className="text-right px-3 py-2">Win</th>
                <th className="text-right px-3 py-2">Top3</th>
                <th className="text-right px-3 py-2">Top15</th>
                <th className="text-right px-3 py-2">DNF</th>
                <th className="text-center px-3 py-2">Src</th>
              </tr>
            </thead>
            <tbody>
              {displayRiders.map(r => {
                const p = probs[r.holdet_id]
                const inTeam = gs?.my_team?.includes(r.holdet_id)
                const isCaptain = gs?.captain === r.holdet_id
                return (
                  <tr key={r.holdet_id}
                    className={`border-b border-zinc-800/50 hover:bg-zinc-800/40 ${inTeam ? 'bg-zinc-800/20' : ''}`}>
                    <td className="px-3 py-2">
                      <span className={inTeam ? 'text-white font-medium' : 'text-zinc-400'}>
                        {isCaptain && <span className="text-yellow-400 mr-1">★</span>}
                        {r.name}
                      </span>
                      {r.status !== 'active' && (
                        <span className="ml-1 text-red-400 text-xs uppercase">{r.status}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-zinc-500 text-xs hidden sm:table-cell">{r.team_abbr}</td>
                    <td className="px-3 py-2 text-right text-zinc-400 text-xs">{fmt(r.value)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p?.p_win ?? null, true)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p?.p_top3 ?? null, true)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p?.p_top15 ?? null, true)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-zinc-500">{fmt(p?.p_dnf ?? null, true)}</td>
                    <td className="px-3 py-2 text-center">{p && <SourceBadge source={p.source} />}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {displayRiders.length === 0 && (
        <p className="text-zinc-500 text-sm">
          No probability data. Click <b>Run Briefing</b> above or run <code className="text-orange-400">main.py brief --stage {stage.number}</code> then sync.
        </p>
      )}
    </div>
  )
}
