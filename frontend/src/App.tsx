import './App.css'
import { Dashboard } from './pages/Dashboard'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, authSession } from './services/api'

type User = { user_id: string; email: string; role: 'ADMIN' | 'OPERATOR'; building_ids: string[] }

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(Boolean(authSession.getToken()))
  const [error, setError] = useState('')
  useEffect(() => {
    if (!authSession.getToken()) return
    api.me().then(setUser).catch(() => authSession.clear()).finally(() => setLoading(false))
  }, [])
  useEffect(() => {
    const expire = () => setUser(null)
    window.addEventListener("auratwin:session-expired", expire)
    return () => window.removeEventListener("auratwin:session-expired", expire)
  }, [])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    const data = new FormData(event.currentTarget)
    try { setUser(await api.login(String(data.get('email')), String(data.get('password')))) }
    catch (err) { setError(err instanceof Error ? err.message : 'Login failed') }
  }
  if (loading) return <main className="auth-shell">Loading secure session…</main>
  if (!user) return <main className="auth-shell"><form className="auth-card" onSubmit={submit}>
    <div className="brand">AuraTwin AI</div><h1>Sign in</h1>
    <p>Use the account configured for this development building.</p>
    <label>Email<input name="email" type="email" autoComplete="username" required /></label>
    <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
    {error && <div className="demo-error" role="alert">{error}</div>}
    <button className="btn btn-primary" type="submit">Sign in</button>
    <small>Accounts are provisioned from local environment configuration. This demo uses simulated building systems.</small>
  </form></main>
  return <><div className="session-bar"><span>{user.email} · {user.role}</span><button onClick={() => { api.logout(); setUser(null) }}>Sign out</button></div><Dashboard role={user.role} /></>
}

export default App
