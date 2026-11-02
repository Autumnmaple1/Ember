import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, WS_URL, getJSON } from './api';
import { applyMessage, mergeHistory, parseAssistantText } from './messageHistory';
import { AudioController } from './audioController';
import { WorldEpoch, mergeConfigSnapshot } from './worldEpoch';
import { PlaybackOwner } from './playbackOwner';

export default function useEmberSession(autoPlay) {
  const [messages, setMessages] = useState([]);
  const [config, setConfig] = useState(null);
  const [connection, setConnection] = useState('connecting');
  const [configError, setConfigError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [hasMore, setHasMore] = useState(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState('idle');
  const [activeTurn, setActiveTurn] = useState(null);
  const [notice, setNotice] = useState('');
  const [thought, setThought] = useState('');
  const [emotion, setEmotion] = useState(null);
  const emotionTimer = useRef(null);
  const [playback, setPlayback] = useState({ state: 'idle', audio: null, error: '' });
  const [audio] = useState(() => new AudioController(setPlayback));
  const ws = useRef(null);
  const busyRef = useRef(false);
  const configRef = useRef(null);
  const historyFlight = useRef(null);
  const failedCursor = useRef(null);
  const rawReplies = useRef(new Map());
  const receiptTimers = useRef(new Map());
  const savedMessages = useRef(new Set());
  const lastSendAt = useRef(0);
  const sceneVersion = useRef(0);
  const turnVersion = useRef(0);
  const activeTurnRef = useRef(null);
  const finishedTurns = useRef(new Map());
  const stopWaiter = useRef(null);
  const epochGate = useRef(new WorldEpoch());
  const worldReady = useRef(false);
  const worldFlight = useRef(null);
  const synchronizeRef = useRef(null);
  const [worldEpoch, setWorldEpoch] = useState(null);
  const [syncingWorld, setSyncingWorld] = useState(false);
  const [focusMessage, setFocusMessage] = useState(null);
  const timings = useRef(new Map());
  const [metrics, setMetrics] = useState([]);
  const markTiming = useCallback((id, stage) => {
    const entry = timings.current.get(id);
    if (!entry || entry[stage] !== undefined) return;
    entry[stage] = Math.round(performance.now() - entry.started);
    setMetrics([...timings.current.values()].map(record => Object.fromEntries(Object.entries(record).filter(([key]) => key !== 'started'))));
  }, []);

  const resetWorld = useCallback(epoch => {
    if (!epochGate.current.reset(epoch)) return false;
    audio.stop();
    worldReady.current = false;
    historyFlight.current?.abort();
    historyFlight.current = null;
    receiptTimers.current.forEach(clearTimeout);
    receiptTimers.current.clear();
    rawReplies.current.clear();
    savedMessages.current.clear();
    finishedTurns.current.clear();
    activeTurnRef.current = null;
    setActiveTurn(null);
    setMessages([]);
    setThought('');
    clearTimeout(emotionTimer.current);
    setEmotion(null);
    setFocusMessage(null);
    timings.current.clear(); setMetrics([]);
    setHasMore(true);
    busyRef.current = false;
    setBusy(false);
    if (stopWaiter.current) { stopWaiter.current.reject(new Error('世界已恢复到其他版本')); stopWaiter.current = null; }
    sceneVersion.current += 1;
    turnVersion.current += 1;
    setWorldEpoch(epoch);
    return true;
  }, [audio]);

  const applyTurn = useCallback(snapshot => {
    if (!snapshot?.turn_id) return;
    if (['completed', 'interrupted', 'failed'].includes(snapshot.status) && snapshot.released === false) {
      snapshot = { ...snapshot, status: 'finalizing', phase: 'finalizing' };
    }
    const current = activeTurnRef.current;
    const terminal = ['completed', 'interrupted', 'failed'].includes(snapshot.status);
    if (terminal) {
      markTiming(snapshot.turn_id, 'completed');
      finishedTurns.current.set(snapshot.turn_id, snapshot.status);
      if (finishedTurns.current.size > 64) finishedTurns.current.delete(finishedTurns.current.keys().next().value);
      setMessages(previous => previous.map(message => message.turn_id === snapshot.turn_id
        ? { ...message, streaming: false } : message));
      if (current && current.turn_id !== snapshot.turn_id) return;
      activeTurnRef.current = snapshot;
      setActiveTurn(snapshot);
      busyRef.current = false;
      setBusy(false);
      setPhase('idle');
      if (snapshot.status !== 'completed') audio.stop();
      if (snapshot.status === 'interrupted') setNotice('本轮已停止。已显示片段标为中断，已确认的场景不会回滚。');
      if (snapshot.status === 'failed') setNotice('本轮处理失败，未完成的片段不会作为完整回复。可以重新发送。');
      if (stopWaiter.current?.id === snapshot.turn_id) {
        const waiter = stopWaiter.current;
        stopWaiter.current = null;
        waiter.resolve(snapshot);
      }
      return;
    }
    if (finishedTurns.current.has(snapshot.turn_id)) return;
    const next = stopWaiter.current?.id === snapshot.turn_id ? { ...snapshot, status: 'stopping', phase: 'stopping' } : snapshot;
    activeTurnRef.current = next;
    setActiveTurn(next);
    busyRef.current = true;
    setBusy(true);
    setPhase(next.phase || 'preparing');
    if (next.status === 'stopping') audio.stop();
  }, [audio, markTiming]);

  const loadHistory = useCallback(async (cursor = null) => {
    if (historyFlight.current) return false;
    const abort = new AbortController();
    const epoch = epochGate.current.epoch;
    historyFlight.current = abort;
    setLoadingHistory(true);
    const query = new URLSearchParams({ limit: '20' });
    if (cursor) {
      query.set('before', cursor.timestamp);
      if (cursor.history_id) query.set('before_id', cursor.history_id);
    }
    try {
      const data = await getJSON(`${API_BASE}/history?${query}`, { signal: abort.signal });
      if (epoch !== epochGate.current.epoch) return false;
      if (!Array.isArray(data)) throw new Error('聊天记录返回格式无效');
      setMessages(previous => mergeHistory(previous, [...data].reverse()));
      data.forEach(message => savedMessages.current.add(message.message_id));
      setHasMore(data.length === 20);
      setHistoryError('');
      failedCursor.current = null;
      return true;
    } catch (error) {
      if (error.name !== 'AbortError') {
        setHistoryError(error.message || '聊天记录加载失败');
        failedCursor.current = cursor;
      }
      return false;
    } finally {
      if (historyFlight.current === abort) {
        historyFlight.current = null;
        setLoadingHistory(false);
      }
    }
  }, []);

  useEffect(() => { audio.setEnabled(autoPlay); }, [autoPlay, audio]);
  useEffect(() => {
    const owner = new PlaybackOwner(`ember-audio:${API_BASE}`, () => audio.stop());
    audio.acquire = manual => owner.claim(manual);
    const hidden = () => { if (document.hidden) { audio.stop(); owner.release(); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { audio.acquire = null; owner.close(); document.removeEventListener('visibilitychange', hidden); };
  }, [audio]);
  useEffect(() => {
    if (!historyError || connection !== 'online') return;
    let disposed = false;
    let attempt = 0;
    let timer;
    const retry = async () => {
      attempt += 1;
      const success = await loadHistory(failedCursor.current);
      if (!success && !disposed && attempt < 3) timer = setTimeout(retry, 5000 * 2 ** attempt);
    };
    timer = setTimeout(retry, 5000);
    return () => { disposed = true; clearTimeout(timer); };
  }, [historyError, connection, loadHistory]);

  useEffect(() => {
    let disposed = false;
    let reconnect;
    let pollTimer;
    let pollAbort;
    const timers = receiptTimers.current;
    const synchronize = async (epoch = epochGate.current.epoch) => {
      if (!epoch || disposed || epochGate.current.retired.has(epoch)) return;
      resetWorld(epoch);
      const token = {};
      worldFlight.current = token;
      setSyncingWorld(true);
      try {
        const data = await getJSON(`${API_BASE}/sync?epoch=${encodeURIComponent(epoch)}&after=${epochGate.current.cursor}`);
        if (disposed || worldFlight.current !== token || data.boundary.epoch !== epochGate.current.epoch) return;
        if (data.tail.complete === false) throw new Error('事件超出恢复窗口，正在重新同步');
        configRef.current = mergeConfigSnapshot(configRef.current, data.config);
        setConfig(configRef.current);
        const live = data.live_reply;
        if (live?.message_id && ['running', 'stopping'].includes(data.config.turn?.status) && !rawReplies.current.has(live.message_id)) {
          rawReplies.current.set(live.message_id, live.content || '');
          setMessages(previous => applyMessage(previous, { ...live, type: 'message', sender: 'ai', mode: 'append' }));
        }
        data.history.forEach(message => savedMessages.current.add(message.message_id));
        setHasMore(data.history.length >= 100);
        data.tail.events.forEach(event => receive({ data: JSON.stringify(event) }));
        setMessages(previous => mergeHistory(previous, [...data.history].reverse()));
        if (!activeTurnRef.current && data.config.turn) applyTurn(data.config.turn);
        setLoadingHistory(false);
        setHistoryError('');
        setConfigError('');
        worldReady.current = true;
      } catch (error) {
        if (!disposed && worldFlight.current === token && !configRef.current?.status?.paused && configRef.current?.archive_task?.status !== 'running') setConfigError(`世界恢复失败：${error.message}`);
      } finally {
        if (!disposed && worldFlight.current === token) { worldFlight.current = null; setSyncingWorld(false); }
      }
    };
    synchronizeRef.current = synchronize;
    const clearReceipt = id => {
      clearTimeout(timers.get(id));
      timers.delete(id);
    };
    const poll = async () => {
      const startedAt = performance.now();
      const version = sceneVersion.current;
      const turnAtStart = turnVersion.current;
      pollAbort = new AbortController();
      try {
        const data = await getJSON(`${API_BASE}/config`, { signal: pollAbort.signal });
        if (disposed) return;
        if (data.epoch && epochGate.current.retired.has(data.epoch)) return;
        if (data.epoch && data.epoch !== epochGate.current.epoch) {
          resetWorld(data.epoch);
          synchronize(data.epoch);
        }
        if (data.epoch && !worldReady.current && !worldFlight.current && !data.status?.paused) synchronize(data.epoch);
        const snapshot = mergeConfigSnapshot(configRef.current, version === sceneVersion.current ? data : { ...data, state: configRef.current?.state || data.state });
        configRef.current = snapshot;
        setConfig(snapshot);
        setConfigError('');
        // A local send remains busy until acknowledged, even if an older poll returns idle.
        if (startedAt >= lastSendAt.current) {
          if (turnAtStart === turnVersion.current && data.capabilities?.cancel_turn && !data.turn && !data.is_thinking && !timers.size) {
            if (stopWaiter.current) {
              stopWaiter.current.reject(new Error('后台轮次已变化，无法确认停止结果；草稿已保留。'));
              stopWaiter.current = null;
              setNotice('后台轮次已变化，无法确认停止结果；草稿已保留。');
            }
            activeTurnRef.current = null;
            setActiveTurn(null);
          }
          if (turnAtStart === turnVersion.current && data.turn) applyTurn(data.turn);
          const thinking = !!data.is_thinking || timers.size > 0 || ['running', 'stopping', 'finalizing'].includes(activeTurnRef.current?.status);
          if (turnAtStart === turnVersion.current) {
            busyRef.current = thinking;
            setBusy(thinking);
            if (!thinking) setPhase('idle');
          }
        }
      } catch (error) {
        if (!disposed && error.name !== 'AbortError') setConfigError('无法同步场景与服务状态，请检查后端连接。');
      } finally {
        if (!disposed) pollTimer = setTimeout(poll, 2000);
      }
    };
    const receive = event => {
      let data;
      try { data = JSON.parse(event.data); }
      catch { setNotice('收到无法识别的服务消息，请重新连接。'); return; }
      if (data.type === 'ping') { ws.current?.send(JSON.stringify({ type: 'pong' })); return; }
      if (['world.reset', 'world.snapshot'].includes(data.type)) {
        if (epochGate.current.retired.has(data.epoch)) return;
        resetWorld(data.epoch);
        synchronize(data.epoch);
        return;
      }
      if (!epochGate.current.accepts(data)) return;
      if (data.type === 'experience.update') {
        setConfig(previous => previous && (data.experience.revision || 0) >= (previous.experience?.revision || 0) ? { ...previous, experience: data.experience } : previous);
        if (configRef.current && (data.experience.revision || 0) >= (configRef.current.experience?.revision || 0)) configRef.current.experience = data.experience;
        return;
      }
      if (data.type === 'archive.task') {
        if ((data.task.revision || 0) < (configRef.current?.archive_task?.revision || 0)) return;
        if (data.task.status === 'running') audio.stop();
        if (configRef.current) configRef.current = mergeConfigSnapshot(configRef.current, { ...configRef.current, archive_task: data.task });
        setConfig(previous => previous ? mergeConfigSnapshot(previous, { ...previous, archive_task: data.task }) : previous);
        return;
      }
      if (['turn.started', 'turn.stopping', 'turn.finished'].includes(data.type)) {
        turnVersion.current += 1;
        applyTurn(data);
        return;
      }
      if (data.type === 'message') {
        const id = data.message_id || data.id;
        if (data.sender !== 'user' && data.turn_id && data.mode !== 'replace' && (finishedTurns.current.has(data.turn_id)
          || (activeTurnRef.current?.turn_id === data.turn_id && activeTurnRef.current?.status === 'stopping'))) return;
        if (data.sender === 'user') {
          markTiming(data.client_request_id || id, 'received');
          clearReceipt(data.client_request_id || id);
          if (data.replayed) {
            activeTurnRef.current = null;
            setActiveTurn(null);
            busyRef.current = false;
            setBusy(false);
            setPhase('idle');
            applyTurn(data.turn_snapshot);
          } else if (activeTurnRef.current?.status !== 'stopping') setPhase('preparing');
        }
        else {
          if (data.mode === 'start') { rawReplies.current.set(id, ''); setThought(''); }
          const raw = data.mode === 'append' ? (rawReplies.current.get(id) || '') + (data.content || '') : data.content || '';
          rawReplies.current.set(id, raw);
          if (rawReplies.current.size > 32) rawReplies.current.delete(rawReplies.current.keys().next().value);
          const parsed = parseAssistantText(raw);
          if (parsed.speech) markTiming(data.turn_id, 'first_speech');
          if (parsed.thought) setThought(parsed.thought);
          const current = !data.turn_id || !activeTurnRef.current || activeTurnRef.current.turn_id === data.turn_id;
          if (!current || data.completion_status === 'interrupted') audio.suppressMessage(id);
          else audio.beginMessage(id);
          if (!data.turn_id) {
            busyRef.current = data.mode !== 'replace';
            setBusy(busyRef.current);
            setPhase(data.mode === 'replace' ? 'idle' : parsed.speech ? 'generating' : 'preparing');
          } else if (current && activeTurnRef.current?.status !== 'stopping' && !finishedTurns.current.has(data.turn_id)) {
            setPhase(data.mode === 'replace' ? 'finalizing' : parsed.speech ? 'generating' : 'preparing');
          }
        }
        setMessages(previous => applyMessage(previous, data).map(message =>
          savedMessages.current.has(message.message_id) ? { ...message, delivery: 'persisted' } : message));
        if (['normal', 'happy', 'sad', 'angry', 'questioning'].includes(data.live2d_emotion)) {
          setEmotion(data.live2d_emotion);
          clearTimeout(emotionTimer.current);
          emotionTimer.current = setTimeout(() => setEmotion(null), 6000);
        }
      } else if (data.type === 'message.persisted') {
        savedMessages.current.add(data.message_id);
        setMessages(previous => previous.map(message => message.message_id === data.message_id
          ? { ...message, delivery: 'persisted' } : message));
      } else if (data.type === 'llm.done') {
        if (data.turn_id && configRef.current?.capabilities?.cancel_turn) return;
        setMessages(previous => previous.map(message => message.role === 'ai' && (!data.message_id || message.message_id === data.message_id)
          ? { ...message, streaming: false } : message));
        busyRef.current = false;
        setBusy(false);
        setPhase('idle');
      } else if (data.type === 'audio' || data.type === 'voice' || data.type === 'tts_error') {
        if (!data.request_id && data.turn_id && (['interrupted', 'failed'].includes(finishedTurns.current.get(data.turn_id))
          || activeTurnRef.current?.turn_id !== data.turn_id || activeTurnRef.current?.status === 'stopping')) return;
        if (data.type === 'tts_error') audio.fail(data);
        else { markTiming(data.turn_id, 'audio_ready'); audio.receive(data.type === 'voice' ? { ...data, format: 'wav' } : data); }
      } else if (data.type === 'state_update') {
        if (data.state_version && data.state_version < (configRef.current?.state_version || 0)) return;
        sceneVersion.current += 1;
        if (configRef.current) configRef.current = { ...configRef.current, state: data.state, state_version: data.state_version };
        setConfig(previous => previous ? { ...previous, state: data.state } : previous);
      } else if (data.type === 'error') {
        if (data.action === 'cancel_turn') {
          setNotice(data.content);
          if (stopWaiter.current) { stopWaiter.current.reject(new Error(data.content)); stopWaiter.current = null; }
          return;
        }
        const id = data.client_request_id;
        if (id) {
          clearReceipt(id);
          setMessages(previous => previous.map(message => message.client_request_id === id
            ? { ...message, delivery: 'failed', error: data.content } : message));
          if (activeTurnRef.current?.turn_id === id) { activeTurnRef.current = null; setActiveTurn(null); }
        }
        busyRef.current = false;
        setBusy(false);
        setPhase('idle');
        setNotice(data.content || '操作未完成，请重试。');
      }
    };
    const connect = () => {
      if (disposed) return;
      const socket = new WebSocket(`${WS_URL}?protocol=2`);
      ws.current = socket;
      socket.onopen = () => {
        if (disposed) return;
        setConnection('online');
        if (epochGate.current.epoch) synchronize();
        else loadHistory();
      };
      socket.onmessage = receive;
      socket.onerror = () => { if (!disposed) setConnection('reconnecting'); };
      socket.onclose = () => {
        if (disposed) return;
        setConnection('reconnecting');
        audio.stop();
        if (stopWaiter.current) {
          stopWaiter.current.reject(new Error('连接中断，无法确认停止结果；草稿已保留。'));
          stopWaiter.current = null;
          setNotice('连接中断，无法确认停止结果；草稿已保留。');
        }
        timers.forEach(clearTimeout);
        timers.clear();
        setMessages(previous => previous.map(message => message.delivery === 'submitting'
          ? { ...message, delivery: 'uncertain' } : message.role === 'ai' ? { ...message, streaming: false } : message));
        reconnect = setTimeout(connect, 3000);
      };
    };
    poll();
    loadHistory();
    connect();
    return () => {
      disposed = true;
      clearTimeout(reconnect);
      clearTimeout(pollTimer);
      clearTimeout(emotionTimer.current);
      pollAbort?.abort();
      historyFlight.current?.abort();
      historyFlight.current = null;
      timers.forEach(clearTimeout);
      timers.clear();
      if (ws.current) { ws.current.onclose = null; ws.current.close(); }
      audio.stop();
      if (stopWaiter.current) { stopWaiter.current.reject(new Error('页面已离开')); stopWaiter.current = null; }
    };
  }, [loadHistory, audio, applyTurn, resetWorld, markTiming]);

  const sendMessage = (text, retry = null) => {
    if (!text.trim()) return false;
    if (ws.current?.readyState !== WebSocket.OPEN) { setNotice('连接尚未恢复，草稿已保留。'); return false; }
    if (busyRef.current) { setNotice('本轮还在处理中，你可以继续编辑下一条草稿。'); return false; }
    if ((epochGate.current.epoch && !worldReady.current) || configRef.current?.world_paused || configRef.current?.archive_task?.status === 'running' || configRef.current?.status?.paused || configRef.current?.status?.scene?.ready === false) {
      setNotice('场景正在准备或存读档，请稍后发送。'); return false;
    }
    const id = retry?.client_request_id || crypto.randomUUID();
    timings.current.set(id, { started: performance.now() });
    if (timings.current.size > 32) timings.current.delete(timings.current.keys().next().value);
    requestAnimationFrame(() => markTiming(id, 'local_feedback'));
    lastSendAt.current = performance.now();
    audio.stop();
    busyRef.current = true;
    setBusy(true);
    setPhase('sending');
    if (configRef.current?.capabilities?.cancel_turn) {
      const local = { turn_id: id, status: 'running', phase: 'sending' };
      activeTurnRef.current = local;
      setActiveTurn(local);
    }
    setNotice('');
    setMessages(previous => retry
      ? previous.map(message => message.client_request_id === id ? { ...message, delivery: 'submitting', error: '' } : message)
      : [...previous, { role: 'user', id, message_id: id, client_request_id: id, content: text.trim(), delivery: 'submitting' }]);
    const timer = setTimeout(() => {
      receiptTimers.current.delete(id);
      setMessages(previous => previous.map(message => message.client_request_id === id && message.delivery === 'submitting'
        ? { ...message, delivery: 'uncertain' } : message));
      const serverConfirmed = activeTurnRef.current?.turn_id === id && activeTurnRef.current.phase !== 'sending';
      if (!serverConfirmed) {
        activeTurnRef.current = null;
        setActiveTurn(null);
        busyRef.current = false;
        setBusy(false);
        setPhase('idle');
      }
      setNotice('尚未收到发送确认。请先同步记录；重试会沿用同一消息标识。');
    }, 12000);
    receiptTimers.current.set(id, timer);
    try {
      ws.current.send(JSON.stringify({ sender: 'user', format: 'text', content: text.trim(),
        client_request_id: id, retry: !!retry, epoch: epochGate.current.epoch }));
      return true;
    } catch {
      clearTimeout(timer);
      receiptTimers.current.delete(id);
      busyRef.current = false;
      if (activeTurnRef.current?.turn_id === id) { activeTurnRef.current = null; setActiveTurn(null); }
      setBusy(false);
      setPhase('idle');
      setMessages(previous => previous.map(message => message.client_request_id === id ? { ...message, delivery: 'failed' } : message));
      setNotice('发送失败，原文仍保留在消息中，可重新编辑或重试。');
      return false;
    }
  };

  const replay = message => {
    if (ws.current?.readyState !== WebSocket.OPEN) { setNotice('连接恢复后才能生成声音。'); return; }
    if (!configRef.current?.capabilities?.correlated_audio) { setNotice('请先更新并重启后端，以支持指定消息重播。'); return; }
    const id = crypto.randomUUID();
    audio.request(id);
    ws.current.send(JSON.stringify({ type: 'tts_request', content: message.content,
      message_id: message.message_id, request_id: id, epoch: epochGate.current.epoch }));
    setTimeout(() => audio.failRequest(id, '语音等待超时，可以重播这句。'), 30000);
  };

  const cancelTurn = () => {
    const turn = activeTurnRef.current;
    if (!configRef.current?.capabilities?.cancel_turn || !turn || ws.current?.readyState !== WebSocket.OPEN) {
      setNotice('暂时无法确认当前轮次，草稿已保留，请等待同步。');
      return Promise.reject(new Error('No cancellable turn'));
    }
    if (stopWaiter.current?.id === turn.turn_id) return stopWaiter.current.promise;
    let resolve, reject;
    const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
    stopWaiter.current = { id: turn.turn_id, promise, resolve, reject };
    audio.stop();
    setPhase('stopping');
    activeTurnRef.current = { ...turn, status: 'stopping', phase: 'stopping' };
    setActiveTurn(activeTurnRef.current);
    try { ws.current.send(JSON.stringify({ type: 'cancel_turn', turn_id: turn.turn_id, epoch: epochGate.current.epoch })); }
    catch (error) { stopWaiter.current = null; reject(error); setNotice('停止请求未发出，请恢复连接后重试。'); }
    return promise;
  };

  const playerAction = async action => {
    const epoch = epochGate.current.epoch;
    const result = await getJSON(`${API_BASE}/experience`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...action, epoch }) });
    if (epoch !== epochGate.current.epoch) throw new Error('世界已变化，请重试');
    configRef.current = mergeConfigSnapshot(configRef.current, { ...configRef.current, experience: result.experience, world_paused: result.world_paused });
    setConfig(previous => mergeConfigSnapshot(previous, { ...previous, experience: result.experience, world_paused: result.world_paused }));
    return result;
  };
  const locateMessage = async id => {
    const epoch = epochGate.current.epoch;
    const result = await getJSON(`${API_BASE}/history/source/${encodeURIComponent(id)}`);
    if (epoch !== epochGate.current.epoch || result.epoch !== epoch) throw new Error('世界已变化，请重试');
    if (!result.items.some(item => item.message_id === id)) throw new Error('这条来源不在当前存档中');
    setMessages(previous => mergeHistory(previous, result.items));
    setFocusMessage({ id, nonce: performance.now() });
  };

  const setVolume = useCallback(value => audio.setVolume(value), [audio]);
  return { messages, config, connection, configError, historyError, loadingHistory, hasMore, busy, phase,
    metrics, clearMetrics: () => { timings.current.clear(); setMetrics([]); },
    worldEpoch, syncingWorld, playerAction, locateMessage, focusMessage, refreshWorld: () => synchronizeRef.current?.(),
    setVolume, toggleAudioPause: () => audio.togglePause(),
    notice, setNotice, thought, emotion, playback, loadHistory,
    retryHistory: () => loadHistory(failedCursor.current), sendMessage, replay, activeTurn, cancelTurn, stopAudio: () => audio.stop() };
}
