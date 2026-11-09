import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { messageKey } from '../messageHistory';
const deliveryLabel = {
  submitting: '发送中',
  received: '已接收 · 等待保存',
  persisted: '已保存',
  uncertain: '发送结果待确认',
  failed: '未发送成功'
};
function messageTime(timestamp, timezone) {
  if (!timestamp) return '时间待确认';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone || 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(new Date(Number(timestamp)));
}
export default function ChatPanel({ onPinMaterial,
  session,
  onEdit,
  fontSize
}) {
  const list = useRef(null);
  const follow = useRef(true);
  const anchor = useRef(null);
  const geometry = useRef({
    width: 0,
    height: 0
  });
  const [following, setFollowing] = useState(true);
  const [seen, setSeen] = useState('');
  const focusedNonce = useRef(null);
  const {
    messages,
    loadingHistory,
    historyError,
    hasMore,
    loadHistory
  } = session;
  const visible = messages.filter(message => message.content);
  const latest = visible.at(-1);
  const latestVersion = latest ? `${messageKey(latest)}:${latest.content.length}` : '';
  const unread = !following && latestVersion !== seen;
  useLayoutEffect(() => {
    if (!session.focusMessage || focusedNonce.current === session.focusMessage.nonce) return;
    const node = [...(list.current?.querySelectorAll('[data-message-key]') || [])].find(item => item.dataset.messageKey === session.focusMessage.id);
    if (node) { focusedNonce.current = session.focusMessage.nonce; follow.current = false; node.scrollIntoView({ block: 'center' }); node.focus({ preventScroll: true }); }
  }, [session.focusMessage, messages]);
  const toLatest = () => {
    follow.current = true;
    setFollowing(true);
    setSeen(latestVersion);
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  };
  useLayoutEffect(() => {
    const element = list.current;
    if (!element) return;
    if (anchor.current && !loadingHistory) {
      const saved = anchor.current;
      const node = [...element.querySelectorAll('[data-message-key]')].find(item => item.dataset.messageKey === saved.key);
      if (node) element.scrollTop += node.getBoundingClientRect().top - saved.top;else element.scrollTop = saved.scrollTop + element.scrollHeight - saved.height;
      anchor.current = null;
    } else if (!anchor.current && follow.current) element.scrollTop = element.scrollHeight;
  }, [messages, loadingHistory, fontSize]);
  useEffect(() => {
    const element = list.current;
    const observer = new ResizeObserver(() => {
      if (follow.current && !anchor.current) element.scrollTop = element.scrollHeight;
      geometry.current = {
        width: element.clientWidth,
        height: element.clientHeight
      };
    });
    observer.observe(element);
    observer.observe(element.querySelector('ol'));
    return () => observer.disconnect();
  }, []);
  const more = async () => {
    const oldest = messages.find(message => message.history_id && message.timestamp);
    if (!oldest || loadingHistory) return;
    const element = list.current;
    const top = element.getBoundingClientRect().top;
    const first = [...element.querySelectorAll('[data-message-key]')].find(item => item.getBoundingClientRect().bottom > top);
    anchor.current = {
      key: first?.dataset.messageKey,
      top: first?.getBoundingClientRect().top,
      scrollTop: element.scrollTop,
      height: element.scrollHeight
    };
    follow.current = false;
    setFollowing(false);
    await loadHistory({
      timestamp: oldest.timestamp,
      history_id: oldest.history_id
    });
  };
  return <section className="conversation-panel" aria-label="当前对话" style={{
    '--dialogue-font': `${fontSize}px`
  }}>
  <header className="conversation-heading">
    <div>
      <span className="eyebrow">CONVERSATION</span>
      <h2>和{session.config?.display_name || '依鸣'}聊聊</h2>
    </div>
    <button className="quiet-button" onClick={() => loadHistory()} disabled={loadingHistory}>同步记录</button>
  </header>
  {historyError && <div className="inline-error" role="alert">
    聊天记录加载失败：
    {historyError}
    <button onClick={session.retryHistory} disabled={loadingHistory}>重试加载</button>
  </div>}
  <div ref={list} className="conversation-scroll" onScroll={event => {
      const element = event.currentTarget;
      const changedSize = element.clientWidth !== geometry.current.width || element.clientHeight !== geometry.current.height;
      if (changedSize && follow.current) {
        element.scrollTop = element.scrollHeight;
        return;
      }
      const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
      follow.current = atBottom;
      setFollowing(atBottom);
      if (atBottom) setSeen(latestVersion);
    }} aria-label="聊天记录" aria-busy={loadingHistory}>
    {hasMore && messages.some(message => message.history_id) && <button className="load-more" onClick={more} disabled={loadingHistory}>{loadingHistory ? '正在加载…' : '加载更早的对话'}</button>}
    {loadingHistory && !messages.length && <div className="empty-conversation" role="status">正在找回你们的对话…</div>}
    {!loadingHistory && !historyError && !visible.length && <div className="empty-conversation">
      <span>从一句话开始</span>
      <p>聊聊今天，或说说你此刻想做的事。</p>
    </div>}
    <ol className="conversation-messages" aria-live="off">{visible.map(message => <li tabIndex={-1} key={messageKey(message)} data-message-key={messageKey(message)} className={`message message-${message.role}${session.focusMessage?.id === message.message_id ? ' message-highlight' : ''}`}>
        <div className="message-meta">
          <span>{message.role === 'ai' ? session.config?.display_name || '依鸣' : '你'}</span>
          <time>{messageTime(message.timestamp, session.config?.timezone)}</time>
          {message.role === 'ai' && message.completion_status === 'interrupted' && <span className="interrupted-label">已中断 · 未完成片段</span>}
          {message.role === 'user' && <span className={`delivery delivery-${message.delivery}`}>{deliveryLabel[message.delivery]}</span>}
        </div>
        <p className="message-text">{message.content}</p>
        {session.config?.capabilities?.player_records && <button className="quiet-button" disabled={!message.history_id && message.delivery !== 'persisted'} onClick={() => session.playerAction({ action: 'favorite', message_id: message.message_id, enabled: !session.config.experience?.favorites.some(item => item.message_id === message.message_id) }).catch(error => session.setNotice(error.message))}>{session.config.experience?.favorites.some(item => item.message_id === message.message_id) ? '取消收藏' : '收藏这条'}</button>}
        {message.role === 'user' && session.config?.capabilities?.shared_materials && <button className="quiet-button"
          disabled={!message.history_id && message.delivery !== 'persisted'} onClick={() => onPinMaterial?.(message)}>一起看这段</button>}
        {message.role === 'ai' && <div className="message-actions">
          <button onClick={() => session.replay(message)} disabled={session.connection !== 'online'}>重播这句</button>
          <button onClick={() => {
            if (!navigator.clipboard) { session.setNotice('浏览器未开放剪贴板，请选择文字复制。'); return; }
            navigator.clipboard.writeText(message.content).catch(() => session.setNotice('复制失败，请选择文字复制。'));
          }}>复制</button>
        </div>}
        {['failed', 'uncertain'].includes(message.delivery) && <div className="message-recovery">
          <p>{message.error || '尚未确认服务器是否接收；同步或重试时会核对同一消息标识。'}</p>
          <button onClick={() => onEdit(message.content)}>重新编辑</button>
          {session.config?.capabilities?.message_receipts && <button onClick={() => session.sendMessage(message.content, message)} disabled={session.busy || session.connection !== 'online'}>重试发送</button>}
        </div>}
      </li>)}</ol>
  </div>
  {!following && <button className="jump-to-latest" onClick={toLatest}>{unread ? '有新消息 · 回到最新' : '回到最新对话'}</button>}
  </section>;
}
