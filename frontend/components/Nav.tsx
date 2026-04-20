'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { createClient } from '@/lib/supabase'

const links = [
  { href: '/briefing', label: 'Briefing' },
  { href: '/team',     label: 'My Team' },
  { href: '/history',  label: 'History' },
  { href: '/riders',   label: 'Riders' },
  { href: '/stages',   label: 'Stages' },
]

export default function Nav() {
  const path = usePathname()
  const [user, setUser] = useState<{ email?: string | null } | null>(null)

  useEffect(() => {
    const sb = createClient()
    sb.auth.getUser().then(({ data }) => setUser(data.user))
  }, [])

  const handleSignOut = async () => {
    const sb = createClient()
    await sb.auth.signOut()
    window.location.href = '/auth'
  }

  return (
    <nav className="border-b border-zinc-800 bg-zinc-900 sticky top-0 z-50">
      <div className="max-w-5xl mx-auto px-4 flex items-center gap-1 h-12">
        <Link href="/briefing" className="font-bold text-orange-400 mr-4 text-sm tracking-wide">
          HOLDET
        </Link>
        {links.map(l => (
          <Link
            key={l.href}
            href={l.href}
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              path.startsWith(l.href)
                ? 'bg-zinc-700 text-white'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
            }`}
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {user ? (
            <>
              <span className="text-xs text-zinc-400">
                {(user.email ?? '').slice(0, 20)}
              </span>
              <button
                onClick={handleSignOut}
                className="text-xs text-zinc-500 hover:text-zinc-300"
              >
                Sign out
              </button>
            </>
          ) : (
            <Link href="/auth" className="text-xs text-zinc-500 hover:text-zinc-300">
              Sign in
            </Link>
          )}
        </div>
      </div>
    </nav>
  )
}
