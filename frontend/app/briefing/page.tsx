'use client'
import { useEffect, useState, useCallback, useRef } from 'react'
import { createClient, Stage, Rider, ProbSnapshot, GameState, RiderAdjustment } from '@/lib/supabase'
import { AlertTriangle, Zap, RefreshCw, Play, ChevronDown, ChevronUp } from 'lucide-react'

const RACE = 'giro_2026'
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const RIDERS_CACHE_KEY = 'holdet_riders_cache'

// C1: Default scenario priors by stage type — shown before first run
const STAGE_TYPE_DEFAULTS: Record<string, Record<string, number>> = {
  flat:     { bunch_sprint: 65, reduced_sprint: 20, breakaway: 15 },
  hilly:    { bunch_sprint: 25, reduced_sprint: 25, breakaway: 30, gc_day: 20 },
  mountain: { gc_day: 70, breakaway: 25, reduced_sprint: 5 },
  itt:      { tt: 100 },
  ttt:      { ttt: 100 },
}

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

const ROLE_COLOURS: Record<string, string> = {
  GC:         'bg-indigo-900 text-indigo-300',
  Sprinter:   'bg-green-900 text-green-300',
  Climber:    'bg-red-900 text-red-300',
  Breakaway:  'bg-yellow-900 text-yellow-300',
  TT:         'bg-purple-900 text-purple-300',
  Domestique: 'bg-zinc-700 text-zinc-400',
}

function RoleBadge({ role }: { role: string }) {
  return (
    <span className={`px-1 py-0.5 rounded text-xs font-medium ${ROLE_COLOURS[role] ?? 'bg-zinc-700 text-zinc-400'}`}>
      {role}
    </span>
  )
}

function DistributionBar({ p10, p50, ev, p80, p95 }: { p10: number; p50: number; ev: number; p80: number; p95: number }) {
  const min = Math.min(p10, 0)
  const max = Math.max(p95, 1)
  const range = max - min || 1
  const pct = (v: number) => `${((v - min) / range) * 100}%`
  const skewed = ev > p50
  return (
    <div className="relative h-2 w-32 bg-zinc-800 rounded overflow-hidden" title={`p10:${fmtK(p10)} p50:${fmtK(p50)} EV:${fmtK(ev)} p80:${fmtK(p80)} p95:${fmtK(p95)}`}>
      {/* p10–p95 band */}
      <div
        className={`absolute h-full ${skewed ? 'bg-green-900/60' : 'bg-blue-900/60'}`}
        style={{ left: pct(p10), width: `${((p95 - p10) / range) * 100}%` }}
      />
      {/* p10 marker */}
      <div className="absolute h-full w-px bg-red-500/70" style={{ left: pct(p10) }} />
      {/* p50 marker */}
      <div className="absolute h-full w-px bg-zinc-400/70" style={{ left: pct(p50) }} />
      {/* EV marker */}
      <div className="absolute h-full w-0.5 bg-orange-400" style={{ left: pct(ev) }} />
      {/* p95 marker */}
      <div className="absolute h-full w-px bg-green-500/70" style={{ left: pct(p95) }} />
    </div>
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
  team_ev: number | null
  team_p10: number | null
  team_p80: number | null
  team_p95: number | null
  etapebonus_ev: number | null
  etapebonus_p95: number | null
}

type TeamSim = {
  holdet_id: string
  name: string
  team_abbr: string
  expected_value: number
  downside_10pct: number
  upside_90pct: number
  is_captain: boolean
  roles: string[]
  percentile_10: number
  percentile_50: number
  percentile_80: number
  percentile_90: number
  percentile_95: number
  p_positive: number
}

// ── Decision Trace types (Session 22.5 contract) ────────────────────────────

type RiderTrace = {
  base_ev: number
  probability_adjustment: number
  variance_adjustment: number
  intent_adjustment: number   // always 0.0 in 22.5 — column omitted in UI
  lookahead_adjustment: number
  final_ev: number
}

type CaptainCandidate = {
  rider_id: string
  ev: number
  p_win: number
  score: number
}

type CaptainTrace = {
  mode: string
  lambda: number
  ev_component: number
  p_win_component: number
  final_score: number
}

type FlipThreshold = {
  score_gap: number
  interpretation: string
  a: string
  b: string
}

type Contributor = {
  label: string
  share: number
}

type DecisionTrace = {
  riders: Record<string, RiderTrace>
  captain_trace: CaptainTrace
  flip_threshold?: FlipThreshold
  contributors: {
    rider_contributors: Contributor[]
    scenario_contributions?: Contributor[]
  }
  trace_version: string
}

// ── BriefResult ───────────────────────────────────────────────────────────────

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
  team_sims: TeamSim[]
  dns_alerts: { name: string; status: string }[]
  scenario_priors: Record<string, number> | null
  scenario_stats: Record<string, number> | null
  team_note: string | null
  decision_trace?: DecisionTrace
  captain_candidates?: CaptainCandidate[]
  captain_recommendation?: { rider_id: string; mode: string }
}

function parseJsonField(val: unknown): string[] {
  if (Array.isArray(val)) return val as string[]
  if (typeof val === 'string') { try { return JSON.parse(val) } catch { return [] } }
  return []
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

// ── Decision Trace helpers ────────────────────────────────────────────────────

function deltaColor(v: number | null | undefined): string {
  if (v == null || v === 0) return 'text-zinc-500'
  return v > 0 ? 'text-green-400' : 'text-red-400'
}

function fmtDelta(v: number | null | undefined): string {
  if (v == null) return '—'
  return v === 0 ? '0k' : fmtK(v)
}

// ── DecisionTraceInspector component ─────────────────────────────────────────

function DecisionTraceInspector({
  trace,
  riderNameMap,
  candidates,
  captainRiderId,
  baselineTrace,
  baselineCandidates,
  baselineCaptainRiderId,
}: {
  trace: DecisionTrace
  riderNameMap: Record<string, string>
  candidates: CaptainCandidate[]
  captainRiderId?: string
  baselineTrace?: DecisionTrace
  baselineCandidates?: CaptainCandidate[]
  baselineCaptainRiderId?: string
}) {
  // Trace validity gate
  if (trace.trace_version !== '22.5') return null

  const comparisonActive = baselineTrace != null && baselineTrace.trace_version === '22.5'

  const [open, setOpen] = useState(false)
  const [openSection, setOpenSection] = useState<Record<string, boolean>>({
    riders: true, captain: true, flip: true, contributors: true,
    cmp_riders: true, cmp_captain: true, cmp_candidates: true, cmp_flip: true, cmp_contributors: true,
  })
  const toggleSection = (key: string) =>
    setOpenSection(prev => ({ ...prev, [key]: !prev[key] }))

  function cmpDelta(curr: number | null | undefined, base: number | null | undefined): number | null {
    if (curr == null || base == null) return null
    return curr - base
  }

  // C.1 — sort riders by final_ev desc, rider_id asc as tie-breaker
  const sortedRiders = Object.entries(trace.riders).sort(([idA, a], [idB, b]) => {
    if (b.final_ev !== a.final_ev) return b.final_ev - a.final_ev
    return idA < idB ? -1 : 1
  })

  const name = (id: string) => riderNameMap[id] ?? id

  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800">
      {/* Panel toggle */}
      <button
        onClick={() => setOpen(p => !p)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-zinc-300 hover:text-white"
      >
        <span>Decision Trace Inspector</span>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {open && (
        <div className="border-t border-zinc-800 divide-y divide-zinc-800/50">

          {/* ── C.1 Riders ──────────────────────────────────────────────── */}
          <div className="p-4 space-y-2">
            <button
              onClick={() => toggleSection('riders')}
              className="flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-zinc-200 uppercase tracking-wide"
            >
              {openSection.riders ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              Riders (by final EV)
            </button>
            {openSection.riders && (
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-xs font-mono">
                  <thead className="sticky top-0 bg-zinc-900">
                    <tr className="border-b border-zinc-700 text-zinc-500">
                      <th className="text-left py-1.5 pr-3">Rider</th>
                      <th className="text-right py-1.5 px-2">base_ev</th>
                      <th className="text-right py-1.5 px-2">prob_adj</th>
                      <th className="text-right py-1.5 px-2">var_adj</th>
                      <th className="text-right py-1.5 px-2">la_adj</th>
                      <th className="text-right py-1.5 pl-2">final_ev</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRiders.map(([rid, rt]) => (
                      <tr key={rid} className="border-b border-zinc-800/40 hover:bg-zinc-800/20">
                        <td className="py-1 pr-3 text-zinc-300 font-sans">{name(rid)}</td>
                        <td className="py-1 px-2 text-right text-zinc-400">{fmtDelta(rt.base_ev)}</td>
                        <td className={`py-1 px-2 text-right ${deltaColor(rt.probability_adjustment)}`}>
                          {fmtDelta(rt.probability_adjustment)}
                        </td>
                        <td className={`py-1 px-2 text-right ${deltaColor(rt.variance_adjustment)}`}>
                          {fmtDelta(rt.variance_adjustment)}
                        </td>
                        <td className={`py-1 px-2 text-right ${deltaColor(rt.lookahead_adjustment)}`}>
                          {fmtDelta(rt.lookahead_adjustment)}
                        </td>
                        <td className="py-1 pl-2 text-right text-orange-400 font-semibold">
                          {fmtDelta(rt.final_ev)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ── C.2 Captain decision ─────────────────────────────────────── */}
          <div className="p-4 space-y-2">
            <button
              onClick={() => toggleSection('captain')}
              className="flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-zinc-200 uppercase tracking-wide"
            >
              {openSection.captain ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              Captain Decision
            </button>
            {openSection.captain && trace.captain_trace && (
              <div className="space-y-3">
                <div className="text-xs text-zinc-400 font-mono space-y-0.5">
                  <p>
                    <span className="text-zinc-500">Mode:</span>{' '}
                    <span className="text-zinc-200">{trace.captain_trace.mode}</span>
                    <span className="mx-3 text-zinc-600">|</span>
                    <span className="text-zinc-500">λ:</span>{' '}
                    <span className="text-zinc-200">{trace.captain_trace.lambda}</span>
                  </p>
                  <p>
                    <span className="text-zinc-500 inline-block w-28">EV component:</span>
                    <span className="text-zinc-200">{trace.captain_trace.ev_component.toLocaleString('da-DK')}</span>
                  </p>
                  <p>
                    <span className="text-zinc-500 inline-block w-28">p_win component:</span>
                    <span className="text-zinc-200">{trace.captain_trace.p_win_component.toFixed(4)}</span>
                  </p>
                  <p className="border-t border-zinc-800 pt-1">
                    <span className="text-zinc-500 inline-block w-28">Final score:</span>
                    <span className="text-orange-400 font-semibold">{trace.captain_trace.final_score.toLocaleString('da-DK')}</span>
                  </p>
                </div>

                {/* Candidates — backend order, no re-ranking */}
                {candidates.length > 0 && (
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">── Candidates ──</p>
                    <table className="text-xs w-full max-w-xs font-mono">
                      <thead>
                        <tr className="text-zinc-600 border-b border-zinc-800">
                          <th className="text-left py-1">Rider</th>
                          <th className="text-right py-1 px-2">EV</th>
                          <th className="text-right py-1 px-2">p_win</th>
                          <th className="text-right py-1">Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {candidates.map((c, i) => (
                          <tr key={c.rider_id} className={`border-b border-zinc-800/40 ${i === 0 ? 'text-orange-400' : 'text-zinc-300'}`}>
                            <td className="py-1 font-sans">{name(c.rider_id)}</td>
                            <td className="py-1 px-2 text-right tabular-nums">{fmtK(c.ev)}</td>
                            <td className="py-1 px-2 text-right tabular-nums">{(c.p_win * 100).toFixed(1)}%</td>
                            <td className="py-1 text-right tabular-nums">{c.score.toLocaleString('da-DK', { maximumFractionDigits: 1 })}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── C.3 Flip sensitivity ─────────────────────────────────────── */}
          <div className="p-4 space-y-2">
            <button
              onClick={() => toggleSection('flip')}
              className="flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-zinc-200 uppercase tracking-wide"
            >
              {openSection.flip ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              Flip Sensitivity
            </button>
            {openSection.flip && (
              trace.flip_threshold ? (
                <div className="space-y-2 text-xs font-mono">
                  <p className="text-zinc-300">
                    <span className="text-orange-400">{name(trace.flip_threshold.a)}</span>
                    <span className="text-zinc-500 mx-2">vs</span>
                    <span className="text-zinc-300">{name(trace.flip_threshold.b)}</span>
                  </p>
                  <p>
                    <span className="text-zinc-500">score_gap: </span>
                    <span className={deltaColor(trace.flip_threshold.score_gap)}>
                      {trace.flip_threshold.score_gap > 0 ? '+' : ''}
                      {trace.flip_threshold.score_gap.toLocaleString('da-DK', { maximumFractionDigits: 2 })}
                    </span>
                  </p>
                  {/* Visual bar */}
                  <div className="relative h-2 w-full bg-zinc-800 rounded overflow-hidden">
                    {(() => {
                      const gap = trace.flip_threshold.score_gap
                      const absGap = Math.abs(gap)
                      const pct = Math.min(absGap / (absGap + 1) * 50 + 50, 95)
                      if (gap > 0) {
                        return <div className="absolute h-full bg-green-700/70" style={{ left: '50%', width: `${pct - 50}%` }} />
                      }
                      return <div className="absolute h-full bg-red-700/70" style={{ right: '50%', width: `${pct - 50}%` }} />
                    })()}
                    <div className="absolute h-full w-px bg-zinc-400/50" style={{ left: '50%' }} />
                  </div>
                  <p className="text-zinc-600 italic">{trace.flip_threshold.interpretation}</p>
                </div>
              ) : (
                <p className="text-xs text-zinc-500 italic">Only one candidate — no flip analysis</p>
              )
            )}
          </div>

          {/* ── C.4 Contribution breakdown ───────────────────────────────── */}
          <div className="p-4 space-y-2">
            <button
              onClick={() => toggleSection('contributors')}
              className="flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-zinc-200 uppercase tracking-wide"
            >
              {openSection.contributors ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              Contribution Breakdown
            </button>
            {openSection.contributors && (
              <div className="space-y-3">
                {/* Rider contributors */}
                {trace.contributors?.rider_contributors?.length > 0 && (
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">Rider contributors</p>
                    <table className="text-xs w-full max-w-xs">
                      <thead>
                        <tr className="text-zinc-600 border-b border-zinc-800">
                          <th className="text-left py-1">Rider</th>
                          <th className="text-right py-1">Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trace.contributors.rider_contributors.map(c => (
                          <tr key={c.label} className="border-b border-zinc-800/40">
                            <td className="py-1 text-zinc-300">{c.label}</td>
                            <td className="py-1 text-right text-orange-400 tabular-nums">
                              {Math.round(c.share * 100)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {/* Scenario contributions — only when present */}
                {trace.contributors?.scenario_contributions && trace.contributors.scenario_contributions.length > 0 && (
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">Scenario contributions</p>
                    <table className="text-xs w-full max-w-xs">
                      <thead>
                        <tr className="text-zinc-600 border-b border-zinc-800">
                          <th className="text-left py-1">Scenario</th>
                          <th className="text-right py-1">Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trace.contributors.scenario_contributions.map(c => (
                          <tr key={c.label} className="border-b border-zinc-800/40">
                            <td className="py-1 text-zinc-300">{c.label.replace('_', ' ')}</td>
                            <td className="py-1 text-right text-blue-400 tabular-nums">
                              {Math.round(c.share * 100)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── B.1–B.5 Comparison sections (22.7) ──────────────────────── */}
          {comparisonActive && baselineTrace && (
            <>
              {/* B.1 Rider EV delta */}
              <div className="p-4 space-y-2">
                <button
                  onClick={() => toggleSection('cmp_riders')}
                  className="flex items-center gap-1 text-xs font-semibold text-blue-400 hover:text-blue-200 uppercase tracking-wide"
                >
                  {openSection.cmp_riders ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                  Rider EV Δ (current vs baseline)
                </button>
                {openSection.cmp_riders && (
                  <div className="overflow-x-auto max-h-96 overflow-y-auto">
                    <table className="w-full text-xs font-mono">
                      <thead className="sticky top-0 bg-zinc-900">
                        <tr className="border-b border-zinc-700 text-zinc-500">
                          <th className="text-left py-1.5 pr-3">Rider</th>
                          <th className="text-right py-1.5 px-2">current</th>
                          <th className="text-right py-1.5 px-2">baseline</th>
                          <th className="text-right py-1.5 pl-2">Δ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedRiders
                          .filter(([rid]) => rid in baselineTrace.riders)
                          .map(([rid, rt]) => {
                            const bt = baselineTrace.riders[rid]
                            const delta = cmpDelta(rt.final_ev, bt?.final_ev)
                            return (
                              <tr key={rid} className="border-b border-zinc-800/40 hover:bg-zinc-800/20">
                                <td className="py-1 pr-3 text-zinc-300 font-sans">{name(rid)}</td>
                                <td className="py-1 px-2 text-right text-orange-400">{fmtK(rt.final_ev)}</td>
                                <td className="py-1 px-2 text-right text-zinc-400">{bt?.final_ev != null ? fmtK(bt.final_ev) : '—'}</td>
                                <td className={`py-1 pl-2 text-right font-semibold ${deltaColor(delta)}`}>
                                  {fmtK(delta)}
                                </td>
                              </tr>
                            )
                          })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* B.2 Captain comparison */}
              {trace.captain_trace && baselineTrace.captain_trace && (
                <div className="p-4 space-y-2">
                  <button
                    onClick={() => toggleSection('cmp_captain')}
                    className="flex items-center gap-1 text-xs font-semibold text-blue-400 hover:text-blue-200 uppercase tracking-wide"
                  >
                    {openSection.cmp_captain ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    Captain Δ (current vs baseline)
                  </button>
                  {openSection.cmp_captain && (
                    <div className="space-y-2 text-xs font-mono">
                      {captainRiderId !== baselineCaptainRiderId ? (
                        <p className="text-yellow-400">
                          ⚠ Captain changed: {name(baselineCaptainRiderId ?? '?')} → {name(captainRiderId ?? '?')}
                        </p>
                      ) : captainRiderId ? (
                        <p className="text-zinc-400">Captain: {name(captainRiderId)}</p>
                      ) : null}
                      <table className="text-xs w-full max-w-xs">
                        <thead>
                          <tr className="text-zinc-600 border-b border-zinc-800">
                            <th className="text-left py-1">metric</th>
                            <th className="text-right py-1 px-2">current</th>
                            <th className="text-right py-1 px-2">baseline</th>
                            <th className="text-right py-1">Δ</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr className="border-b border-zinc-800/40">
                            <td className="py-1 text-zinc-400">final_score</td>
                            <td className="py-1 px-2 text-right text-orange-400">
                              {fmtK(trace.captain_trace.final_score)}
                            </td>
                            <td className="py-1 px-2 text-right text-zinc-400">
                              {fmtK(baselineTrace.captain_trace.final_score)}
                            </td>
                            {(() => {
                              const d = cmpDelta(trace.captain_trace.final_score, baselineTrace.captain_trace.final_score)
                              return (
                                <td className={`py-1 text-right font-semibold ${deltaColor(d)}`}>
                                  {fmtK(d)}
                                </td>
                              )
                            })()}
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* B.3 Candidate comparison */}
              {candidates.length > 0 && baselineCandidates && baselineCandidates.length > 0 && (
                <div className="p-4 space-y-2">
                  <button
                    onClick={() => toggleSection('cmp_candidates')}
                    className="flex items-center gap-1 text-xs font-semibold text-blue-400 hover:text-blue-200 uppercase tracking-wide"
                  >
                    {openSection.cmp_candidates ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    Candidate Score Δ (current vs baseline)
                  </button>
                  {openSection.cmp_candidates && (
                    <div className="overflow-x-auto">
                      <table className="text-xs w-full max-w-sm font-mono">
                        <thead>
                          <tr className="text-zinc-600 border-b border-zinc-800">
                            <th className="text-left py-1">Rider</th>
                            <th className="text-right py-1 px-2">current</th>
                            <th className="text-right py-1 px-2">baseline</th>
                            <th className="text-right py-1">Δ</th>
                          </tr>
                        </thead>
                        <tbody>
                          {candidates
                            .filter(c => baselineCandidates!.some(bc => bc.rider_id === c.rider_id))
                            .map(c => {
                              const bc = baselineCandidates!.find(bc => bc.rider_id === c.rider_id)!
                              const delta = cmpDelta(c.score, bc.score)
                              return (
                                <tr key={c.rider_id} className="border-b border-zinc-800/40">
                                  <td className="py-1 font-sans text-zinc-300">{name(c.rider_id)}</td>
                                  <td className="py-1 px-2 text-right text-orange-400 tabular-nums">
                                    {c.score.toLocaleString('da-DK', { maximumFractionDigits: 1 })}
                                  </td>
                                  <td className="py-1 px-2 text-right text-zinc-400 tabular-nums">
                                    {bc.score.toLocaleString('da-DK', { maximumFractionDigits: 1 })}
                                  </td>
                                  <td className={`py-1 text-right font-semibold ${deltaColor(delta)}`}>
                                    {fmtK(delta)}
                                  </td>
                                </tr>
                              )
                            })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* B.4 Flip threshold comparison */}
              {trace.flip_threshold && baselineTrace.flip_threshold && (
                <div className="p-4 space-y-2">
                  <button
                    onClick={() => toggleSection('cmp_flip')}
                    className="flex items-center gap-1 text-xs font-semibold text-blue-400 hover:text-blue-200 uppercase tracking-wide"
                  >
                    {openSection.cmp_flip ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    Flip Threshold Δ (current vs baseline)
                  </button>
                  {openSection.cmp_flip && (
                    <div className="space-y-2 text-xs font-mono">
                      <p className="text-zinc-400">
                        A: {name(trace.flip_threshold.a)}
                        <span className="text-zinc-600 mx-2">·</span>
                        B: {name(trace.flip_threshold.b)}
                      </p>
                      <table className="text-xs w-full max-w-xs">
                        <thead>
                          <tr className="text-zinc-600 border-b border-zinc-800">
                            <th className="text-left py-1">metric</th>
                            <th className="text-right py-1 px-2">current</th>
                            <th className="text-right py-1 px-2">baseline</th>
                            <th className="text-right py-1">Δ</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr className="border-b border-zinc-800/40">
                            <td className="py-1 text-zinc-400">score_gap</td>
                            <td className={`py-1 px-2 text-right ${deltaColor(trace.flip_threshold.score_gap)}`}>
                              {trace.flip_threshold.score_gap > 0 ? '+' : ''}{trace.flip_threshold.score_gap.toLocaleString('da-DK', { maximumFractionDigits: 2 })}
                            </td>
                            <td className={`py-1 px-2 text-right ${deltaColor(baselineTrace.flip_threshold.score_gap)}`}>
                              {baselineTrace.flip_threshold.score_gap > 0 ? '+' : ''}{baselineTrace.flip_threshold.score_gap.toLocaleString('da-DK', { maximumFractionDigits: 2 })}
                            </td>
                            {(() => {
                              const d = cmpDelta(trace.flip_threshold.score_gap, baselineTrace.flip_threshold.score_gap)
                              return (
                                <td className={`py-1 text-right font-semibold ${deltaColor(d)}`}>
                                  {fmtK(d)}
                                </td>
                              )
                            })()}
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* B.5 Contributors comparison (rider only, max 3) */}
              {trace.contributors?.rider_contributors?.length > 0 &&
               baselineTrace.contributors?.rider_contributors?.length > 0 && (
                <div className="p-4 space-y-2">
                  <button
                    onClick={() => toggleSection('cmp_contributors')}
                    className="flex items-center gap-1 text-xs font-semibold text-blue-400 hover:text-blue-200 uppercase tracking-wide"
                  >
                    {openSection.cmp_contributors ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    Contributor Share Δ (current vs baseline)
                  </button>
                  {openSection.cmp_contributors && (
                    <div className="overflow-x-auto">
                      <table className="text-xs w-full max-w-xs">
                        <thead>
                          <tr className="text-zinc-600 border-b border-zinc-800">
                            <th className="text-left py-1">Rider</th>
                            <th className="text-right py-1 px-2">current</th>
                            <th className="text-right py-1 px-2">baseline</th>
                            <th className="text-right py-1">Δ</th>
                          </tr>
                        </thead>
                        <tbody>
                          {trace.contributors.rider_contributors
                            .slice(0, 3)
                            .filter(c => baselineTrace.contributors?.rider_contributors?.some(bc => bc.label === c.label))
                            .map(c => {
                              const bc = baselineTrace.contributors?.rider_contributors?.find(bc => bc.label === c.label)
                              const delta = cmpDelta(c.share, bc?.share)
                              return (
                                <tr key={c.label} className="border-b border-zinc-800/40">
                                  <td className="py-1 text-zinc-300">{c.label}</td>
                                  <td className="py-1 px-2 text-right text-orange-400 tabular-nums">
                                    {Math.round(c.share * 100)}%
                                  </td>
                                  <td className="py-1 px-2 text-right text-zinc-400 tabular-nums">
                                    {bc ? `${Math.round(bc.share * 100)}%` : '—'}
                                  </td>
                                  <td className={`py-1 text-right font-semibold ${deltaColor(delta)}`}>
                                    {delta == null ? '—' : `${delta > 0 ? '+' : ''}${Math.round(delta * 100)}pp`}
                                  </td>
                                </tr>
                              )
                            })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

        </div>
      )}
    </div>
  )
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
  const [baselineBrief, setBaselineBrief] = useState<BriefResult | null>(null)
  const [briefError, setBriefError] = useState<string | null>(null)
  const [ingestMsg, setIngestMsg] = useState<string | null>(null)
  const [showProfiles, setShowProfiles] = useState(false)
  const [user, setUser] = useState<any>(null)
  const [scenarioPriors, setScenarioPriors] = useState<Record<string, number> | null>(null)
  const [reSimulating, setReSimulating] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // C4: Restore briefing result AND slider state on mount / tab-switch
  useEffect(() => {
    const stored = localStorage.getItem('holdet_briefing_result')
    if (stored) {
      try {
        const parsed = JSON.parse(stored)
        setBriefResult(parsed)
        setShowProfiles(true)
        // Restore slider state from cached result
        if (parsed.scenario_priors) {
          const pct = Object.fromEntries(
            Object.entries(parsed.scenario_priors as Record<string, number>)
              .map(([k, v]) => [k, Math.round((v as number) * 100)])
          )
          setScenarioPriors(pct)
        }
      } catch {
        // ignore parse errors
      }
    }
  }, [])

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      setUser(user)
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

      // C3: Cache riders; fall back to cache if Supabase returns empty
      const riderList = (ridersRes.data as Rider[]) ?? []
      if (riderList.length > 0) {
        localStorage.setItem(RIDERS_CACHE_KEY, JSON.stringify(riderList))
        setRiders(riderList)
      } else {
        const cached = localStorage.getItem(RIDERS_CACHE_KEY)
        if (cached) { try { setRiders(JSON.parse(cached)) } catch { /* ignore */ } }
      }

      // C1: Initialize sliders from stage type defaults before first run
      if (stageData?.stage_type) {
        setScenarioPriors(prev => {
          if (prev) return prev  // already set (e.g. from localStorage restore)
          return STAGE_TYPE_DEFAULTS[stageData.stage_type] ?? null
        })
      }

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

  function normalizeScenario(
    key: string,
    newValue: number,
    current: Record<string, number>
  ): Record<string, number> {
    const others = Object.keys(current).filter(k => k !== key)
    const remaining = 100 - newValue
    const sumOthers = others.reduce((s, k) => s + current[k], 0)
    const updated: Record<string, number> = { ...current, [key]: newValue }
    for (const k of others) {
      updated[k] = sumOthers > 0
        ? Math.round((current[k] / sumOthers) * remaining)
        : Math.round(remaining / others.length)
    }
    return updated
  }

  const runBriefing = useCallback(async (priorsOverride?: Record<string, number> | null) => {
    if (!stage) return
    // C2: slider re-sims keep old table visible; full runs clear it
    const isSliderRun = priorsOverride != null
    if (isSliderRun) {
      setReSimulating(true)
    } else {
      setBriefResult(null)
      setBriefError(null)
      setShowProfiles(false)
    }
    setBriefLoading(true)
    try {
      const priors = priorsOverride ?? scenarioPriors
      // Convert percentages to fractions for the API, omit if null
      const scenarioPriorsPayload = priors
        ? Object.fromEntries(Object.entries(priors).map(([k, v]) => [k, v / 100]))
        : null
      const res = await fetch(`${API}/brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stage: stage.number,
          look_ahead: lookAhead,
          captain_override: captainOverride || null,
          scenario_priors: scenarioPriorsPayload,
        }),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Brief failed')
      setBriefResult(d)
      // C1: Always update sliders from resolved priors returned by API
      if (d.scenario_priors) {
        const pct = Object.fromEntries(
          Object.entries(d.scenario_priors as Record<string, number>)
            .map(([k, v]) => [k, Math.round((v as number) * 100)])
        )
        setScenarioPriors(pct)
      }
      localStorage.setItem('holdet_briefing_result', JSON.stringify(d))
      setShowProfiles(true)
    } catch (e: unknown) {
      setBriefError(e instanceof Error ? e.message : 'Server not running? Start with: bash scripts/start_api.sh')
    } finally {
      setReSimulating(false)
      setBriefLoading(false)
    }
  }, [stage, lookAhead, captainOverride, scenarioPriors])

  const gatherIntelligence = useCallback(async () => {
    if (!stage || !gs) return
    setIntelligenceLoading(true)
    setIntelligenceError(null)
    setIntelligence(null)
    try {
      const myTeamNames = riders
        .filter(r => myTeamIds.includes(r.holdet_id))
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

  const myTeamIds = parseJsonField(gs?.my_team)

  const dnsDNFTeamRiders = riders.filter(
    r => myTeamIds.includes(r.holdet_id) && (r.status === 'dns' || r.status === 'dnf')
  )

  const teamRiders = riders.filter(r => myTeamIds.includes(r.holdet_id))

  const displayRiders = riders
    .filter(r => myTeamIds.includes(r.holdet_id) || !!probs[r.holdet_id])
    .sort((a, b) => (probs[b.holdet_id]?.p_win ?? 0) - (probs[a.holdet_id]?.p_win ?? 0))

  if (!user) return (
    <div className="text-center mt-24 space-y-4">
      <p className="text-zinc-400">You need to be logged in to use the briefing.</p>
      <a href="/auth" className="px-4 py-2 bg-orange-700 hover:bg-orange-600 text-white rounded-lg text-sm font-medium">Sign in</a>
    </div>
  )

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
          className="w-full h-auto rounded-lg border border-zinc-800" />
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
          <button onClick={() => runBriefing()} disabled={briefLoading}
            className="flex items-center gap-2 px-4 py-1.5 bg-orange-700 hover:bg-orange-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
            <Play size={14} />
            {briefLoading ? 'Running…' : 'Run Briefing'}
          </button>
        </div>

        {/* Scenario sliders — shown once briefResult has initialized scenario_priors */}
        {scenarioPriors && Object.keys(scenarioPriors).length > 0 && (
          <div>
            <p className="text-zinc-500 text-xs mb-2">Scenario priors (adjust to recompute)</p>
            <div className="flex flex-wrap gap-4">
              {Object.entries(scenarioPriors).map(([key, val]) => (
                <div key={key} className="flex flex-col gap-1 min-w-[120px]">
                  <div className="flex justify-between text-xs text-zinc-400">
                    <label>{key.replace('_', ' ')}</label>
                    <span className="tabular-nums">{val}%</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={val}
                    onChange={e => {
                      const newVal = Number(e.target.value)
                      const updated = normalizeScenario(key, newVal, scenarioPriors)
                      setScenarioPriors(updated)
                      if (debounceRef.current) clearTimeout(debounceRef.current)
                      debounceRef.current = setTimeout(() => {
                        runBriefing(updated)
                      }, 500)
                    }}
                    className="w-full accent-orange-500"
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {briefError && (
          <p className="text-red-400 text-xs">{briefError}</p>
        )}
      </div>

      {/* Briefing result — 4-profile table */}
      {briefResult && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 p-4 space-y-4">
          {reSimulating && (
            <p className="text-xs text-zinc-500 italic animate-pulse">Re-computing…</p>
          )}
          <div className={reSimulating ? 'opacity-50' : ''}>
          {/* team_note banner — shown when no team is selected */}
          {briefResult.team_note && (
            <div className="bg-yellow-950 border border-yellow-700 rounded-lg p-3 flex items-start gap-2">
              <AlertTriangle className="text-yellow-400 mt-0.5 shrink-0" size={16} />
              <p className="text-yellow-300 text-sm">{briefResult.team_note}</p>
            </div>
          )}

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

          {/* Scenario priors + realized stats */}
          {briefResult.scenario_priors && Object.keys(briefResult.scenario_priors).length > 0 && (
            <div className="text-xs text-zinc-500 space-y-0.5">
              <p>
                <span className="text-zinc-400">Priors:</span>{' '}
                {Object.entries(briefResult.scenario_priors)
                  .map(([s, p]) => `${s.replace('_', ' ')} ${Math.round((p as number) * 100)}%`)
                  .join(' · ')}
              </p>
              {briefResult.scenario_stats && Object.keys(briefResult.scenario_stats).length > 0 && (
                <p>
                  <span className="text-zinc-400">Realized:</span>{' '}
                  {Object.entries(briefResult.scenario_stats)
                    .map(([s, p]) => `${s.replace('_', ' ')} ${Math.round((p as number) * 100)}%`)
                    .join(' · ')}
                </p>
              )}
            </div>
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
                    <th className="text-right py-1.5 px-2">Team EV</th>
                    <th className="text-right py-1.5 px-2">Eta EV</th>
                    <th className="text-right py-1.5 px-2">Team p10</th>
                    <th className="text-right py-1.5 px-2">Team p80</th>
                    <th className="text-right py-1.5 px-2">Team p95</th>
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
                        <td className="py-2 px-2 text-right tabular-nums text-orange-400">{fmtK(rec.team_ev)}</td>
                        <td className="py-2 px-2 text-right tabular-nums text-yellow-600">{fmtK(rec.etapebonus_ev)}</td>
                        <td className={`py-2 px-2 text-right tabular-nums ${(rec.team_p10 ?? 0) < 0 ? 'text-red-400' : 'text-green-400'}`}>
                          {fmtK(rec.team_p10)}
                        </td>
                        <td className="py-2 px-2 text-right tabular-nums text-green-400">{fmtK(rec.team_p80)}</td>
                        <td className="py-2 px-2 text-right tabular-nums text-green-300">{fmtK(rec.team_p95)}</td>
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
                    {rec.transfers.map((t, i) => {
                      const transferRider = riders.find(r => r.holdet_id === t.rider_id)
                      return (
                      <div key={i} className="flex gap-2 text-zinc-400">
                        <span className={t.action === 'buy' ? 'text-green-400' : 'text-red-400'}>
                          {t.action === 'buy' ? '▲ BUY' : '▼ SELL'}
                        </span>
                        <span className="text-zinc-200">
                          {t.rider_name}
                          {transferRider?.team_abbr && (
                            <span className="text-zinc-500 ml-1">{transferRider.team_abbr}</span>
                          )}
                        </span>
                        <span>{(t.value / 1e6).toFixed(1)}M</span>
                        {t.fee ? <span className="text-zinc-600">fee {fmtK(-t.fee)}</span> : null}
                      </div>
                      )
                    })}
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
                    <th className="text-left py-1 px-2">Team</th>
                    <th className="text-left py-1 px-2">Roles</th>
                    <th className="text-right py-1 px-2">EV</th>
                    <th className="text-left py-1 px-2">Distribution</th>
                    <th className="text-right py-1 px-2">p+</th>
                  </tr>
                </thead>
                <tbody>
                  {[...briefResult.team_sims]
                    .sort((a, b) => b.expected_value - a.expected_value)
                    .map(s => (
                      <tr key={s.holdet_id} className="border-b border-zinc-800/40">
                        <td className="py-1.5 text-zinc-200">
                          {s.is_captain && <span className="text-yellow-400 mr-1">★</span>}
                          {s.name}
                        </td>
                        <td className="py-1.5 px-2 text-zinc-500">{s.team_abbr}</td>
                        <td className="py-1.5 px-2">
                          <div className="flex gap-1 flex-wrap">
                            {(s.roles ?? []).map(role => <RoleBadge key={role} role={role} />)}
                          </div>
                        </td>
                        <td className="py-1.5 px-2 text-right tabular-nums text-orange-400">{fmtK(s.expected_value)}</td>
                        <td className="py-1.5 px-2">
                          <DistributionBar
                            p10={s.percentile_10}
                            p50={s.percentile_50}
                            ev={s.expected_value}
                            p80={s.percentile_80}
                            p95={s.percentile_95}
                          />
                        </td>
                        <td className="py-1.5 px-2 text-right tabular-nums text-zinc-400">
                          {s.p_positive != null ? `${Math.round(s.p_positive * 100)}%` : '—'}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
          </div>{/* end reSimulating opacity wrapper */}
        </div>
      )}

      {/* Decision Trace Inspector — baseline controls + comparison banner */}
      {briefResult?.decision_trace && (() => {
        const comparisonActive =
          briefResult.decision_trace?.trace_version === '22.5' &&
          baselineBrief?.decision_trace?.trace_version === '22.5'
        return (
          <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => setBaselineBrief(briefResult)}
                className="px-3 py-1 bg-zinc-700 hover:bg-zinc-600 text-zinc-200 rounded text-xs font-medium transition-colors"
              >
                {baselineBrief !== null ? 'Baseline set ✓' : 'Set as baseline'}
              </button>
              {baselineBrief !== null && (
                <button
                  onClick={() => setBaselineBrief(null)}
                  className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 rounded text-xs font-medium transition-colors"
                >
                  Clear comparison
                </button>
              )}
            </div>
            {comparisonActive && (
              <p className="text-xs text-zinc-400">
                Comparison mode active — showing current vs baseline
              </p>
            )}
            <DecisionTraceInspector
              trace={briefResult.decision_trace}
              riderNameMap={Object.fromEntries(riders.map(r => [r.holdet_id, r.name]))}
              candidates={briefResult.captain_candidates ?? []}
              captainRiderId={briefResult.captain_recommendation?.rider_id}
              baselineTrace={comparisonActive ? baselineBrief?.decision_trace : undefined}
              baselineCandidates={comparisonActive ? (baselineBrief?.captain_candidates ?? []) : undefined}
              baselineCaptainRiderId={comparisonActive ? baselineBrief?.captain_recommendation?.rider_id : undefined}
            />
          </div>
        )
      })()}

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
                const inTeam = myTeamIds.includes(r.holdet_id)
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
