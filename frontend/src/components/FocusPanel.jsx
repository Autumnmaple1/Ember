import { useEffect, useState } from 'react';

const clock = seconds => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
export default function FocusPanel({ session }) {
  const [duration, setDuration] = useState(25);
  const [shared, setShared] = useState(false);
  const [source, setSource] = useState('');
  const [summary, setSummary] = useState('');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(0);
  const experience = session.config?.experience;
  const active = experience?.activities.find(item => ['running', 'paused'].includes(item.status));
  useEffect(() => {
    const begin = performance.now();
    const tick = () => setSeconds((active?.elapsed_seconds || 0) + (active?.status === 'running' ? (performance.now() - begin) / 1000 : 0));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [active]);
  const act = async action => {
    setWorking(true); setError('');
    try { await session.playerAction(action); if (['start', 'complete', 'interrupt'].includes(action.action)) setSummary(''); }
    catch (failure) { setError(failure.message); }
    finally { setWorking(false); }
  };
  const sources = session.messages.filter(item => item.role === 'ai' && (item.history_id || item.delivery === 'persisted') && item.completion_status === 'completed' && !item.streaming);
  return <section className="focus-panel" aria-label="专注自习">
    <div><span className="eyebrow">A LITTLE TIME TOGETHER</span><h2>专注自习</h2>
      <p className="muted">按现实时间计时，世界加速不会改变它。切换视图不会开始或结束活动。</p></div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    {!experience ? <p>需要配套后端支持活动记录。</p> : active ? <>
      <p>{active.shared ? '共同自习 · 由你确认' : '个人计时 · 未确认共同活动'} · {active.status === 'paused' ? '已暂停' : '进行中'}</p>
      <output className="focus-clock" aria-label="已专注时间" aria-live="off">{clock(seconds)}</output>
      <p>计划 {active.target_seconds / 60} 分钟{seconds >= active.target_seconds ? ' · 已到计划时间，可以自行决定结束或继续' : ''}</p>
      <label>可选回顾<textarea value={summary} onChange={event => setSummary(event.target.value)} maxLength={2000} placeholder="记录你实际做了什么；留空也可以" /></label>
      <div className="action-row">
        <button disabled={working} onClick={() => act({ action: active.status === 'running' ? 'pause' : 'resume' })}>{active.status === 'running' ? '暂停计时' : '继续计时'}</button>
        <button disabled={working} onClick={() => act({ action: 'complete', summary })}>完成自习</button>
        <button disabled={working} onClick={() => act({ action: 'interrupt', summary })}>提前结束</button>
      </div>
      {active.pause_reason && active.status === 'paused' && <p className="muted">{active.pause_reason}</p>}
    </> : <form onSubmit={event => { event.preventDefault(); act({ action: 'start', duration: Number(duration), shared, message_id: shared ? source : undefined }); }}>
      <label>计划分钟<input type="number" min="1" max="240" value={duration} onChange={event => setDuration(event.target.value)} /></label>
      <label className="setting-row"><span>我已在对话中确认双方同意一起自习</span><input type="checkbox" checked={shared} onChange={event => setShared(event.target.checked)} /></label>
      {shared && <label>确认对话来源<select value={source} required onChange={event => setSource(event.target.value)}><option value="">选择已保存的角色回复</option>{sources.map(item => <option key={item.message_id} value={item.message_id}>{item.content.slice(0, 70)}</option>)}</select></label>}
      <button className="primary-button" disabled={working || session.syncingWorld || session.config?.status?.paused}>{working ? '正在开始…' : '开始自习计时'}</button>
    </form>}
    {experience && <label className="setting-row"><span>勿扰 · 减少主动发言</span><input type="checkbox" checked={experience.quiet} disabled={working} onChange={event => act({ action: 'quiet', enabled: event.target.checked })} /></label>}
    <p className="muted">勿扰只关闭角色主动说话；你的输入仍会正常回复，世界继续演化。</p>
    {experience?.activities.filter(item => ['completed', 'interrupted'].includes(item.status)).slice(-3).reverse().map(item => <article className="record-card" key={item.id}><strong>{item.status === 'completed' ? '自习已完成' : '自习提前结束'}</strong><p>{clock(item.elapsed_seconds)} · {item.shared ? '由你确认的共同活动' : '个人计时'}</p><p>{item.summary || '没有填写回顾'}</p></article>)}
  </section>;
}
