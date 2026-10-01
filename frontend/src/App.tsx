import './App.css'
import { Dashboard } from './pages/Dashboard'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, authSession } from './services/api'

type User = { user_id: string; email: string; role: 'ADMIN' | 'OPERATOR'; building_ids: string[] }

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [authState, setAuthState] = useState<'initializing' | 'anonymous' | 'authenticated' | 'recovery-error'>('initializing')
  const [error, setError] = useState('')
  const [sessionNotice, setSessionNotice] = useState('')
  const hydrationRef = useRef<Promise<User> | null>(null)

  const restoreSession = useCallback(async () => {
    const token = authSession.getToken()
    if (!token) throw new Error('NO_SESSION')
    const current = await api.me()
    await api.getAccessUsers()
    return current
  }, [])

  useEffect(() => {
    let active = true
    const token = authSession.getToken()
    if (!token) { setAuthState('anonymous'); return () => { active = false } }
    setAuthState('initializing')
    hydrationRef.current ??= restoreSession()
    hydrationRef.current.then(current => {
      if (!active) return
      setUser(current); setAuthState('authenticated'); setSessionNotice(''); setError('')
    }).catch(err => {
      if (!active) return
      if (!authSession.getToken() || (err instanceof Error && err.message === 'NO_SESSION')) {
        setUser(null); setAuthState('anonymous'); setSessionNotice('Your session is no longer valid. Sign in again.')
      } else {
        setUser(null); setAuthState('recovery-error')
        setError(err instanceof Error ? err.message : 'Unable to restore the authenticated session.')
      }
    })
    return () => { active = false }
  }, [restoreSession])

  useEffect(() => {
    const expire = (event: Event) => {
      const detail = (event as CustomEvent<{ endpoint?: string }>).detail
      setUser(null); setAuthState('anonymous')
      setSessionNotice(`Your session expired while requesting ${detail?.endpoint ?? 'a protected resource'}. Sign in again.`)
    }
    window.addEventListener("auratwin:session-expired", expire)
    return () => window.removeEventListener("auratwin:session-expired", expire)
  }, [])

  const retryRestore = () => {
    hydrationRef.current = null
    if (!authSession.getToken()) { setAuthState('anonymous'); return }
    setAuthState('initializing'); setError('')
    hydrationRef.current = restoreSession()
    hydrationRef.current.then(current => {
      setUser(current); setAuthState('authenticated'); setSessionNotice('')
    }).catch(err => {
      if (!authSession.getToken()) { setUser(null); setAuthState('anonymous'); setSessionNotice('Your session is no longer valid. Sign in again.') }
      else { setAuthState('recovery-error'); setError(err instanceof Error ? err.message : 'Unable to restore session.') }
    })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    const data = new FormData(event.currentTarget)
    setAuthState('initializing')
    try {
      await api.login(String(data.get('email')), String(data.get('password')))
      hydrationRef.current = restoreSession()
      const current = await hydrationRef.current
      setUser(current); setAuthState('authenticated'); setSessionNotice('')
    } catch (err) {
      hydrationRef.current = null
      if (!authSession.getToken()) { setUser(null); setAuthState('anonymous') }
      else setAuthState('recovery-error')
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }

  if (authState === 'initializing') return <main className="auth-shell">Restoring secure session…</main>
  if (authState === 'recovery-error') return <main className="auth-shell"><section className="auth-card">
    <div className="brand">AuraTwin AI</div><h1>Session check unavailable</h1>
    <p role="alert">{error}</p><p>Protected pages are paused until the session and access list can be verified.</p>
    <button className="btn btn-primary" onClick={retryRestore}>Retry session check</button>
    <button className="btn btn-secondary" onClick={() => { api.logout(); hydrationRef.current = null; setUser(null); setAuthState('anonymous'); setSessionNotice('Signed out.') }}>Sign out</button>
  </section></main>
  if (!user || authState !== 'authenticated') return <main className="auth-shell"><form className="auth-card" onSubmit={submit}>
    <div className="brand">AuraTwin AI</div><h1>Sign in</h1>
    <p>Use the account configured for this development building.</p>
    {sessionNotice && <div className="demo-error" role="status">{sessionNotice}</div>}
    <label>Email<input name="email" type="email" autoComplete="username" required /></label>
    <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
    {error && <div className="demo-error" role="alert">{error}</div>}
    <button className="btn btn-primary" type="submit">Sign in</button>
    <small>Accounts are provisioned from local environment configuration. This demo uses simulated building systems.</small>
  </form></main>
  return <><div className="session-bar"><span>{user.email} · {user.role} · SESSION AUTHENTICATED</span><button onClick={() => { api.logout(); hydrationRef.current = null; setUser(null); setAuthState('anonymous'); setSessionNotice('Signed out.') }}>Sign out</button></div><Dashboard role={user.role} /></>
}

export default App
