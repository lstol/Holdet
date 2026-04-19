'use client'
import { useEffect, useState, useMemo } from 'react'
import { createClient, Rider, ProbSnapshot, GameState } from '@/lib/supabase'
import { RefreshCw } from 'lucide-react'

const RACE = 'giro_2026'
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function fmt(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('da-DK')
}

type SortKey = 'value' | 'delta' | 'p_win'

export default function RidersPage() {
  const sb = createClient()
  const [riders, setRiders] = useState<Rider[]>([])
  const [probs, setProbs] = useState<Record<string, ProbSnapshot>>({})
  const [gs, setGs] = useState<GameState | null>(null)

  const [filterTeam, setFilterTeam] = useState('')
  const [filterStatus, setFilterStatus] = useState('all')
  const [filterMyTeam, setFilterMyTeam] = useState(false)
  const [minValue, setMinValue] = useState(0)
  const [maxValue, setMaxValue] = useState(20_000_000)
  const [sort, setSort] = useState<SortKey>('value')
  const [ingestLoading, setIngestLoading] = useState(false)
  const [ingestMsg, setIngestMsg] = useState<string | null>(null)
  const [user, setUser] = useState<any>(null)

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      setUser(user)
      if (!user) return
      const [ridersRes, gsRes] = await Promise.all([
        sb.from('riders').select('*').eq('user_id', user.id).eq('race', RACE),
        sb.from('game_state').select('*').eq('user_id', user.id).eq('race', RACE).single(),
      ])
      const riderList = (ridersRes.data as Rider[]) ?? []
      setRiders(riderList)
      setGs(gsRes.data as GameState | null)
      const currentStage = (gsRes.data as GameState | null)?.current_stage ?? 1
      const { data: probData } = await sb
        .from('prob_snapshots').select('*')
        .eq('user_id', user.id).eq('race', RACE).eq('stage_number', currentStage)
      const map: Record<string, ProbSnapshot> = {}
      for (const p of (probData ?? []) as ProbSnapshot[]) map[p.rider_id] = p
      setProbs(map)
      const maxV = Math.max(...riderList.map(r => r.value ?? 0), 20_000_000)
      setMaxValue(maxV)
    }
    load()
  }, [])

  const teams = useMemo(() => [...new Set(riders.map(r => r.team_abbr).filter(Boolean))].sort(), [riders])

  const filtered = useMemo(() => {
    return riders
      .filter(r => {
        if (filterTeam && r.team_abbr !== filterTeam) return false
        if (filterStatus !== 'all' && r.status !== filterStatus) return false
        if (filterMyTeam && !gs?.my_team?.includes(r.holdet_id)) return false
        const v = r.value ?? 0
        if (v < minValue || v > maxValue) return false
        return true
      })
      .sort((a, b) => {
        if (sort === 'value') return (b.value ?? 0) - (a.value ?? 0)
        if (sort === 'delta') return ((b.value ?? 0) - (b.start_value ?? 0)) - ((a.value ?? 0) - (a.start_value ?? 0))
        if (sort === 'p_win') return (probs[b.holdet_id]?.p_win ?? 0) - (probs[a.holdet_id]?.p_win ?? 0)
        return 0
      })
  }, [riders, filterTeam, filterStatus, filterMyTeam, minValue, maxValue, sort, probs, gs])

  const ingest = async () => {
    setIngestLoading(true)
    setIngestMsg(null)
    try {
      const res = await fetch(`${API}/ingest`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Ingest failed')
      setIngestMsg(`✓ ${d.riders_count} riders refreshed`)
    } catch (e: unknown) {
      setIngestMsg(`✗ ${e instanceof Error ? e.message : 'Server not running?'}`)
    } finally {
      setIngestLoading(false)
    }
  }

  if (!user) return (
    <div className="text-center mt-24 space-y-4">
      <p className="text-zinc-400">You need to be logged in to use the riders.</p>
      <a href="/auth" className="px-4 py-2 bg-orange-700 hover:bg-orange-600 text-white rounded-lg text-sm font-medium">Sign in</a>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">Riders</h1>
        <div className="flex items-center gap-2">
          <button onClick={ingest} disabled={ingestLoading}
            className="flex items-center gap-2 px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-zinc-200 rounded-lg text-sm font-medium transition-colors">
            <RefreshCw size={14} className={ingestLoading ? 'animate-spin' : ''} />
            {ingestLoading ? 'Refreshing…' : 'Ingest'}
          </button>
          {ingestMsg && (
            <span className={`text-xs ${ingestMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>{ingestMsg}</span>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="bg-zinc-900 rounded-xl p-3 border border-zinc-800 flex flex-wrap gap-3 items-end text-sm">
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Team</label>
          <select value={filterTeam} onChange={e => setFilterTeam(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-xs">
            <option value="">All</option>
            {teams.map(t => <option key={t} value={t!}>{t}</option>)}
          </select>
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Status</label>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-xs">
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="dns">DNS</option>
            <option value="dnf">DNF</option>
          </select>
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Sort by</label>
          <select value={sort} onChange={e => setSort(e.target.value as SortKey)}
            className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-xs">
            <option value="value">Value</option>
            <option value="delta">Value Δ</option>
            <option value="p_win">p_win</option>
          </select>
        </div>
        <label className="flex items-center gap-2 cursor-pointer mt-auto pb-1">
          <input type="checkbox" checked={filterMyTeam}
            onChange={e => setFilterMyTeam(e.target.checked)}
            className="accent-orange-500" />
          <span className="text-zinc-400 text-xs">My team only</span>
        </label>
        <div className="ml-auto text-zinc-500 text-xs mt-auto pb-1">{filtered.length} riders</div>
      </div>

      {/* Table */}
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
              <th className="text-left px-3 py-2">Rider</th>
              <th className="text-left px-3 py-2 hidden sm:table-cell">Team</th>
              <th className="text-right px-3 py-2">Value</th>
              <th className="text-right px-3 py-2">Δ</th>
              <th className="text-right px-3 py-2">Win%</th>
              <th className="text-right px-3 py-2 hidden sm:table-cell">Top15%</th>
              <th className="text-center px-3 py-2 hidden sm:table-cell">Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => {
              const d = (r.value ?? 0) - (r.start_value ?? 0)
              const p = probs[r.holdet_id]
              const inTeam = gs?.my_team?.includes(r.holdet_id)
              return (
                <tr key={r.holdet_id}
                  className={`border-b border-zinc-800/50 hover:bg-zinc-800/40 ${inTeam ? 'bg-zinc-800/20' : ''}`}>
                  <td className="px-3 py-2">
                    <span className={inTeam ? 'text-white font-medium' : 'text-zinc-300'}>
                      {gs?.captain === r.holdet_id && <span className="text-yellow-400 mr-1">★</span>}
                      {r.name}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-zinc-500 text-xs hidden sm:table-cell">{r.team_abbr}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-zinc-300">{fmt(r.value)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums text-xs ${
                    d > 0 ? 'text-green-400' : d < 0 ? 'text-red-400' : 'text-zinc-600'
                  }`}>
                    {d !== 0 ? (d > 0 ? '+' : '') + fmt(d) : '—'}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {p?.p_win != null ? `${(p.p_win * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums hidden sm:table-cell">
                    {p?.p_top15 != null ? `${(p.p_top15 * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="px-3 py-2 text-center hidden sm:table-cell">
                    {r.status !== 'active' && (
                      <span className="text-red-400 text-xs uppercase">{r.status}</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
