import { useEffect, useRef, useState } from 'react';
import './ArchiveModal.css';
import { API_BASE, getJSON } from './api';

const endpoint = API_BASE + '/api/archive';
const terminal = task => task && task.status !== 'running';
export default function ArchiveModal({ isOpen, onClose, session }) {
  const dialog = useRef(null), completed = useRef(null);
  const [archives, setArchives] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [task, setTask] = useState(session?.config?.archive_task || null);
  const [pending, setPending] = useState(false);
  const [preview, setPreview] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const busy = pending || task?.status === 'running';
  const taskId = task?.task_id, taskStatus = task?.status;
  const loadList = async () => {
    setLoading(true);
    try { const data = await getJSON(endpoint + '/list'); if (!data.success) throw new Error(data.error || '读取失败'); setArchives(data.archives); }
    catch (failure) { setError(failure.message); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    if (!isOpen) return;
    const previous = document.activeElement, element = dialog.current;
    element.showModal(); loadList();
    return () => { element.close(); previous?.focus(); };
  }, [isOpen]);
  const adoptTask = incoming => setTask(current => !incoming || (current?.revision || 0) > (incoming.revision || 0)
    || (current?.task_id === incoming.task_id && terminal(current) && !terminal(incoming)) ? current : incoming);
  useEffect(() => { adoptTask(session?.config?.archive_task); }, [session?.config?.archive_task]);
  useEffect(() => {
    if (!taskId || taskStatus !== 'running') return;
    let disposed = false, timer;
    const poll = async () => {
      try { const result = await getJSON(endpoint + '/tasks/current'); if (!disposed) adoptTask(result.task); }
      catch (failure) { if (!disposed) setError('任务状态暂不可读：' + failure.message + '。恢复连接后继续核对。'); }
      if (!disposed) timer = setTimeout(poll, 1000);
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [taskId, taskStatus]);
  useEffect(() => {
    if (!terminal(task) || completed.current === task.task_id) return;
    completed.current = task.task_id;
    if (task.status === 'failed') setError(task.result?.error || task.phase);
    else {
      setError(''); setPreview(null); setName(''); setDescription(''); loadList();
      if (['load', 'quick-load'].includes(task.operation)) session?.refreshWorld();
    }
  }, [task, session]);
  const start = async (operation, slot_name = '') => {
    setPending(true); setError('');
    try {
      const result = await getJSON(endpoint + '/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation, slot_name, description, epoch: session.worldEpoch }) });
      adoptTask(result);
    } catch (failure) {
      setError(failure.message);
      try { const result = await getJSON(endpoint + '/tasks/current'); adoptTask(result.task); } catch { /* Preserve the error; a timeout is not proof of failure. */ }
    } finally { setPending(false); }
  };
  const inspect = async slot => {
    setPending(true); setError('');
    try {
      const result = await getJSON(endpoint + '/' + encodeURIComponent(slot) + '/preview');
      if (!result.success) throw new Error(result.error || '预览失败');
      setPreview({ slot, ...result.manifest });
    } catch (failure) { setError(failure.message); }
    finally { setPending(false); }
  };
  const remove = async () => {
    setPending(true); setError('');
    try {
      const result = await getJSON(endpoint + '/' + encodeURIComponent(deleting), { method: 'DELETE' });
      if (!result.success) throw new Error(result.error || '删除失败');
      setDeleting(null); await loadList();
    } catch (failure) { setError(failure.message); }
    finally { setPending(false); }
  };
  if (!isOpen) return null;
  return <dialog ref={dialog} aria-label="存档管理" className="archive-modal-overlay" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="archive-modal"><header className="archive-modal-header"><h2>存档管理</h2><button aria-label="关闭存档管理" disabled={busy} onClick={onClose}>关闭</button></header>
      {error && <p className="archive-error" role="alert">{error}</p>}
      {task && <div className="archive-progress" role="status" aria-live="polite"><strong>{({ running: '正在处理', completed: '已完成', failed: '未完成' })[task.status]}</strong><span>{task.phase}</span><small>任务 {task.task_id.slice(0, 8)}{task.status === 'running' ? ' · 当前步骤不可安全取消，请等待' : ''}</small>
        {task.recovery_required && <p className="inline-error">恢复未完成，世界已暂停。请选择已知有效的存档重试；保留自动备份。</p>}</div>}
      {!session?.config?.capabilities?.archive_tasks ? <p>请同时更新并重启后端，以启用安全存读档。</p> : <>
        <div className="archive-footer"><button disabled={busy} onClick={() => start('quick-save')}>快速存档</button><button disabled={busy} onClick={() => inspect('quick_save')}>快速读档 · 先预览</button></div>
        {preview && <section className="record-card" aria-label="加载预览"><h3>加载 {preview.slot}</h3><dl className="fact-list"><dt>角色</dt><dd>{preview.character_name}</dd><dt>世界时间</dt><dd>{preview.logical_time}</dd><dt>角色位置</dt><dd>{preview.extra?.scene?.character_location || '旧存档未记录'}</dd><dt>消息数</dt><dd>{preview.stats?.message_count || 0}</dd></dl><p>{preview.description}</p><p>{session?.config?.demo_mode ? '展示快照仅备份和恢复展示目录内的合成 JSON 数据，不连接数据库，不兼容生产 .ember 存档。' : '先等待当前任务结束并备份现有进度，再恢复数据库、场景、计时和活动记录。自习暂停等待确认；旧声音和事件失效。'}</p><div className="action-row"><button disabled={busy} onClick={() => start('load', preview.slot)}>备份当前进度并加载</button><button disabled={busy} onClick={() => setPreview(null)}>取消加载</button></div></section>}
        {deleting && <section className="record-card" aria-label="确认删除"><h3>删除存档 {deleting}？</h3><p>仅删除此存档文件，无法撤销。不会删除当前聊天数据库。</p><div className="action-row"><button disabled={busy} onClick={remove}>确认删除此存档</button><button disabled={busy} onClick={() => setDeleting(null)}>保留存档</button></div></section>}
        <form className="archive-create-form" onSubmit={event => { event.preventDefault(); if (archives.some(item => item.slot_name === name.trim())) { setError('同名存档已存在，请使用新名称'); return; } start('create', name.trim()); }}>
          <h3>新建存档</h3><label>存档名称<input required maxLength={40} value={name} onChange={event => setName(event.target.value)} /></label><label>简短回顾<input maxLength={300} value={description} onChange={event => setDescription(event.target.value)} /></label><button disabled={busy || !name.trim()}>创建新存档</button>
        </form>
      </>}
      <section className="archive-list"><h3>已保存的进度</h3><button disabled={busy || loading} onClick={loadList}>刷新存档列表</button>{loading && <p role="status">正在读取存档…</p>}{!loading && !archives.length && <p>暂无存档。</p>}
        {['快速存档', '手动存档', '自动备份'].map(group => {
          const slots = archives.filter(item => (item.slot_name === 'quick_save' ? '快速存档' : item.slot_name.startsWith('auto_backup') ? '自动备份' : '手动存档') === group);
          return slots.length ? <div key={group}><h4>{group}</h4><div className="archive-gallery">{slots.map(item => <article className="archive-card" key={item.slot_name}><h4>{item.slot_name}</h4><p>{item.description || '没有回顾'}</p><p>{item.logical_time || item.created_at}</p><small>{Math.round((item.file_size || 0) / 1024)} KB</small>{!item.is_valid && <p className="inline-error">{item.error_message || '存档无效'}</p>}<div className="action-row"><button disabled={busy || !item.is_valid} onClick={() => inspect(item.slot_name)}>预览并加载</button><button disabled={busy} onClick={() => setDeleting(item.slot_name)}>删除</button></div></article>)}</div></div> : null;
        })}
      </section>
    </div>
  </dialog>;
}
