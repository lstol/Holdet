'use client'
import { useEffect, useState } from 'react'
import { createClient, Rider, GameState } from '@/lib/supabase'

const RACE = 'giro_2026'

function fmt(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('da-DK')
}

function delta(current: number | null, start: number | null) {
  if (current == null || start == null) return null
  return current - start
}

export default function TeamPage() {
  const sb = createClient()
  const [riders, setRiders] = useState<Rider[]>([])
  const [gs, setGs] = useState<GameState | null>(null)

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      if (!user) return
      const [ridersRes, gsRes] = await Promise.all([
        sb.from('riders').select('*').eq('user_id', user.id).eq('race', RACE),
        sb.from('game_state').select('*').eq('user_id', user.id).eq('race', RACE).single(),
      ])
      setRiders((ridersRes.data as Rider[]) ?? [])
      setGs(gsRes.data as GameState | null)
    }
    load()
  }, [])

  const teamRiders = riders.filter(r => gs?.my_team?.includes(r.holdet_id))
  const totalValue = teamRiders.reduce((s, r) => s + (r.value ?? 0), 0)
  const totalStart = teamRiders.reduce((s, r) => s + (r.start_value ?? 0), 0)
  const totalDelta = totalValue - totalStart

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">My Team</h1>
          <p className="text-zinc-500 text-sm mt-1">
            Stage {gs?.current_stage ?? '—'} / {gs?.total_stages ?? 21}
          </p>
        </div>
        <div className="text-right">
          <div className="text-zinc-400 text-sm">Bank</div>
          <div className="text-green-400 font-bold text-lg">{fmt(gs?.bank ?? null)} kr</div>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {teamRiders.map(r => {
          const d = delta(r.value, r.start_value)
          const isCaptain = gs?.captain === r.holdet_id
          return (
            <div key={r.holdet_id}
              className={`bg-zinc-900 rounded-xl p-4 border ${
                r.status !== 'active' ? 'border-red-800' : 'border-zinc-800'
              }`}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    {isCaptain && (
                      <span className="text-yellow-400 text-xs font-bold bg-yellow-900/40 px-1.5 py-0.5 rounded">
                        ★ CAPTAIN
                      </span>
                    )}
                    <span className={`font-semibold ${r.status !== 'active' ? 'text-red-300' : 'text-white'}`}>
                      {r.name}
                    </span>
                  </div>
                  <p className="text-zinc-500 text-xs mt-0.5">{r.team}</p>
                </div>
                {r.status !== 'active' && (
                  <span className="text-red-400 text-xs font-bold bg-red-900/40 px-2 py-0.5 rounded">
                    {r.status.toUpperCase()}
                  </span>
                )}
              </div>
              <div className="flex justify-between items-end mt-3">
                <span className="text-zinc-300 font-medium tabular-nums">{fmt(r.value)} kr</span>
                {d != null && (
                  <span className={`text-sm tabular-nums font-medium ${
                    d > 0 ? 'text-green-400' : d < 0 ? 'text-red-400' : 'text-zinc-500'
                  }`}>
                    {d > 0 ? '+' : ''}{fmt(d)}
                  </span>
                )}
              </div>
              {r.jerseys?.length > 0 && (
                <div className="flex gap-1 mt-2">
                  {r.jerseys.map((j: string) => (
                    <span key={j} className="text-xs px-1.5 py-0.5 bg-zinc-700 rounded">{j}</span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {teamRiders.length === 0 && (
        <p className="text-zinc-500 text-sm text-center mt-12">
          No team data. Run <code className="text-orange-400">sync_to_supabase.py</code> after ingest.
        </p>
      )}

      {teamRiders.length > 0 && (
        <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800 flex flex-wrap gap-6">
          <div>
            <div className="text-zinc-500 text-xs">Total team value</div>
            <div className="font-bold text-white tabular-nums">{fmt(totalValue)} kr</div>
          </div>
          <div>
            <div className="text-zinc-500 text-xs">Change vs race start</div>
            <div className={`font-bold tabular-nums ${totalDelta >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {totalDelta >= 0 ? '+' : ''}{fmt(totalDelta)} kr
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
