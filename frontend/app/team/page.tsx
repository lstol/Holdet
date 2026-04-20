'use client'
import { useEffect, useState } from 'react'
import { createClient, Rider, GameState } from '@/lib/supabase'
import { Users } from 'lucide-react'

const RACE = 'giro_2026'
const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

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

  // Team editor state
  const [showEditor, setShowEditor] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [captainId, setCaptainId] = useState('')
  const [filterText, setFilterText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

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
      const rList = (ridersRes.data as Rider[]) ?? []
      setRiders(rList)
      setGs(gsRes.data as GameState | null)

      // Pre-populate editor with current team
      const currentTeam = (gsRes.data as GameState | null)?.my_team ?? []
      const currentCaptain = (gsRes.data as GameState | null)?.captain ?? ''
      setSelectedIds(currentTeam)
      setCaptainId(currentCaptain)
    }
    load()
  }, [])

  const teamRiders = riders.filter(r => gs?.my_team?.includes(r.holdet_id))
  const totalValue = teamRiders.reduce((s, r) => s + (r.value ?? 0), 0)
  const totalStart = teamRiders.reduce((s, r) => s + (r.start_value ?? 0), 0)
  const totalDelta = totalValue - totalStart

  const toggleRider = (id: string) => {
    setSelectedIds(prev => {
      if (prev.includes(id)) {
        const next = prev.filter(x => x !== id)
        if (captainId === id) setCaptainId('')
        return next
      }
      if (prev.length >= 8) return prev
      return [...prev, id]
    })
  }

  const saveTeam = async () => {
    if (selectedIds.length !== 8) { setSaveMsg('Select exactly 8 riders'); return }
    if (!captainId) { setSaveMsg('Select a captain'); return }
    setSaving(true)
    setSaveMsg(null)
    try {
      const res = await fetch(`${API}/team`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ my_team: selectedIds, captain: captainId }),
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail ?? 'Save failed')
      setSaveMsg('✓ Team saved to state.json')
      setShowEditor(false)
    } catch (e: unknown) {
      const msg = `✗ ${e instanceof Error ? e.message : 'Server not running?'}`
      setSaveMsg(msg)
      console.error('saveTeam error:', e)
    } finally {
      setSaving(false)
    }
  }

  const filteredRiders = riders
    .filter(r => !filterText || r.name?.toLowerCase().includes(filterText.toLowerCase()) || r.team?.toLowerCase().includes(filterText.toLowerCase()))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))

  if (!user) return (
    <div className="text-center mt-24 space-y-4">
      <p className="text-zinc-400">You need to be logged in to use the team.</p>
      <a href="/auth" className="px-4 py-2 bg-orange-700 hover:bg-orange-600 text-white rounded-lg text-sm font-medium">Sign in</a>
    </div>
  )

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">My Team</h1>
          <p className="text-zinc-500 text-sm mt-1">Stage {gs?.current_stage ?? '—'} / {gs?.total_stages ?? 21}</p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <div className="text-right">
            <div className="text-zinc-400 text-sm">Bank</div>
            <div className="text-green-400 font-bold text-lg">{fmt(gs?.bank ?? null)} kr</div>
          </div>
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2 items-center">
        <button onClick={() => setShowEditor(e => !e)}
          className="flex items-center gap-2 px-3 py-1.5 bg-orange-700 hover:bg-orange-600 text-white rounded-lg text-sm font-medium transition-colors">
          <Users size={14} />
          Update My Team
        </button>
        {saveMsg && (
          <span className={`text-xs ${saveMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
            {saveMsg}
          </span>
        )}
      </div>
      {riders.length === 0 && user && (
        <p className="text-yellow-400 text-xs">
          No riders loaded — run Refresh Riders on the Briefing page first.
        </p>
      )}

      {/* Team editor */}
      {showEditor && (
        <div className="bg-zinc-900 rounded-xl border border-zinc-700 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-white text-sm">Select 8 riders + captain</h2>
            <span className={`text-xs font-mono ${selectedIds.length === 8 ? 'text-green-400' : 'text-orange-400'}`}>
              {selectedIds.length}/8
            </span>
          </div>

          <input
            type="text"
            placeholder="Search rider or team…"
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-zinc-200 text-sm placeholder:text-zinc-600"
          />

          {/* Captain selector */}
          {selectedIds.length > 0 && (
            <div>
              <label className="text-zinc-500 text-xs block mb-1">Captain</label>
              <select value={captainId} onChange={e => setCaptainId(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-200 text-sm">
                <option value="">— pick captain —</option>
                {selectedIds.map(id => {
                  const r = riders.find(x => x.holdet_id === id)
                  return r ? <option key={id} value={id}>{r.name}</option> : null
                })}
              </select>
            </div>
          )}

          <div className="max-h-72 overflow-y-auto space-y-1">
            {filteredRiders.map(r => {
              const selected = selectedIds.includes(r.holdet_id)
              const isCaptain = captainId === r.holdet_id
              const disabled = !selected && selectedIds.length >= 8
              return (
                <label key={r.holdet_id}
                  className={`flex items-center justify-between gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                    selected ? 'bg-zinc-700 text-white' : 'bg-zinc-800/60 text-zinc-400'
                  } ${disabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-zinc-700'}`}>
                  <div className="flex items-center gap-2 min-w-0">
                    <input type="checkbox" checked={selected} disabled={disabled}
                      onChange={() => toggleRider(r.holdet_id)}
                      className="accent-orange-500 shrink-0" />
                    <span className="text-sm font-medium truncate">
                      {isCaptain && <span className="text-yellow-400 mr-1">★</span>}
                      {r.name}
                    </span>
                    <span className="text-xs text-zinc-500 hidden sm:inline">{r.team_abbr}</span>
                  </div>
                  <span className="text-xs tabular-nums text-zinc-400 shrink-0">
                    {(r.value ?? 0) >= 1e6 ? `${((r.value ?? 0) / 1e6).toFixed(1)}M` : fmt(r.value)}
                  </span>
                </label>
              )
            })}
          </div>

          <div className="flex gap-2 pt-1">
            <button onClick={saveTeam} disabled={saving || selectedIds.length !== 8 || !captainId}
              className="px-4 py-1.5 bg-orange-700 hover:bg-orange-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
              {saving ? 'Saving…' : 'Save Team'}
            </button>
            <button onClick={() => setShowEditor(false)}
              className="px-4 py-1.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded-lg text-sm font-medium transition-colors">
              Cancel
            </button>
          </div>
          {saveMsg && (
            <p className={`text-xs ${saveMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>{saveMsg}</p>
          )}
        </div>
      )}

      {/* Team cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {teamRiders.map(r => {
          const d = delta(r.value, r.start_value)
          const isCaptain = gs?.captain === r.holdet_id
          return (
            <div key={r.holdet_id}
              className={`bg-zinc-900 rounded-xl p-4 border ${r.status !== 'active' ? 'border-red-800' : 'border-zinc-800'}`}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    {isCaptain && (
                      <span className="text-yellow-400 text-xs font-bold bg-yellow-900/40 px-1.5 py-0.5 rounded">★ CAPTAIN</span>
                    )}
                    <span className={`font-semibold ${r.status !== 'active' ? 'text-red-300' : 'text-white'}`}>{r.name}</span>
                  </div>
                  <p className="text-zinc-500 text-xs mt-0.5">{r.team}</p>
                </div>
                {r.status !== 'active' && (
                  <span className="text-red-400 text-xs font-bold bg-red-900/40 px-2 py-0.5 rounded">{r.status.toUpperCase()}</span>
                )}
              </div>
              <div className="flex justify-between items-end mt-3">
                <span className="text-zinc-300 font-medium tabular-nums">{fmt(r.value)} kr</span>
                {d != null && (
                  <span className={`text-sm tabular-nums font-medium ${d > 0 ? 'text-green-400' : d < 0 ? 'text-red-400' : 'text-zinc-500'}`}>
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
          No team data. Click <b>Update My Team</b> above or run <code className="text-orange-400">sync_to_supabase.py</code> after ingest.
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
