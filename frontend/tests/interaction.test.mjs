import assert from 'node:assert/strict';
import test from 'node:test';
import { AudioController } from '../src/audioController.js';
import { applyMessage, parseAssistantText, mergeHistory } from '../src/messageHistory.js';

test('actual memory tool markup stays hidden at each streaming boundary', () => {
  for (const tag of ['tool', 'tool_call']) {
    const raw = `<${tag}>{"name":"memory_query","parameters":{"query":"私有检索词"}}</${tag}>`;
    for (let length = 1; length <= raw.length; length += 1) assert.equal(parseAssistantText(raw.slice(0, length)).speech, '');
    assert.equal(parseAssistantText(raw + '桂花茶，我记得。').speech, '桂花茶，我记得。');
  }
});

test('provider reasoning stays hidden on every stream prefix and during history replay', () => {
  const raw = '<think>私有推理。</think><thought>角色独白</thought><response>你好</response>';
  let messages = [];
  for (const char of raw) {
    messages = applyMessage(messages, { sender: 'ai', message_id: 'private-test', mode: 'append', content: char });
    assert.ok(!messages[0].content.includes('私有'));
    assert.ok(!messages[0].content.includes('推理'));
    assert.ok(!messages[0].content.includes('角色独白'));
  }
  assert.equal(messages[0].content, '你好');
  assert.equal(parseAssistantText('<REASONING>私有推理</REASONING>你好').speech, '你好');
  assert.equal(parseAssistantText('<think>未完成推理').speech, '');
  assert.equal(mergeHistory([], [{ id: 1, role: 'ai', content: raw }])[0].content, '你好');
  assert.equal(mergeHistory([], [{ id: 2, role: 'user', content: '<think>用户原文</think>' }])[0].content, '<think>用户原文</think>');
});
import { isSendKey, readPreference } from '../src/preferences.js';
import { getJSON } from '../src/api.js';

function player() {
  const states = [], audios = [];
  const controller = new AudioController(state => states.push(state), source => {
    const audio = { source, paused: false, play: async () => {}, pause() { this.paused = true; }, removeAttribute() {}, load() {} };
    audios.push(audio);
    return audio;
  });
  return { controller, states, audios };
}

test('Chinese composition and Shift+Enter never submit', () => {
  for (const event of [{ key: 'Enter', isComposing: true }, { key: 'Enter', nativeEvent: { isComposing: true } },
    { key: 'Enter', keyCode: 229 }, { key: 'Enter', shiftKey: true }, { key: 'a' }]) assert.equal(isSendKey(event), false);
  assert.equal(isSendKey({ key: 'Enter' }), true);
});

test('invalid or unavailable browser preferences do not prevent startup', () => {
  assert.equal(readPreference('missing', false), false);
  globalThis.localStorage = { getItem: () => 'invalid JSON' };
  try { assert.equal(readPreference('autoPlay', false), false); }
  finally { delete globalThis.localStorage; }
});

test('hung HTTP requests time out and caller cancellation stays distinguishable', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    if (signal.aborted) reject(new DOMException('aborted', 'AbortError'));
  });
  try {
    await assert.rejects(getJSON('http://synthetic/config', { timeout: 5 }), /请求超时/);
    const abort = new AbortController();
    const request = getJSON('http://synthetic/history', { signal: abort.signal });
    abort.abort();
    await assert.rejects(request, { name: 'AbortError' });
  } finally { globalThis.fetch = original; }
});

test('muting drops late and queued audio; reenabling does not replay suppressed turns', async () => {
  const { controller, audios } = player();
  controller.setEnabled(true);
  controller.beginMessage('one');
  controller.receive({ message_id: 'one', audio_base64: 'one' });
  await Promise.resolve();
  controller.beginMessage('two');
  controller.receive({ message_id: 'two', audio_base64: 'two' });
  controller.setEnabled(false);
  assert.equal(audios[0].paused, true);
  controller.receive({ message_id: 'three', audio_base64: 'late' });
  controller.setEnabled(true);
  controller.receive({ message_id: 'two', audio_base64: 'queued' });
  controller.receive({ message_id: 'three', audio_base64: 'late-again' });
  assert.equal(audios.length, 1);
  controller.beginMessage('fresh');
  controller.receive({ message_id: 'fresh', audio_base64: 'fresh' });
  assert.equal(audios.length, 2);
});

test('manual replay works while muted and only for a pending request', () => {
  const { controller, audios } = player();
  controller.request('mine');
  controller.receive({ request_id: 'another-client', audio_base64: 'wrong' });
  assert.equal(audios.length, 0);
  controller.receive({ request_id: 'mine', audio_base64: 'right' });
  assert.equal(audios.length, 1);
  controller.request('cancelled');
  controller.stop();
  controller.receive({ request_id: 'cancelled', audio_base64: 'late' });
  assert.equal(audios.length, 1);
});

test('a play promise resolving after stop cannot resurrect playback', async () => {
  const states = [];
  let resolvePlay;
  const fake = { play: () => new Promise(resolve => { resolvePlay = resolve; }), pause() {}, removeAttribute() {}, load() {} };
  const controller = new AudioController(state => states.push(state.state), () => fake);
  controller.setEnabled(true);
  controller.receive({ message_id: 'old', audio_base64: 'sound' });
  controller.stop();
  resolvePlay();
  await Promise.resolve();
  assert.equal(states.at(-1), 'idle');
  assert.equal(states.includes('playing'), false);
});

test('audio failures remain recoverable without blocking conversation text', async () => {
  const states = [];
  const controller = new AudioController(state => states.push(state), () => ({
    play: async () => { const error = new Error('gesture required'); error.name = 'NotAllowedError'; throw error; },
  }));
  controller.setEnabled(true);
  controller.receive({ message_id: 'text-still-visible', audio_base64: 'audio' });
  await Promise.resolve();
  assert.equal(states.at(-1).state, 'blocked');
  assert.equal(controller.current, null);
});

test('automatic speech errors are visible while muted or cancelled failures stay suppressed', () => {
  const { controller, states } = player();
  controller.setEnabled(true);
  controller.beginMessage('reply');
  controller.fail({ message_id: 'reply', content: '语音服务拒绝请求（403）' });
  assert.equal(states.at(-1).error, '语音服务拒绝请求（403）');
  controller.stop();
  controller.fail({ message_id: 'reply', content: '迟到的失败' });
  assert.equal(states.at(-1).state, 'idle');
  controller.setEnabled(false);
  controller.beginMessage('muted');
  controller.fail({ message_id: 'muted', content: '静音时的失败' });
  assert.equal(states.at(-1).state, 'idle');
});

test('manual replay errors stay request-correlated and ignore late automatic failures', () => {
  const { controller, states } = player();
  controller.setEnabled(true);
  controller.request('manual');
  controller.beginMessage('auto');
  controller.fail({ message_id: 'auto', content: '自动失败' });
  controller.fail({ request_id: 'other', content: '其他请求失败' });
  assert.equal(states.at(-1).state, 'loading');
  controller.fail({ request_id: 'manual', content: '网络连接失败' });
  assert.equal(states.at(-1).error, '网络连接失败');
  controller.stop();
  controller.fail({ request_id: 'manual', content: '迟到的失败' });
  assert.equal(states.at(-1).state, 'idle');
});

test('receipts and history reconcile an optimistic message without duplication', () => {
  let messages = [{ role: 'user', id: 'stable', message_id: 'stable', client_request_id: 'stable', content: '你好', delivery: 'submitting' }];
  messages = applyMessage(messages, { sender: 'user', message_id: 'stable', client_request_id: 'stable', timestamp: 1000, content: '你好' });
  assert.equal(messages.length, 1);
  assert.equal(messages[0].delivery, 'received');
  messages = mergeHistory(messages, [{ role: 'user', message_id: 'stable', id: 3, timestamp: 1000, content: '你好' }]);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].delivery, 'persisted');
  messages = applyMessage(messages, { sender: 'user', message_id: 'stable', content: '你好' });
  assert.equal(messages[0].delivery, 'persisted');
});

test('thought chunks stay outside speech while the final response replaces tool fragments', () => {
  let messages = applyMessage([], { sender: 'ai', message_id: 'reply', mode: 'start', content: '' });
  messages = applyMessage(messages, { sender: 'ai', message_id: 'reply', mode: 'append', content: '<thought>独白' });
  assert.equal(messages[0].content, '');
  messages = applyMessage(messages, { sender: 'ai', message_id: 'reply', mode: 'append', content: '</thought><response>你好</response>' });
  assert.equal(messages[0].content, '你好');
  messages = applyMessage(messages, { sender: 'ai', message_id: 'reply', mode: 'replace', content: '<thought>独白</thought><response>最终对白</response>' });
  assert.equal(messages[0].content, '最终对白');
  assert.equal(parseAssistantText('<thought>独白</thought><response>你好</response>').thought, '独白');
  assert.equal(parseAssistantText('<th').speech, '');
});

test('history restores a completed reply when its final live event was missed', () => {
  const messages = [{ id: 'reply', message_id: 'reply', role: 'ai', streaming: false, content: '断线前的片段', rawContent: '断线前的片段' }];
  const result = mergeHistory(messages, [{ id: 4, message_id: 'reply', role: 'ai', timestamp: 1000, content: '完整的已保存回复' }]);
  assert.equal(result[0].content, '完整的已保存回复');
  assert.equal(result.length, 1);
});

test('tool payloads and split control tags never enter visible dialogue', () => {
  assert.equal(parseAssistantText('前文<tool_call>{"name":"lookup"}</tool_call>后文').speech, '前文后文');
  assert.equal(parseAssistantText('前文<tool_call>{"name":').speech, '前文');
  assert.equal(parseAssistantText('<tool_').speech, '');
  assert.equal(parseAssistantText('数值 a < threshold').speech, '数值 a < threshold');
});

test('interrupted completion metadata survives history and late final replacement', () => {
  let messages = applyMessage([], { sender: 'ai', message_id: 'fragment', turn_id: 'turn', mode: 'replace',
    content: '未完成片段', completion_status: 'interrupted' });
  messages = mergeHistory(messages, [{ id: 1, message_id: 'fragment', turn_id: 'turn', role: 'ai',
    content: '未完成片段', completion_status: 'interrupted', timestamp: 1000 }]);
  assert.equal(messages[0].completion_status, 'interrupted');
  assert.equal(messages[0].turn_id, 'turn');
});

test('history recovers an interruption marker when its live terminal message was lost', () => {
  const current = [{ role: 'ai', message_id: 'missed-terminal', content: '原先显示的片段', streaming: false, completion_status: 'completed' }];
  const result = mergeHistory(current, [{ id: 9, role: 'ai', message_id: 'missed-terminal', content: '已保存的中断片段',
    timestamp: 1000, completion_status: 'interrupted', turn_id: 'saved-turn' }]);
  assert.equal(result[0].completion_status, 'interrupted');
  assert.equal(result[0].turn_id, 'saved-turn');
  assert.equal(result[0].content, '已保存的中断片段');
});
