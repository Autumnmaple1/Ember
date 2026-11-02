import { useState } from 'react';
import { API_BASE, getJSON } from '../api';

export default function TimeSettings({ session }) {
  const current = session.config?.time_accel_factor || 1;
  const [draft, setDraft] = useState(current);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const apply = async () => {
    setWorking(true); setError('');
    try {
      const result = await getJSON(`${API_BASE}/config/time_accel`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ factor: Number(draft) }) });
      setDraft(result.time_accel_factor);
      await session.refreshWorld();
    } catch (failure) { setDraft(current); setError(`时间倍率未修改：${failure.message}`); }
    finally { setWorking(false); }
  };
  return <section className="settings-section"><h3>时间与后台运行</h3>
    <p>世界时间：{session.config?.logical_time}（{session.config?.timezone || 'Asia/Shanghai'}）</p>
    <p className="muted">正常启动跟随现实时间；读档恢复存档时间。自习计时始终使用现实时间。</p>
    <label>世界时间倍率 · 已确认 {current}x<input aria-label="世界时间倍率" type="range" min="0.5" max="20" step="0.5" value={draft} onChange={event => setDraft(Number(event.target.value))} /></label>
    <p>预览 {draft}x</p><button disabled={working || draft === current || session.busy || session.config?.status?.paused} onClick={apply}>{working ? '正在应用…' : '应用时间倍率'}</button>
    {session.config?.capabilities?.player_records && <div className="action-row"><button disabled={working || session.busy} onClick={async () => {
      setWorking(true); setError('');
      try { await session.playerAction({ action: 'world_pause', enabled: !session.config?.world_paused }); }
      catch (failure) { setError(failure.message); } finally { setWorking(false); }
    }}>{session.config?.world_paused ? '继续世界' : '暂停世界'}</button><span>{session.config?.world_paused ? '逻辑时钟与自主演化已冻结；恢复后可继续对话' : '世界继续运行；关闭声音不会暂停世界'}</span></div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}
