'use client'
import { useEffect, useState, useCallback } from 'react'
import { createClient, Stage, Rider, ProbSnapshot, GameState, RiderAdjustment } from '@/lib/supabase'
import { AlertTriangle, Zap, ChevronUp, ChevronDown, Minus } from 'lucide-react'

const RACE = 'giro_2026'

function fmt(v: number | null, pct = false) {
  if (v == null) return '—'
  return pct ? `${(v * 100).toFixed(0)}%` : v.toLocaleString('da-DK')
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
          .from('prob_snapshots')
          .select('*')
          .eq('user_id', user.id)
          .eq('race', RACE)
          .eq('stage_number', currentStage)
        const map: Record<string, ProbSnapshot> = {}
        for (const p of (probData ?? []) as ProbSnapshot[]) map[p.rider_id] = p
        setProbs(map)
      }
    }
    load()
  }, [])

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

      // Save to intelligence_log
      const { data: { user } } = await sb.auth.getUser()
      if (user) {
        await sb.from('intelligence_log').insert({
          user_id: user.id,
          race: RACE,
          stage_number: stage.number,
          ...data,
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
    // Update local probs display with intelligence source
    const adj = intelligence?.rider_adjustments.find(a => a.name === name)
    if (!adj) return
    const rider = riders.find(r => r.name?.toLowerCase().includes(name.toLowerCase()))
    if (!rider) return
    setProbs(prev => ({
      ...prev,
      [rider.holdet_id]: {
        ...(prev[rider.holdet_id] ?? { rider_id: rider.holdet_id } as ProbSnapshot),
        p_win:   adj.p_win_suggested,
        p_top3:  adj.p_top3_suggested,
        p_top15: adj.p_top15_suggested,
        p_dnf:   adj.p_dnf_suggested,
        source:  'intelligence',
      },
    }))
  }

  const dnsDNFTeamRiders = riders.filter(
    r => gs?.my_team?.includes(r.holdet_id) && (r.status === 'dns' || r.status === 'dnf')
  )

  const displayRiders = riders
    .filter(r => {
      const inTeam = gs?.my_team?.includes(r.holdet_id)
      const hasProb = !!probs[r.holdet_id]
      return inTeam || hasProb
    })
    .sort((a, b) => {
      const pa = probs[a.holdet_id]?.p_win ?? 0
      const pb = probs[b.holdet_id]?.p_win ?? 0
      return pb - pa
    })

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
          {stage.date && (
            <span className="text-zinc-400 text-sm">{stage.date}</span>
          )}
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
        <img
          src={stage.image_url}
          alt={`Stage ${stage.number} profile`}
          className="w-full rounded-xl border border-zinc-800 max-h-48 object-cover"
        />
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

      {/* Gather Intelligence */}
      <div className="space-y-3">
        <button
          onClick={gatherIntelligence}
          disabled={intelligenceLoading}
          className="flex items-center gap-2 px-4 py-2 bg-purple-800 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg font-medium text-sm transition-colors"
        >
          <Zap size={16} />
          {intelligenceLoading ? 'Gathering intelligence…' : 'Gather Intelligence'}
        </button>

        {intelligenceError && (
          <p className="text-red-400 text-sm">{intelligenceError}</p>
        )}

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
                        p_win: {fmt(adj.p_win_suggested, true)}
                        {' · '}p_top3: {fmt(adj.p_top3_suggested, true)}
                        {' · '}p_top15: {fmt(adj.p_top15_suggested, true)}
                      </span>
                      <span className={`ml-2 text-xs font-bold ${
                        adj.confidence === 'high' ? 'text-green-400' :
                        adj.confidence === 'medium' ? 'text-yellow-400' : 'text-zinc-500'
                      }`}>
                        {adj.confidence.toUpperCase()}
                      </span>
                    </div>
                    {accepted.has(adj.name) ? (
                      <span className="text-green-400 text-xs">✓ Accepted</span>
                    ) : (
                      <div className="flex gap-2">
                        <button onClick={() => acceptAdjustment(adj.name)}
                          className="px-2 py-0.5 bg-green-800 hover:bg-green-700 text-green-200 rounded text-xs">
                          Accept
                        </button>
                        <button className="px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded text-xs">
                          Ignore
                        </button>
                      </div>
                    )}
                  </div>
                  <p className="text-zinc-400 text-xs mt-1">{adj.reasoning}</p>
                </div>
              ))}
            </div>

            {intelligence.rider_adjustments.length > 1 && (
              <div className="flex gap-2">
                <button
                  onClick={() => intelligence.rider_adjustments.forEach(a => acceptAdjustment(a.name))}
                  className="px-3 py-1 bg-green-800 hover:bg-green-700 text-green-200 rounded text-xs">
                  Accept All
                </button>
                <button
                  onClick={() => setIntelligence(null)}
                  className="px-3 py-1 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded text-xs">
                  Ignore All
                </button>
              </div>
            )}

            {intelligence.sources_used.length > 0 && (
              <p className="text-zinc-600 text-xs">
                Sources: {intelligence.sources_used.join(', ')}
              </p>
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
                    className={`border-b border-zinc-800/50 hover:bg-zinc-800/40 ${
                      inTeam ? 'bg-zinc-800/20' : ''
                    }`}>
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
                    <td className="px-3 py-2 text-center">
                      {p && <SourceBadge source={p.source} />}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {displayRiders.length === 0 && (
        <p className="text-zinc-500 text-sm">
          No probability data. Run <code className="text-orange-400">main.py brief --stage {stage.number}</code> then sync.
        </p>
      )}
    </div>
  )
}
