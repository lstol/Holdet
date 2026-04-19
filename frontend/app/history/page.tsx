'use client'
import { useEffect, useState } from 'react'
import { createClient, BrierRecord, ValueDelta } from '@/lib/supabase'
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend
} from 'recharts'

const RACE = 'giro_2026'

export default function HistoryPage() {
  const sb = createClient()
  const [brier, setBrier] = useState<BrierRecord[]>([])
  const [valueHistory, setValueHistory] = useState<ValueDelta[]>([])
  const [ridersMap, setRidersMap] = useState<Record<string, string>>({})
  const [user, setUser] = useState<any>(null)

  useEffect(() => {
    async function load() {
      const { data: { user } } = await sb.auth.getUser()
      setUser(user)
      if (!user) return
      const [brierRes, valueRes, ridersRes] = await Promise.all([
        sb.from('brier_history').select('*').eq('user_id', user.id).eq('race', RACE).order('stage_number'),
        sb.from('value_history').select('*').eq('user_id', user.id).eq('race', RACE).order('stage_number'),
        sb.from('riders').select('holdet_id,name').eq('user_id', user.id).eq('race', RACE),
      ])
      setBrier((brierRes.data as BrierRecord[]) ?? [])
      setValueHistory((valueRes.data as ValueDelta[]) ?? [])
      const m: Record<string, string> = {}
      for (const r of (ridersRes.data ?? []) as { holdet_id: string; name: string }[]) {
        m[r.holdet_id] = r.name
      }
      setRidersMap(m)
    }
    load()
  }, [])

  // Brier by stage (win event only for chart clarity)
  const stages = [...new Set(brier.map(b => b.stage_number))].sort((a, b) => a - b)
  const brierChartData = stages.map(stg => {
    const rows = brier.filter(b => b.stage_number === stg)
    const modelAvg = rows.reduce((s, r) => s + (r.model_brier ?? 0), 0) / (rows.length || 1)
    const manualRows = rows.filter(r => r.manual_brier != null)
    const manualAvg = manualRows.length
      ? manualRows.reduce((s, r) => s + (r.manual_brier ?? 0), 0) / manualRows.length
      : null
    return { stage: `S${stg}`, model: +modelAvg.toFixed(4), manual: manualAvg != null ? +manualAvg.toFixed(4) : undefined }
  })

  const stagesWon = brierChartData.filter(
    d => d.manual != null && d.manual < d.model
  ).length
  const stagesWithManual = brierChartData.filter(d => d.manual != null).length

  // Value history by stage (team total)
  const valueStages = [...new Set(valueHistory.map(v => v.stage_number))].sort((a, b) => a - b)
  let runningTotal = 0
  const valueChartData = valueStages.map(stg => {
    const rows = valueHistory.filter(v => v.stage_number === stg)
    const stageTotal = rows.reduce((s, r) => {
      const d = r.delta_json as Record<string, number>
      return s + (d.total_rider_value_delta ?? 0)
    }, 0)
    runningTotal += stageTotal
    return { stage: `S${stg}`, total: runningTotal }
  })

  if (!user) return (
    <div className="text-center mt-24 space-y-4">
      <p className="text-zinc-400">You need to be logged in to use the history.</p>
      <a href="/auth" className="px-4 py-2 bg-orange-700 hover:bg-orange-600 text-white rounded-lg text-sm font-medium">Sign in</a>
    </div>
  )

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">History</h1>

      {/* Team value chart */}
      {valueChartData.length > 0 && (
        <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide">
            Team value change (cumulative)
          </h2>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={valueChartData}>
              <XAxis dataKey="stage" stroke="#52525b" tick={{ fill: '#a1a1aa', fontSize: 11 }} />
              <YAxis stroke="#52525b" tick={{ fill: '#a1a1aa', fontSize: 11 }}
                tickFormatter={v => `${(v / 1000).toFixed(0)}k`} />
              <Tooltip
                contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 8 }}
                labelStyle={{ color: '#a1a1aa' }}
                formatter={(v: unknown) => [`${Number(v).toLocaleString('da-DK')} kr`, 'Value']}
              />
              <Line type="monotone" dataKey="total" stroke="#f97316" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Brier score chart */}
      {brierChartData.length > 0 && (
        <div className="bg-zinc-900 rounded-xl p-4 border border-zinc-800 space-y-2">
          <h2 className="text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide">
            Brier Score — model vs manual
          </h2>
          {stagesWithManual > 0 && (
            <p className="text-zinc-300 text-sm">
              You beat the model on{' '}
              <span className="text-green-400 font-semibold">{stagesWon}/{stagesWithManual}</span> stages
            </p>
          )}
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={brierChartData}>
              <XAxis dataKey="stage" stroke="#52525b" tick={{ fill: '#a1a1aa', fontSize: 11 }} />
              <YAxis stroke="#52525b" tick={{ fill: '#a1a1aa', fontSize: 11 }} domain={[0, 'auto']} />
              <Tooltip
                contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 8 }}
                labelStyle={{ color: '#a1a1aa' }}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: '#a1a1aa' }} />
              <Line type="monotone" dataKey="model" stroke="#71717a" strokeWidth={2} dot={false} name="Model" />
              <Line type="monotone" dataKey="manual" stroke="#a855f7" strokeWidth={2} dot={false} name="Manual" />
            </LineChart>
          </ResponsiveContainer>
          <p className="text-zinc-600 text-xs">Lower Brier score = better calibration</p>
        </div>
      )}

      {/* Value delta table */}
      {valueStages.length > 0 && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500 text-xs">
                <th className="text-left px-3 py-2">Stage</th>
                <th className="text-left px-3 py-2">Rider</th>
                <th className="text-right px-3 py-2">Value Δ</th>
                <th className="text-right px-3 py-2 hidden sm:table-cell">Stage pos.</th>
                <th className="text-right px-3 py-2 hidden sm:table-cell">GC standing</th>
              </tr>
            </thead>
            <tbody>
              {valueHistory.map(v => {
                const d = v.delta_json as Record<string, number>
                const total = d.total_rider_value_delta ?? 0
                return (
                  <tr key={v.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td className="px-3 py-2 text-zinc-500">S{v.stage_number}</td>
                    <td className="px-3 py-2 text-zinc-300">{ridersMap[v.rider_id] ?? v.rider_id}</td>
                    <td className={`px-3 py-2 text-right tabular-nums font-medium ${
                      total > 0 ? 'text-green-400' : total < 0 ? 'text-red-400' : 'text-zinc-500'
                    }`}>
                      {total > 0 ? '+' : ''}{total.toLocaleString('da-DK')}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-500 tabular-nums hidden sm:table-cell">
                      {(d.stage_position_value ?? 0).toLocaleString('da-DK')}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-500 tabular-nums hidden sm:table-cell">
                      {(d.gc_standing_value ?? 0).toLocaleString('da-DK')}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {brier.length === 0 && valueHistory.length === 0 && (
        <p className="text-zinc-500 text-sm text-center mt-12">
          No history yet. Data appears here after each settled stage.
        </p>
      )}
    </div>
  )
}
