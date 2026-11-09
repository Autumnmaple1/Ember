export const API_BASE = (import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const resolvedBase = new URL(API_BASE || '/', globalThis.location?.origin || 'http://localhost:8000');
export const WS_URL = `${resolvedBase.origin.replace(/^http/, 'ws')}${resolvedBase.pathname.replace(/\/$/, '')}/ws/chat`;

export async function getJSON(url, options = {}) {
  const { timeout = 10000, signal, ...request } = options;
  const abort = new AbortController();
  let timedOut = false;
  const cancel = () => abort.abort();
  if (signal?.aborted) cancel();
  else signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; abort.abort(); }, timeout);
  try {
    const headers = new Headers(request.headers);
    if (authSession().csrf && !['GET', 'HEAD'].includes((request.method || 'GET').toUpperCase())) headers.set('X-Ember-CSRF', authSession().csrf);
    const response = await fetch(url, { credentials: 'include', ...request, headers, signal: abort.signal });
    let data;
    try { data = await response.json(); }
    catch (error) { if (response.ok) throw error; }
    if (!response.ok) {
      if (response.status === 401 && data?.code === 'authentication_required') authenticationLost();
      const detail = Array.isArray(data?.detail) ? data.detail.slice(0, 3).map(item => item.msg).join('；') : data?.detail;
      throw new Error(typeof detail === 'string' ? detail : `请求失败 (${response.status})`);
    }
    return data;
  } catch (error) {
    if (timedOut) throw new Error('请求超时，请重试。');
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}
import { authSession, authenticationLost } from './authSession.js';
