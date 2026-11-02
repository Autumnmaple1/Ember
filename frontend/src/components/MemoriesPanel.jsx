import { useEffect, useState } from 'react';
import { API_BASE, getJSON } from '../api';

export default function MemoriesPanel({ session, onClose, onEdit }) {
  const [tab, setTab] = useState('history');
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
    try { await session.playerAction(action); setSelected(null); }
    catch (failure) { setError(failure.message); }
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
    {tab === 'agreements' && <><p className="muted">这些是你整理的记录，确认不等于角色已知晓；需要告知时请在对话中说明。</p>{experience?.agreements.map(item => <article className="record-card" key={item.id}><h3>{item.title}</h3><p>{({ pending: '待确认', confirmed: '由你确认', completed: '已完成', cancelled: '已取消' })[item.status]}</p><p>{item.when || '时间未知'} · {item.location || '地点未知'}</p><blockquote>{item.source.content}</blockquote><button onClick={() => locate(item.source.message_id)}>查看原始对话</button><div className="action-row">{[['pending', '设为待确认'], ['confirmed', '我已核对确认'], ['completed', '标记完成'], ['cancelled', '取消约定']].filter(([key]) => item.status !== key).map(([key, label]) => <button key={key} disabled={working} onClick={() => mutate({ action: 'agreement_status', record_id: item.id, status: key })}>{label}</button>)}</div></article>)}{!experience?.agreements.length && <p>还没有约定。先找到对话来源，再记录。</p>}</>}
    {tab === 'activities' && <>{experience?.activities.slice().reverse().map(item => <article className="record-card" key={item.id}><h3>自习 · {({ running: '进行中', paused: '已暂停', completed: '已完成', interrupted: '提前结束' })[item.status]}</h3><p>{Math.floor(item.elapsed_seconds / 60)} 分钟 · {item.shared ? '由你确认的共同活动' : '个人计时'}</p><p>开始时的世界时间：{item.world_time}</p><p>{item.summary || '没有填写回顾'}</p>{item.source && <button onClick={() => locate(item.source.message_id)}>查看确认来源</button>}</article>)}{!experience?.activities.length && <p>还没有活动记录。可以在专注陪伴中开始自习。</p>}</>}
    {tab === 'memories' && <><p className="muted">由模型整理的角色记忆，可能含推演或错误；没有来源时不冒充已核对的共同经历。</p><button disabled={working} onClick={() => search()}>{working ? '正在读取…' : '读取角色记忆'}</button>{items.map(item => <article className="record-card" key={item.id}><small>{item.time} · 模型整理</small><p>{item.content}</p>{item.insight && <p className="muted">{item.insight}</p>}<button onClick={() => onEdit(`你关于“${item.content.slice(0, 300)}”的记忆可能不准确，我想核对一下来源并更正：`)}>这条不准确 · 在对话中核对</button></article>)}</>}
  </section>;
}
