import { useEffect, useRef, useState } from 'react';
import { readPreference, writePreference } from '../preferences';
import './SharedProjectPanel.css';

const phases = ['route', 'investigate', 'compose', 'finished'];
const steps = ['选一份材料', '面对变化', '整理这一页', '留下成果'];

function PageEditor({ project, epoch, working, onSave }) {
  const key = `ember.projectDraft.${epoch}.${project.id}.${project.version}`;
  const [draft, setDraft] = useState(() => {
    const saved = readPreference(key, null);
    return saved && typeof saved.title === 'string' && typeof saved.note === 'string'
      ? saved : { title: project.title, note: project.note };
  });
  useEffect(() => { writePreference(key, draft); }, [key, draft]);
  return <form className="project-editor" onSubmit={event => {
    event.preventDefault(); onSave({ action: project.status === 'completed' ? 'project_edit' : 'project_finish', ...draft });
  }}>
    <label>这一页的标题<input required maxLength={100} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
    <label>你的页边注（可留空）<textarea rows={4} maxLength={1000} value={draft.note} onChange={event => setDraft({ ...draft, note: event.target.value })} placeholder="哪一处改变了你的判断？下次还想知道什么？" /></label>
    <p className="muted">页边注由你撰写。尚未提交的文字保存在本浏览器，按世界与手册版本区分。</p>
    <button className="primary-button" disabled={working || !draft.title.trim()}>{project.status === 'completed' ? '保存页边注' : '留下这一页'}</button>
  </form>;
}

export default function SharedProjectPanel({ session, onEdit }) {
  const [selected, setSelected] = useState('');
  const [source, setSource] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const inFlight = useRef(false);
  const projects = session.config?.experience?.projects || [];
  const active = projects.find(item => ['active', 'paused'].includes(item.status));
  const project = projects.find(item => item.id === selected) || active || projects.at(-1);
  const intro = session.config?.shared_project;
  const blocked = working || session.busy || session.syncingWorld || session.connection !== 'online'
    || session.config?.world_paused || session.config?.status?.paused;
  const sources = session.messages.filter(item => item.role === 'ai' && (item.history_id || item.delivery === 'persisted') && item.completion_status === 'completed' && !item.streaming);
  const act = async (action, retrying = false) => {
    if (inFlight.current) return;
    const command = retrying ? action : { ...action, request_id: crypto.randomUUID(),
      ...(action.action === 'project_start' ? {} : { record_id: project.id, expected_version: project.version }) };
    inFlight.current = true; setWorking(true); setError(''); setRetry(null);
    try {
      await session.playerAction(command);
      setConfirmEnd(false);
      if (command.action === 'project_start') { setSelected(''); setConfirmed(false); setSource(''); }
    } catch (failure) {
      setError(failure.message); setRetry(command);
    } finally { inFlight.current = false; setWorking(false); }
  };
  const discuss = () => onEdit(project ? `我们一起看看“${project.title}”这一页吧。现在到“${project.view.heading}”这一步了。你对现有材料有什么想法？先讨论，不替我在界面里选择。`
    : '要不要一起试做一页校园观察手册？我们先用虚构资料做桌面推演，不用实际出门。可以一起讨论材料、做取舍，最后留下一页作品。你愿意吗？');
  return <section className="shared-project" aria-label="共同手册">
    <div className={`project-cover${project ? ' project-cover-open' : ''}`}>
      <span className="eyebrow">A PAGE WE MAKE TOGETHER</span>
      <h3>{project?.title || intro?.title || '窗边的一页'}</h3>
      <p>{intro?.subtitle || '共同观察手册 · 试作篇'}</p>
      <span className="project-leaf" aria-hidden="true">⌁</span>
    </div>
    <p className="project-boundary">{intro?.boundary || '这是虚构资料推演，不代表现实外出或同处一地。'}</p>
    {!session.config?.capabilities?.shared_projects ? <p>请更新并重启后端，以使用共同手册。</p> : <>
      {error && <div role="alert" className="inline-error"><p>{error}</p><p>若网络中断，操作可能已保存；先同步即可核对进度。</p><div className="action-row">
        <button disabled={working} onClick={async () => { await session.refreshWorld(); setRetry(null); }}>同步手册</button>
        {retry && <button disabled={blocked} onClick={() => act(retry, true)}>重试原操作</button>}
      </div></div>}
      <div className="project-discuss action-row"><button onClick={discuss}>和{session.config?.display_name || '依鸣'}商量</button><small>填入对话草稿，由你发送</small></div>
      {project && <>
        {projects.length > 1 && <label>翻到哪一页<select value={project.id} onChange={event => { setSelected(event.target.value); setConfirmEnd(false); }}>{projects.slice().reverse().map(item => <option key={item.id} value={item.id}>{item.title} · {item.status === 'completed' ? '已留下' : item.status === 'abandoned' ? '未完成的片段' : '待继续'}</option>)}</select></label>}
        <ol className="project-steps" aria-label="手册进度">{steps.map((step, index) => <li key={step} aria-current={phases[index] === project.phase ? 'step' : undefined} className={index <= phases.indexOf(project.phase) ? 'reached' : ''}><span>{index + 1}</span>{step}</li>)}</ol>
        <div className="project-heading"><h3>{project.view.heading}</h3><span>{['completed', 'abandoned'].includes(project.status) ? project.view.edition : `剩余调查步数 ${project.budget} / 3`}</span></div>
        {['active', 'paused'].includes(project.status) && <p className="muted">调查步数用于这份材料的取舍，不是现实计时。合上手册不会消耗它。</p>}
        {project.cards.length > 0 && <div className="project-cards" aria-label="已留下的材料">{project.cards.map(card => <article key={card.id}><small>{card.source}</small><h4>{card.title}</h4><p>{card.text}</p><p className="project-unknown">{card.uncertainty}</p></article>)}</div>}
        {project.status === 'paused' ? <div className="project-event"><p>{project.pause_reason}</p><button className="primary-button" disabled={blocked} onClick={() => act({ action: 'project_resume' })}>继续这份手册</button></div> : <>
          {project.view.event && <aside className="project-event"><strong>准备落笔时，出现了一个问题</strong><p>{project.view.event}</p></aside>}
          {project.status === 'active' && project.view.choices.length > 0 && <div className="project-choices" role="group" aria-label="本步选择">{project.view.choices.map(choice => <button key={choice.id} disabled={blocked} onClick={() => act({ action: 'project_choose', choice: choice.id })}><strong>{choice.label}</strong><span>{choice.hint}</span><small>使用 {choice.cost} 个调查步数</small></button>)}</div>}
        </>}
        {project.view.outcome && <p className="project-outcome">{project.view.outcome}</p>}
        {((project.status === 'active' && project.phase === 'compose') || project.status === 'completed') && <PageEditor key={`${project.id}:${project.version}`} project={project} epoch={session.worldEpoch} working={blocked} onSave={act} />}
        {project.status === 'active' && <button className="project-pause" disabled={blocked} onClick={() => act({ action: 'project_pause' })}>先合上，下次继续</button>}
        {['active', 'paused'].includes(project.status) && (confirmEnd ? <div className="project-event"><p>结束这次试作？已有材料会保留为未完成片段，此页不再继续。</p><div className="action-row"><button disabled={blocked} onClick={() => act({ action: 'project_abandon' })}>确定结束并保留片段</button><button onClick={() => setConfirmEnd(false)}>继续保留这次试作</button></div></div> : <button className="project-pause" onClick={() => setConfirmEnd(true)}>不想继续这页了</button>)}
        <details className="project-record"><summary>这页的选择与来源</summary><p>开始时的世界时间：{project.started_at}</p><blockquote>{project.source.content}</blockquote><p className="muted">由你核对的共同试作邀请；不会据此推断用户现实经历。</p><ol>{project.decisions.map((item, index) => <li key={index}>{({ window: '从窗边速写开始', archive: '从旧资料开始', verify: '补充核对', broaden: '换一个观察角度', leave_open: '把疑问留在这一页' })[item.choice]} · {item.world_time}</li>)}</ol></details>
      </>}
      {!active && <details className="project-start" open={!projects.length}>
        <summary>{projects.length ? '另起一页 · 尝试不同选择' : '准备一起试做'}</summary>
        <p>{intro?.description}</p>
        <p>先点击“和{session.config?.display_name || '依鸣'}商量”发出邀请，等对话完成后，在这里选择回复。</p>
        <form onSubmit={event => { event.preventDefault(); act({ action: 'project_start', message_id: source }); }}>
          <label>共同试作的对话来源<select required value={source} onChange={event => setSource(event.target.value)}><option value="">选择已保存、已完成的角色回复</option>{sources.slice(-30).reverse().map(item => <option key={item.message_id} value={item.message_id}>{item.content.slice(0, 70)}</option>)}</select></label>
          <label className="setting-row"><span>我已核对这条回复，双方愿意共同试作</span><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /></label>
          <button className="primary-button" disabled={blocked || !confirmed || !source}>开始共同试作</button>
        </form>
      </details>}
      {session.config?.world_paused && <p role="status">世界已暂停，在设置中继续后可以操作手册。</p>}
    </>}
  </section>;
}
