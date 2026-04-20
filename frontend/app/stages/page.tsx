'use client'
import { useEffect, useState } from 'react'
import { createClient, Stage, GameState, Rider } from '@/lib/supabase'
import { CheckSquare } from 'lucide-react'

const RACE = 'giro_2026'
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function parseJsonField(val: unknown): unknown[] {
  if (Array.isArray(val)) return val
  if (typeof val === 'string') { try { return JSON.parse(val) } catch { return [] } }
  return []
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

// Rider autocomplete input
function RiderSearch({
  riders,
  value,
  onChange,
  placeholder,
}: {
  riders: Rider[]
  value: string  // holdet_id
  onChange: (id: string) => void
  placeholder?: string
}) {
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)

  const selected = riders.find(r => r.holdet_id === value)
  useEffect(() => { if (selected?.name) setText(selected.name) }, [value])

  const matches = text.length >= 2
    ? riders.filter(r => r.name?.toLowerCase().includes(text.toLowerCase())).slice(0, 8)
    : []

  return (
    <div className="relative">
      <input
        className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-xs"
        value={text}
        placeholder={placeholder ?? 'Search rider…'}
        onChange={e => { setText(e.target.value); onChange(''); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && matches.length > 0 && (
        <div className="absolute z-10 mt-0.5 w-full bg-zinc-900 border border-zinc-700 rounded shadow-xl max-h-40 overflow-y-auto">
          {matches.map(r => (
            <button key={r.holdet_id} type="button"
              className="w-full text-left px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700 flex justify-between"
              onMouseDown={() => { onChange(r.holdet_id); setText(r.name ?? ''); setOpen(false) }}>
              <span>{r.name}</span>
              <span className="text-zinc-500">{r.team_abbr}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function StagesPage() {
  const sb = createClient()
  const [stages, setStages] = useState<Stage[]>([])
  const [gs, setGs] = useState<GameState | null>(null)
  const [riders, setRiders] = useState<Rider[]>([])
  const [selected, setSelected] = useState<Stage | null>(null)
  const [results, setResults] = useState<Record<number, unknown>>({})

  // Settle form
  const [settleOpen, setSettleOpen] = useState(false)
  const [settling, setSettling] = useState(false)
  const [settleMsg, setSettleMsg] = useState<string | null>(null)

  // Form fields (holdet_ids)
  const [finishOrder, setFinishOrder] = useState<string[]>(Array(15).fill(''))
  const [dnfRiders, setDnfRiders] = useState<string[]>([])
  const [gcStandings, setGcStandings] = useState<string[]>(Array(10).fill(''))
  const [jerseyWinners, setJerseyWinners] = useState<Record<string, string>>({})
  const [mostAggressive, setMostAggressive] = useState('')
  const [holdetBank, setHoldetBank] = useState('')

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      const [stagesRes, resultsRes, ridersRes] = await Promise.all([
        sb.from('stages').select('*').eq('race', RACE).order('number'),
        sb.from('stage_results').select('*').eq('race', RACE),
        user ? sb.from('riders').select('*').eq('user_id', user.id).eq('race', RACE) : Promise.resolve({ data: [] }),
      ])
      setStages((stagesRes.data as Stage[]) ?? [])
      setRiders((ridersRes.data as Rider[]) ?? [])
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

  const submitSettle = async () => {
    if (!selected) return
    setSettling(true)
    setSettleMsg(null)
    try {
      const payload = {
        stage: selected.number,
        finish_order: finishOrder.filter(Boolean),
        dnf_riders: dnfRiders.filter(Boolean),
        dns_riders: [],
        gc_standings: gcStandings.filter(Boolean),
        jersey_winners: Object.fromEntries(Object.entries(jerseyWinners).filter(([, v]) => v)),
        most_aggressive: mostAggressive || null,
        sprint_point_winners: {},
        kom_point_winners: {},
        times_behind_winner: {},
        holdet_bank: holdetBank ? parseFloat(holdetBank.replace(/[.,\s]/g, '')) : null,
      }
      const res = await fetch(`${API}/settle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Settle failed')
      const delta = d.total_bank_delta
      setSettleMsg(`✓ Stage ${selected.number} settled. Bank Δ: ${delta > 0 ? '+' : ''}${Number(delta).toLocaleString('da-DK')} kr. New bank: ${Number(d.new_bank).toLocaleString('da-DK')} kr`)
      setSettleOpen(false)
    } catch (e: unknown) {
      setSettleMsg(`✗ ${e instanceof Error ? e.message : 'Server not running?'}`)
    } finally {
      setSettling(false)
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Stages</h1>

      <div className="grid grid-cols-1 gap-2">
        {stages.map(s => {
          const completed = (parseJsonField(gs?.stages_completed) as number[]).includes(s.number)
          const isCurrent = s.number === currentStage
          const result = results[s.number] as { finish_order?: string[] } | undefined
          const isSelected = selected?.id === s.id
          return (
            <div key={s.id}>
              <button
                onClick={() => {
                  setSelected(isSelected ? null : s)
                  setSettleOpen(false)
                  setSettleMsg(null)
                }}
                className={`w-full text-left bg-zinc-900 rounded-xl p-3 border transition-colors ${
                  isCurrent ? 'border-orange-600' : completed ? 'border-zinc-700' : 'border-zinc-800'
                } hover:border-zinc-600`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`font-bold w-8 text-sm ${isCurrent ? 'text-orange-400' : 'text-zinc-400'}`}>S{s.number}</span>
                  <StageBadge type={s.stage_type} />
                  <span className="text-zinc-200 text-sm flex-1">{s.start_location} → {s.finish_location}</span>
                  <span className="text-zinc-500 text-xs">{s.distance_km?.toFixed(0)}km</span>
                  {s.date && <span className="text-zinc-600 text-xs hidden sm:inline">{s.date}</span>}
                  {completed && <span className="text-green-500 text-xs">✓</span>}
                  {isCurrent && <span className="text-orange-400 text-xs font-bold">CURRENT</span>}
                </div>
              </button>

              {/* Detail panel */}
              {isSelected && (
                <div className="bg-zinc-900 border border-zinc-700 rounded-b-xl -mt-1 p-4 space-y-4">
                  {s.image_url && (
                    <img src={s.image_url} alt={`Stage ${s.number} profile`}
                      className="w-full h-auto rounded-lg" />
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                    {s.distance_km != null && (
                      <div><div className="text-zinc-500 text-xs">Distance</div><div className="text-zinc-200 font-medium">{s.distance_km.toFixed(1)}km</div></div>
                    )}
                    {s.vertical_meters != null && (
                      <div><div className="text-zinc-500 text-xs">Vertical meters</div><div className="text-zinc-200 font-medium">{s.vertical_meters.toLocaleString('da-DK')}m</div></div>
                    )}
                    {s.start_location && (
                      <div><div className="text-zinc-500 text-xs">Start</div><div className="text-zinc-200 font-medium">{s.start_location}</div></div>
                    )}
                    {s.finish_location && (
                      <div><div className="text-zinc-500 text-xs">Finish</div><div className="text-zinc-200 font-medium">{s.finish_location}</div></div>
                    )}
                    {s.profile_score != null && (
                      <div><div className="text-zinc-500 text-xs">ProfileScore</div><div className="text-zinc-200 font-medium">{s.profile_score}</div></div>
                    )}
                    {s.gradient_final_km != null && (
                      <div><div className="text-zinc-500 text-xs">Final km gradient</div><div className="text-zinc-200 font-medium">{s.gradient_final_km}%</div></div>
                    )}
                    {s.ps_final_25k != null && (
                      <div><div className="text-zinc-500 text-xs">PS final 25k</div><div className="text-zinc-200 font-medium">{s.ps_final_25k}</div></div>
                    )}
                  </div>
                  {s.notes && <p className="text-zinc-400 text-sm">{s.notes}</p>}

                  {/* Stage result (if settled) */}
                  {result?.finish_order && result.finish_order.length > 0 && (
                    <div>
                      <div className="text-zinc-500 text-xs mb-1">Stage result</div>
                      <div className="flex gap-2 flex-wrap">
                        {result.finish_order.slice(0, 10).map((rid: string, i: number) => (
                          <span key={rid} className="text-xs bg-zinc-800 px-2 py-0.5 rounded">{i + 1}. {rid}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Settle button */}
                  {!completed && (
                    <div className="space-y-3">
                      <button onClick={() => setSettleOpen(o => !o)}
                        className="flex items-center gap-2 px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-200 rounded-lg text-sm font-medium transition-colors">
                        <CheckSquare size={14} />
                        Settle Stage {s.number}
                      </button>

                      {settleOpen && (
                        <div className="bg-zinc-800 rounded-xl p-4 space-y-4 text-sm">
                          <p className="text-zinc-400 text-xs">Enter holdet_ids. Search by name below, then copy the ID shown.</p>

                          {/* Finish order */}
                          <div>
                            <p className="text-zinc-400 text-xs mb-2 font-semibold">Top-15 finish order</p>
                            <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                              {Array.from({ length: 15 }, (_, i) => (
                                <div key={i}>
                                  <div className="text-zinc-600 text-xs mb-0.5">{i + 1}.</div>
                                  <RiderSearch riders={riders} value={finishOrder[i] ?? ''}
                                    onChange={id => setFinishOrder(prev => { const a = [...prev]; a[i] = id; return a })} />
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* GC standings */}
                          <div>
                            <p className="text-zinc-400 text-xs mb-2 font-semibold">GC standings top 10</p>
                            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                              {Array.from({ length: 10 }, (_, i) => (
                                <div key={i}>
                                  <div className="text-zinc-600 text-xs mb-0.5">{i + 1}.</div>
                                  <RiderSearch riders={riders} value={gcStandings[i] ?? ''}
                                    onChange={id => setGcStandings(prev => { const a = [...prev]; a[i] = id; return a })} />
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* Jerseys */}
                          <div>
                            <p className="text-zinc-400 text-xs mb-2 font-semibold">Jersey winners</p>
                            <div className="grid grid-cols-2 gap-2">
                              {['yellow', 'green', 'polkadot', 'white'].map(j => (
                                <div key={j}>
                                  <div className="text-zinc-500 text-xs capitalize mb-0.5">{j}</div>
                                  <RiderSearch riders={riders} value={jerseyWinners[j] ?? ''}
                                    onChange={id => setJerseyWinners(prev => ({ ...prev, [j]: id }))} />
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* Most aggressive */}
                          <div>
                            <p className="text-zinc-400 text-xs mb-1 font-semibold">Most aggressive (red number)</p>
                            <div className="max-w-xs">
                              <RiderSearch riders={riders} value={mostAggressive}
                                onChange={setMostAggressive} placeholder="Optional" />
                            </div>
                          </div>

                          {/* Holdet bank validation */}
                          <div>
                            <label className="text-zinc-400 text-xs block mb-1 font-semibold">Your Holdet bank after stage (optional)</label>
                            <input type="text" value={holdetBank} onChange={e => setHoldetBank(e.target.value)}
                              placeholder="e.g. 51234567"
                              className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-xs w-40" />
                          </div>

                          <div className="flex gap-2 pt-1">
                            <button onClick={submitSettle} disabled={settling || finishOrder.filter(Boolean).length < 3}
                              className="px-4 py-1.5 bg-orange-700 hover:bg-orange-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
                              {settling ? 'Settling…' : 'Submit'}
                            </button>
                            <button onClick={() => setSettleOpen(false)}
                              className="px-4 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-lg text-sm font-medium transition-colors">
                              Cancel
                            </button>
                          </div>
                          {settleMsg && (
                            <p className={`text-xs ${settleMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>{settleMsg}</p>
                          )}
                        </div>
                      )}
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
