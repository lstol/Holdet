'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase'

export default function AuthPage() {
  const sb = createClient()
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setMessage(null)
    setLoading(true)
    try {
      if (mode === 'login') {
        const { error } = await sb.auth.signInWithPassword({ email, password })
        if (error) throw error
        router.push('/briefing')
      } else {
        const { error } = await sb.auth.signUp({ email, password })
        if (error) throw error
        setMessage('Account created! Check your email to confirm, then log in.')
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Auth failed')
    } finally {
      setLoading(false)
    }
  }

  async function signOut() {
    await sb.auth.signOut()
    router.refresh()
  }

  return (
    <div className="max-w-sm mx-auto mt-16 space-y-6">
      <h1 className="text-2xl font-bold text-center">
        {mode === 'login' ? 'Log in' : 'Create account'}
      </h1>

      <form onSubmit={submit} className="bg-zinc-900 rounded-xl p-6 border border-zinc-800 space-y-4">
        <div>
          <label className="text-zinc-400 text-sm block mb-1">Email</label>
          <input
            type="email" required value={email}
            onChange={e => setEmail(e.target.value)}
            className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 focus:outline-none focus:border-orange-500"
          />
        </div>
        <div>
          <label className="text-zinc-400 text-sm block mb-1">Password</label>
          <input
            type="password" required value={password}
            onChange={e => setPassword(e.target.value)}
            className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-zinc-100 focus:outline-none focus:border-orange-500"
          />
        </div>

        {error && <p className="text-red-400 text-sm">{error}</p>}
        {message && <p className="text-green-400 text-sm">{message}</p>}

        <button
          type="submit" disabled={loading}
          className="w-full bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white rounded-lg py-2 font-medium transition-colors"
        >
          {loading ? '…' : mode === 'login' ? 'Log in' : 'Sign up'}
        </button>
      </form>

      <p className="text-center text-zinc-500 text-sm">
        {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
        <button
          onClick={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError(null); setMessage(null) }}
          className="text-orange-400 hover:underline"
        >
          {mode === 'login' ? 'Sign up' : 'Log in'}
        </button>
      </p>

      <div className="text-center">
        <button onClick={signOut} className="text-zinc-600 text-xs hover:text-zinc-400">
          Sign out of current session
        </button>
      </div>
    </div>
  )
}
