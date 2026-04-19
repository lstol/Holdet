'use client'
import { useEffect, useState } from 'react'
import { createClient, Stage, GameState } from '@/lib/supabase'

const RACE = 'giro_2026'

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

export default function StagesPage() {
  const sb = createClient()
  const [stages, setStages] = useState<Stage[]>([])
  const [gs, setGs] = useState<GameState | null>(null)
  const [selected, setSelected] = useState<Stage | null>(null)
  const [results, setResults] = useState<Record<number, unknown>>({})

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()

      const [stagesRes, resultsRes] = await Promise.all([
        sb.from('stages').select('*').eq('race', RACE).order('number'),
        sb.from('stage_results').select('*').eq('race', RACE),
      ])
      setStages((stagesRes.data as Stage[]) ?? [])

      const rm: Record<number, unknown> = {}
      for (const r of (resultsRes.data ?? []) as { stage_number: number; result_json: unknown }[]) {
        rm[r.stage_number] = r.result_json
      }
      setResults(rm)

      if (user) {
        const gsRes = await sb.from('game_state').select('*').eq('user_id', user.id).eq('race', RACE).single()
        setGs(gsRes.data as GameState | null)
      }
    }
    load()
  }, [])

  const currentStage = gs?.current_stage ?? 1

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Stages</h1>

      <div className="grid grid-cols-1 gap-2">
        {stages.map(s => {
          const completed = (gs?.stages_completed as number[] | undefined)?.includes(s.number)
          const isCurrent = s.number === currentStage
          const result = results[s.number] as { finish_order?: string[] } | undefined
          return (
            <div key={s.id}>
              <button
                onClick={() => setSelected(selected?.id === s.id ? null : s)}
                className={`w-full text-left bg-zinc-900 rounded-xl p-3 border transition-colors ${
                  isCurrent ? 'border-orange-600' :
                  completed ? 'border-zinc-700' : 'border-zinc-800'
                } hover:border-zinc-600`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`font-bold w-8 text-sm ${isCurrent ? 'text-orange-400' : 'text-zinc-400'}`}>
                    S{s.number}
                  </span>
                  <StageBadge type={s.stage_type} />
                  <span className="text-zinc-200 text-sm flex-1">
                    {s.start_location} → {s.finish_location}
                  </span>
                  <span className="text-zinc-500 text-xs">{s.distance_km?.toFixed(0)}km</span>
                  {s.date && <span className="text-zinc-600 text-xs hidden sm:inline">{s.date}</span>}
                  {completed && <span className="text-green-500 text-xs">✓</span>}
                  {isCurrent && <span className="text-orange-400 text-xs font-bold">CURRENT</span>}
                </div>
              </button>

              {/* Detail panel */}
              {selected?.id === s.id && (
                <div className="bg-zinc-900 border border-zinc-700 rounded-b-xl -mt-1 p-4 space-y-3">
                  {s.image_url && (
                    <img src={s.image_url} alt={`Stage ${s.number} profile`}
                      className="w-full rounded-lg max-h-40 object-cover" />
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                    {s.profile_score != null && (
                      <div>
                        <div className="text-zinc-500 text-xs">ProfileScore</div>
                        <div className="text-zinc-200 font-medium">{s.profile_score}</div>
                      </div>
                    )}
                    {s.gradient_final_km != null && (
                      <div>
                        <div className="text-zinc-500 text-xs">Final km gradient</div>
                        <div className="text-zinc-200 font-medium">{s.gradient_final_km}%</div>
                      </div>
                    )}
                    {s.ps_final_25k != null && (
                      <div>
                        <div className="text-zinc-500 text-xs">PS final 25k</div>
                        <div className="text-zinc-200 font-medium">{s.ps_final_25k}</div>
                      </div>
                    )}
                    {s.distance_km != null && (
                      <div>
                        <div className="text-zinc-500 text-xs">Distance</div>
                        <div className="text-zinc-200 font-medium">{s.distance_km.toFixed(1)}km</div>
                      </div>
                    )}
                  </div>
                  {s.notes && (
                    <p className="text-zinc-400 text-sm">{s.notes}</p>
                  )}
                  {result?.finish_order && result.finish_order.length > 0 && (
                    <div>
                      <div className="text-zinc-500 text-xs mb-1">Stage result</div>
                      <div className="flex gap-2 flex-wrap">
                        {result.finish_order.slice(0, 10).map((rid: string, i: number) => (
                          <span key={rid} className="text-xs bg-zinc-800 px-2 py-0.5 rounded">
                            {i + 1}. {rid}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {stages.length === 0 && (
        <p className="text-zinc-500 text-sm text-center mt-12">
          No stage data. Run <code className="text-orange-400">sync_to_supabase.py</code> after ingest.
        </p>
      )}
    </div>
  )
}
