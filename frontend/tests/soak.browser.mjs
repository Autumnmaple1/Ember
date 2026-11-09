// Real wall-clock lifecycle soak; synthetic messages/voice, real bundled model assets.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const minutes = Number(process.env.EMBER_SOAK_MINUTES || 30);
assert.ok(minutes >= 1 && minutes <= 60);
const vite = await createServer({ root, envDir: false, envPrefix: 'EMBER_SYNTHETIC_', define: {
  'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') }, server: { host: '127.0.0.1', port: 0 } });
await vite.listen();
let browser;
try {
  browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}),
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errors = [], samples = [];
  page.on('pageerror', failure => errors.push(failure.message));
  page.setDefaultTimeout(15000);
  await page.addInitScript(() => {
    localStorage.setItem('autoPlay', 'true');
    window.soakAudio = { active: 0, max: 0, starts: 0 };
    window.Audio = class {
      async play() {
        if (this.src?.startsWith('data:audio/wav;base64,UklGRiY')) return;
        if (!this.playing) { this.playing = true; window.soakAudio.active += 1; }
        window.soakAudio.starts += 1;
        window.soakAudio.max = Math.max(window.soakAudio.max, window.soakAudio.active);
        this.timer = setTimeout(() => { this.pause(); this.onended?.(); }, 200);
      }
      pause() { clearTimeout(this.timer); if (this.playing) { this.playing = false; window.soakAudio.active -= 1; } }
      removeAttribute() {} load() {}
    };
    const nativeAdd = EventTarget.prototype.addEventListener, nativeRemove = EventTarget.prototype.removeEventListener;
    const listeners = new Map();
    EventTarget.prototype.addEventListener = function(type, callback, options) {
      if (this === document || this === window) {
        const key = (this === window ? 'window:' : 'document:') + type + ':' + String(typeof options === 'boolean' ? options : !!options?.capture);
        if (!listeners.has(key)) listeners.set(key, new Set());
        listeners.get(key).add(callback);
      }
      return nativeAdd.call(this, type, callback, options);
    };
    EventTarget.prototype.removeEventListener = function(type, callback, options) {
      if (this === document || this === window) {
        const key = (this === window ? 'window:' : 'document:') + type + ':' + String(typeof options === 'boolean' ? options : !!options?.capture);
        listeners.get(key)?.delete(callback);
      }
      return nativeRemove.call(this, type, callback, options);
    };
    window.soakListeners = () => [...listeners.values()].reduce((count, callbacks) => count + callbacks.size, 0);
  });
  let socket, epoch = 'soak-0', seq = 0;
  const history = [];
  const config = () => ({ epoch, event_seq: seq, state_version: 0, display_name: '依鸣', timezone: 'Asia/Shanghai', logical_time: '2026-11-02 15:00:00', is_thinking: false, turn: null,
    capabilities: { world_epoch: true, correlated_audio: true }, state: { scene: { character_location: '图书馆', communication_mode: 'unknown' } },
    presentation: { background: 'library', period: 'day' }, status: { scene: { ready: true }, database: { ready: true }, long_term_memory: { ready: true }, paused: false } });
  const emit = event => socket.send(JSON.stringify({ ...event, epoch, event_seq: ++seq }));
  await page.route('http://localhost:8000/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path === '/config' ? config() : path === '/sync' ? {
      config: config(), history, boundary: { epoch, event_seq: seq }, tail: { complete: true, events: [] }
    } : path === '/history' ? history : [] });
  });
  await page.routeWebSocket('**/ws/chat*', ws => { socket = ws; ws.send(JSON.stringify({ type: 'world.snapshot', epoch, event_seq: ++seq })); });
  await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}`);
  await page.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor({ timeout: 40000 });
  const cdp = await page.context().newCDPSession(page);
  const started = Date.now();
  let turns = 0, baselineListeners = null, baselineHeap = null, nextSample = 0;
  while (Date.now() - started < minutes * 60000) {
    const id = `soak-reply-${++turns}`;
    emit({ type: 'message', sender: 'ai', mode: 'start', message_id: id, timestamp: Date.now(), content: '' });
    for (const content of ['<thought>合成检查</thought>', '<response>这一条', '只是稳定性检查。', '</response>']) emit({ type: 'message', sender: 'ai', mode: 'append', message_id: id, content });
    emit({ type: 'message', sender: 'ai', mode: 'replace', message_id: id, content: '这一条只是稳定性检查。', completion_status: 'completed' });
    emit({ type: 'llm.done', message_id: id });
    emit({ type: 'audio', message_id: id, audio_base64: 'synthetic' });
    if (turns % 6 === 0) {
      await page.getByRole('button', { name: turns % 12 ? '对话优先' : '舞台优先', exact: true }).click();
      await page.getByRole('button', { name: '设置', exact: true }).click();
      await page.getByLabel('低性能模式 · 降低渲染分辨率和帧率').setChecked(turns % 12 === 0);
      await page.keyboard.press('Escape');
      await page.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor();
    }
    if (turns % 60 === 0) { epoch = `soak-${turns}`; seq = 0; emit({ type: 'world.reset' }); }
    if (Date.now() - started >= nextSample) {
      await cdp.send('HeapProfiler.collectGarbage');
      const heap = await cdp.send('Runtime.getHeapUsage');
      const ui = await page.evaluate(() => ({ listeners: window.soakListeners(), audio: window.soakAudio, canvases: document.querySelectorAll('.live2d-viewer canvas').length }));
      if (turns >= 12 && baselineListeners === null) { baselineListeners = ui.listeners; baselineHeap = heap.usedSize; }
      assert.ok(ui.audio.max <= 1, 'overlapping audio');
      assert.equal(ui.canvases, 1, 'duplicate renderer');
      if (baselineListeners !== null) assert.ok(ui.listeners <= baselineListeners + 8, 'global listeners keep growing');
      if (baselineHeap !== null) assert.ok(heap.usedSize <= baselineHeap + 100 * 1024 * 1024, 'unbounded JS heap growth');
      assert.deepEqual(errors, []);
      const sample = { elapsedSeconds: Math.round((Date.now() - started) / 1000), turns, jsHeapBytes: heap.usedSize, ...ui };
      samples.push(sample); console.log(JSON.stringify(sample));
      nextSample += 60000;
    }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
  await mkdir(resolve(root, 'test-results'), { recursive: true });
  const report = { result: 'passed', actualDurationSeconds: Math.round((Date.now() - started) / 1000), turns, samples };
  await writeFile(resolve(root, 'test-results', 'soak-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ result: 'passed', actualDurationSeconds: report.actualDurationSeconds, turns }));
} finally { await browser?.close(); await vite.close(); }
