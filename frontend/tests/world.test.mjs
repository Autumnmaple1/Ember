import test from 'node:test';
import assert from 'node:assert/strict';
import { WorldEpoch, mergeConfigSnapshot } from '../src/worldEpoch.js';
import { scenePresentation } from '../src/scenePresentation.js';
import { AudioController } from '../src/audioController.js';
import { mergeHistory } from '../src/messageHistory.js';

test('restore retires old epochs and ignores duplicate replay without dropping unseen out of order events', () => {
  const gate = new WorldEpoch();
  gate.reset('one');
  assert.ok(gate.accepts({ epoch: 'one', event_seq: 5 }));
  assert.ok(gate.accepts({ epoch: 'one', event_seq: 3 }));
  assert.ok(!gate.accepts({ epoch: 'one', event_seq: 5 }));
  gate.reset('two');
  assert.ok(!gate.reset('one'));
  assert.ok(!gate.accepts({ epoch: 'one', type: 'audio' }));
  assert.ok(!gate.accepts({ type: 'audio' }));
});

test('unrecognized presentation never selects arbitrary assets or narrative locations', () => {
  assert.equal(scenePresentation({ state: { '客观情境': '图书馆' } }).image, null);
  assert.equal(scenePresentation({ presentation: { background: '../../external', period: 'invented' } }).id, 'neutral');
  assert.equal(scenePresentation({ presentation: { background: 'dorm', period: 'night' } }).image, '/scenes/dorm.svg');
});

test('late snapshots cannot regress state, records or terminal archive tasks', () => {
  const current = { epoch: 'same', state_version: 4, state: { current: true }, experience: { revision: 7 }, archive_task: { task_id: 'a', status: 'completed' } };
  const late = { epoch: 'same', state_version: 2, state: { current: false }, experience: { revision: 3 }, archive_task: { task_id: 'a', status: 'running' } };
  const result = mergeConfigSnapshot(current, late);
  assert.equal(result.state.current, true);
  assert.equal(result.experience.revision, 7);
  assert.equal(result.archive_task.status, 'completed');
  assert.equal(mergeConfigSnapshot(current, { ...late, epoch: 'new' }).state_version, 2);
  assert.equal(mergeConfigSnapshot({ epoch: 'same', archive_task: { task_id: 'newer', revision: 10 } },
    { epoch: 'same', archive_task: { task_id: 'older', revision: 7 } }).archive_task.task_id, 'newer');
});

test('authoritative saved final repairs a stream whose finish event was lost', () => {
  const result = mergeHistory([{ role: 'ai', message_id: 'reply', streaming: true, content: '片段', rawContent: '片段' }],
    [{ id: 1, role: 'ai', message_id: 'reply', content: '已保存的完整回复', completion_status: 'completed' }]);
  assert.equal(result[0].content, '已保存的完整回复');
  assert.equal(result[0].streaming, false);
});

test('stopping while waiting for ownership cannot resurrect audio', async () => {
  const states = [], created = [];
  let grant;
  const audio = new AudioController(state => states.push(state), source => { created.push(source); return { play: async () => {} }; });
  audio.acquire = () => new Promise(resolve => { grant = resolve; });
  audio.setEnabled(true);
  audio.receive({ message_id: 'old', audio_base64: 'synthetic' });
  audio.stop();
  grant(true);
  await Promise.resolve();
  assert.equal(created.length, 0);
  assert.equal(states.at(-1).state, 'idle');
});

test('only one ownership request runs and denied audio remains recoverable', async () => {
  let calls = 0, grant;
  const states = [];
  const audio = new AudioController(state => states.push(state));
  audio.acquire = () => { calls += 1; return new Promise(resolve => { grant = resolve; }); };
  audio.setEnabled(true);
  audio.receive({ message_id: 'one' });
  audio.receive({ message_id: 'two' });
  assert.equal(calls, 1);
  grant(false);
  await Promise.resolve();
  assert.equal(states.at(-1).state, 'blocked');
  assert.equal(audio.queue.length, 0);
});
