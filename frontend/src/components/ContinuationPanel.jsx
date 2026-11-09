import { useState } from 'react';

export default function ContinuationPanel({ session, source, onPinned, onNavigate, onEdit, onLocate }) {
  const [title, setTitle] = useState('');
  const [quote, setQuote] = useState(source?.content.slice(0, 6000) || '');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const materials = session.config?.experience?.materials || [];
  const focused = materials.find(item => item.status === 'focused');
  const stamp = focused?.source.timestamp;
  const sourceDate = typeof stamp === 'number' && Number.isFinite(stamp) ? new Date(stamp) : null;
  const sourceTime = sourceDate && Number.isFinite(sourceDate.getTime())
    ? sourceDate.toLocaleString('zh-CN', { timeZone: session.config?.timezone || 'Asia/Shanghai', hour12: false }) : '时间未记录';
  const blocked = working || session.busy || session.syncingWorld || session.connection !== 'online' || session.config?.status?.paused;
  const act = async action => {
    if (working) return;
    setWorking(true); setError('');
    try { await session.playerAction(action); if (action.action === 'material_pin') onPinned(); }
    catch (failure) { setError(failure.message); }
    finally { setWorking(false); }
  };
  return <section className="continuation-panel" aria-label="共同材料与接续">
    <p className="muted">上次留下的材料、手册和安排，都在这里。可以接着看，也可以先收起来。</p>
    {error && <p role="alert" className="inline-error">{error}</p>}
    {source && <form className="record-card" onSubmit={event => { event.preventDefault(); act({ action: 'material_pin', message_id: source.message_id, title, quote }); }}>
      <h3>一起看这段</h3><p>从这条已保存的用户消息中选取原文。可以缩小范围，不能补写原消息中没有的内容。</p>
      <label>材料标题<input maxLength={100} value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：证明的第二段" /></label>
      <label>共同查看的原文<textarea rows={7} maxLength={6000} value={quote} onChange={event => setQuote(event.target.value)} /></label>
      <p className="muted">最多 6000 字。这里保存的是文字，不表示角色能看到你未提供的屏幕、书页或附件。</p>
      <button className="primary-button" disabled={blocked || !quote.trim() || !source.content.includes(quote)}>确认一起看这段</button>
    </form>}
    {focused ? <article className="record-card"><h3>{focused.title}</h3><small>一起看的原文 · 来源对话：{sourceTime}</small>
      <blockquote className="shared-material-text">{focused.quote}</blockquote>
      <div className="action-row"><button onClick={() => onEdit(`我们接着看“${focused.title}”。我想讨论这段原文：`)}>围绕这段继续聊</button>
        <button onClick={() => onLocate(focused.source.message_id)}>查看材料来源</button>
        <button disabled={blocked} onClick={() => act({ action: 'material_put_away', record_id: focused.id })}>先收起这段</button></div>
    </article> : <p>目前没有选定共同材料。把题目、代码或文字发到聊天里，再点击那条消息下的“一起看这段”。</p>}
    {(session.config?.continuation || []).filter(item => item.kind !== 'material').map(item => <article className="record-card" key={`${item.kind}:${item.id}`}>
      <h3>{item.title}</h3><p>{item.detail}</p><button onClick={() => onNavigate(item.kind)}>打开已有记录</button>
    </article>)}
    {materials.some(item => item.status === 'stored') && <details><summary>之前一起看过的材料</summary>
      {materials.filter(item => item.status === 'stored').slice().reverse().map(item => <article className="record-card" key={item.id}>
        <h3>{item.title}</h3><p>{item.quote.slice(0, 180)}</p><button disabled={blocked} onClick={() => act({ action: 'material_focus', record_id: item.id })}>重新一起看这段</button>
      </article>)}
    </details>}
  </section>;
}
