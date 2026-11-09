import { useEffect, useState } from 'react';
import { API_BASE, getJSON } from './api';
import { setAuthSession } from './authSession';
import App from './App';
import './AuthGate.css';

export default function AuthGate() {
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [accessCode, setAccessCode] = useState('');
  const [label, setLabel] = useState(/Mobile|Android/i.test(navigator.userAgent) ? '我的手机' : '我的电脑');
  const [working, setWorking] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    const refresh = async () => {
      try {
        // 404 supports older/local synthetic backends without the hosted feature.
        const response = await fetch(`${API_BASE}/auth/session`, { credentials: 'include', signal: abort.signal });
        const result = response.status === 404 ? { required: false } : await response.json();
        if (!response.ok && response.status !== 404) throw new Error(result.detail || '无法连接服务');
        if (!abort.signal.aborted) { setAuthSession(result); setSession(result); setError(''); }
      } catch (failure) { if (!abort.signal.aborted) setError(failure.message || '无法连接服务'); }
    };
    refresh();
    const lost = () => { setAuthSession({ required: true, authenticated: false }); setSession({ required: true, authenticated: false }); };
    window.addEventListener('ember-auth-lost', lost);
    return () => { abort.abort(); window.removeEventListener('ember-auth-lost', lost); };
  }, [retry]);
  if (session && (!session.required || session.authenticated)) return <App />;
  return <main className="connection-screen"><section className="connection-card">
    <span className="connection-brand">Ember</span><h1>连接依鸣</h1>
    {!session ? <><p>{error || '正在连接你的陪伴空间…'}</p>{error && <button onClick={() => setRetry(value => value + 1)}>重新连接</button>}</> : <>
      <p>输入服务访问码，让这台设备接续同一个世界。</p>
      {error && <p role="alert">{error}</p>}
      <form onSubmit={async event => {
        event.preventDefault(); if (working) return;
        setWorking(true); setError('');
        try {
          const result = await getJSON(`${API_BASE}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ access_code: accessCode, label }) });
          setAuthSession(result); setSession(result); setAccessCode('');
        } catch (failure) { setError(failure.message); }
        finally { setWorking(false); }
      }}>
        <label>设备名称<input value={label} onChange={event => setLabel(event.target.value)} maxLength={80} required autoComplete="off" /></label>
        <label>访问码<input type="password" value={accessCode} onChange={event => setAccessCode(event.target.value)} maxLength={256} required autoComplete="current-password" /></label>
        <button disabled={working || !accessCode}>{working ? '正在连接…' : '连接这个世界'}</button>
      </form>
    </>}
  </section></main>;
}
