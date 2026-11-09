import { useEffect, useRef, useState } from 'react';
import { API_BASE, getJSON } from '../api';

function ReminderControl({ item, timezone, working, mutate }) {
  const formatted = item.reminder_at ? new Date(item.reminder_at * 1000).toLocaleString('sv-SE', { timeZone: timezone }).slice(0, 16).replace(' ', 'T') : '';
  const [when, setWhen] = useState(formatted);
  const [feedback, setFeedback] = useState('');
  const feedbackElement = useRef(null);
  useEffect(() => { if (feedback) feedbackElement.current?.scrollIntoView({ block: 'nearest' }); }, [feedback]);
  if (item.status !== 'confirmed') return null;
  return <form aria-label={`网页提醒：${item.title}`} onSubmit={async event => { event.preventDefault(); setFeedback('');
    const result = await mutate({ action: 'agreement_reminder', record_id: item.id, enabled: !item.notification_enabled, when });
    if (!result.ok) setFeedback(result.error);
  }}>
    <p>网页提醒：{({ delivered: '已发送', failed: '发送未完成，请核对记录', silent: '未产生消息', interrupted: '已中断', reserved: '已尝试，结果待核对',
      expired: '已错过时间，未补发', waiting: '已开启，等待时间', disabled: '未开启' })[item.notification_status] || (item.notification_enabled ? '已开启' : '未开启')}</p>
    <label>网页提醒时间（世界时间）<input type="datetime-local" required={!item.notification_enabled} disabled={working || item.notification_enabled}
      value={when} onChange={event => { setWhen(event.target.value); setFeedback(''); }} /></label>
    <p className="muted">按 {timezone} 和世界时钟触发，仅发送到已连接网页；受勿扰与睡眠设置影响，超过十五个世界分钟不补发。服务需保持运行。</p>
    <button disabled={working}>{item.notification_enabled ? '关闭网页提醒' : '开启网页提醒'}</button>
    {feedback && <p role="alert" ref={feedbackElement}>{feedback}</p>}
  </form>;
}

export default function MemoriesPanel({ session, onClose, onEdit, initialTab = 'history' }) {
  const [tab, setTab] = useState(initialTab);
  const [query, setQuery] = useState('');
  const [day, setDay] = useState('');
  const [items, setItems] = useState([]);
  const [more, setMore] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const [title, setTitle] = useState('');
  const [when, setWhen] = useState('');
  const [location, setLocation] = useState('');
  const experience = session.config?.experience;
  const search = async (append = false) => {
    setWorking(true); setError('');
    try {
      const params = new URLSearchParams({ q: query, day });
      if (append && items.length) params.set('before_id', items.at(-1).id);
      const data = await getJSON(tab === 'memories' ? `${API_BASE}/experience/memories` : `${API_BASE}/history/search?${params}`);
      if (data.epoch !== session.worldEpoch) throw new Error('世界已变化，请重新打开回忆');
      setItems(previous => append ? [...previous, ...data.items] : data.items);
      setMore(data.has_more === true);
    } catch (failure) { setError(failure.message); }
    finally { setWorking(false); }
  };
  useEffect(() => { setItems([]); setSelected(null); setError(''); }, [tab, session.worldEpoch]);
  const mutate = async action => {
    setWorking(true); setError('');
    try { await session.playerAction(action); setSelected(null); return { ok: true }; }
    catch (failure) { setError(failure.message); return { ok: false, error: failure.message }; }
    finally { setWorking(false); }
  };
  const locate = async id => {
    setError('');
    try { await session.locateMessage(id); onClose(); }
    catch (failure) { setError(failure.message); }
  };
  const sourceActions = item => <div className="action-row">
    <button onClick={() => locate(item.message_id)}>查看原始对话</button>
    <button disabled={working} onClick={() => mutate({ action: 'favorite', message_id: item.message_id,
      enabled: !experience?.favorites.some(record => record.message_id === item.message_id) })}>{experience?.favorites.some(record => record.message_id === item.message_id) ? '取消收藏' : '收藏'}</button>
    <button onClick={() => { setSelected(item); setTitle(''); setWhen(''); setLocation(''); }}>记录约定</button>
  </div>;
  return <section className="memories-panel">
    <div className="record-tabs" role="group" aria-label="回忆分类">{[['history', '搜索对话'], ['favorites', '我的收藏'], ['agreements', '约定'], ['activities', '活动回顾'], ['memories', '角色记忆']].map(([key, label]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    {tab === 'history' && <form className="record-search" onSubmit={event => { event.preventDefault(); search(); }}>
      <label>搜索对白<input value={query} maxLength={200} onChange={event => setQuery(event.target.value)} /></label>
      <label>世界日期<input type="date" value={day} onChange={event => setDay(event.target.value)} /></label>
      <button disabled={working}>{working ? '正在查询…' : '搜索对话'}</button>
    </form>}
    {selected && <form className="record-card" onSubmit={event => { event.preventDefault(); mutate({ action: 'agreement', message_id: selected.message_id, title, when, location }); }}>
      <h3>从这条对话记录约定</h3><blockquote>{selected.content}</blockquote>
      <p className="muted">初始为待确认，由你核对。留空的日期和地点保持未知，不写入角色长期记忆。</p>
      <label>约定内容<input required value={title} maxLength={300} onChange={event => setTitle(event.target.value)} /></label>
      <label>时间（可留空）<input value={when} maxLength={100} onChange={event => setWhen(event.target.value)} /></label>
      <label>地点（可留空）<input value={location} maxLength={150} onChange={event => setLocation(event.target.value)} /></label>
      <div className="action-row"><button disabled={working}>保存待确认约定</button><button type="button" onClick={() => setSelected(null)}>取消</button></div>
    </form>}
    {tab === 'history' && <>{items.map(item => <article className="record-card" key={item.message_id}><small>{item.role === 'ai' ? session.config?.display_name : '你'} · {item.timestamp ? new Date(item.timestamp).toLocaleString('zh-CN', { timeZone: session.config?.timezone || 'Asia/Shanghai' }) : '时间未知'}</small><p className="preserve-text">{item.content}</p>{sourceActions(item)}</article>)}{more && <button disabled={working} onClick={() => search(true)}>加载更多搜索结果</button>}</>}
    {tab === 'favorites' && <><p className="muted">由你主动收藏，不会改写角色记忆。</p>{experience?.favorites.map(item => <article className="record-card" key={item.message_id}><p>{item.content}</p>{sourceActions(item)}</article>)}{!experience?.favorites.length && <p>还没有收藏。可从对白或搜索结果收藏。</p>}</>}
    {tab === 'agreements' && <><p className="muted">这些是你整理的记录。核对确认后，角色可在后续对话中参考；不代表对方已承诺或事情已完成，需要协商时继续聊天。</p>{experience?.agreements.map(item => <article className="record-card" key={item.id}><h3>{item.title}</h3><p>{({ pending: '待确认', confirmed: '由你确认', completed: '已完成', cancelled: '已取消' })[item.status]}</p><p>{item.when || '时间未知'} · {item.location || '地点未知'}</p><ReminderControl key={`${item.id}:${item.notification_enabled}`} item={item} timezone={session.config?.timezone || 'Asia/Shanghai'} working={working} mutate={mutate} /><blockquote>{item.source.content}</blockquote><button onClick={() => locate(item.source.message_id)}>查看原始对话</button><div className="action-row">{[['pending', '设为待确认'], ['confirmed', '我已核对确认'], ['completed', '标记完成'], ['cancelled', '取消约定']].filter(([key]) => item.status !== key).map(([key, label]) => <button key={key} disabled={working} onClick={() => mutate({ action: 'agreement_status', record_id: item.id, status: key })}>{label}</button>)}</div></article>)}{!experience?.agreements.length && <p>还没有约定。先找到对话来源，再记录。</p>}</>}
    {tab === 'activities' && <><h3>她的近况 · 虚构世界</h3><p className="muted">角色自己的活动记录，不代表你参与或现实活动完成。</p>{session.config?.state?.role_continuity?.events?.filter(item => item.timeline_status !== 'future_snapshot').slice().reverse().map(item => <article className="record-card" key={item.id}><p>{item.description}</p><small>{new Date(item.at * 1000).toLocaleString('zh-CN', { timeZone: session.config?.timezone || 'Asia/Shanghai' })} · {item.origin === 'offline_simulation' ? '离线规则推演' : '角色世界记录'}</small></article>)}{!session.config?.state?.role_continuity?.events?.length && <p>还没有她自己的活动记录。</p>}<h3>你的自习记录</h3>{experience?.activities.slice().reverse().map(item => <article className="record-card" key={item.id}><h3>自习 · {({ running: '进行中', paused: '已暂停', completed: '已完成', interrupted: '提前结束' })[item.status]}</h3><p>{Math.floor(item.elapsed_seconds / 60)} 分钟 · {item.shared ? '由你确认的共同活动' : '个人计时'}</p><p>开始时的世界时间：{item.world_time}</p><p>{item.summary || '没有填写回顾'}</p>{item.source && <button onClick={() => locate(item.source.message_id)}>查看确认来源</button>}</article>)}{!experience?.activities.length && <p>还没有活动记录。可以在专注陪伴中开始自习。</p>}</>}
    {tab === 'memories' && <><p className="muted">由模型整理的角色记忆，可能含推演或错误；没有来源时不冒充已核对的共同经历。</p><button disabled={working} onClick={() => search()}>{working ? '正在读取…' : '读取角色记忆'}</button>{items.map(item => <article className="record-card" key={item.id}><small>{item.time} · 模型整理</small><p>{item.content}</p>{item.insight && <p className="muted">{item.insight}</p>}<button onClick={() => onEdit(`你关于“${item.content.slice(0, 300)}”的记忆可能不准确，我想核对一下来源并更正：`)}>这条不准确 · 在对话中核对</button></article>)}</>}
  </section>;
}
