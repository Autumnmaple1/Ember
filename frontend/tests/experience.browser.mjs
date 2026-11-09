// No production backend is started: every API and chat socket is synthetic.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const artifacts = resolve(project, 'test-results');
await mkdir(artifacts, { recursive: true });
const vite = await createServer({ root: project,
  envDir: false, envPrefix: 'EMBER_SYNTHETIC_',
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') },
  server: { host: '127.0.0.1', port: 0, strictPort: false } });
await vite.listen();
let browser;
try {
  browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}),
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.audioStarts = 0;
    window.Audio = class {
      async play() { if (!this.src?.startsWith('data:audio/wav;base64,UklGRiY')) window.audioStarts += 1; }
      pause() {}
      removeAttribute() {}
      load() {}
    };
  });
  const now = Date.parse('2026-10-21T17:40:00+08:00');
  const history = Array.from({ length: 20 }, (_, index) => ({ id: index + 1, message_id: `history-${index}`,
    role: index % 2 ? 'ai' : 'user', timestamp: now - (20 - index) * 60000,
    content: index % 2 ? `这是第 ${index + 1} 条合成回复。一起慢慢想，就能找到思路。` : `这是第 ${index + 1} 条合成问题，我想讨论一下学习中的困惑。` }));
  let historyFailure = false, thinking = false, socket, rejectNext = false, rejectNextSpeech = false;
  const sent = [], accepted = new Map();
  const config = () => ({ display_name: '依鸣', logical_time: '2026-10-21 17:40:00', timezone: 'Asia/Shanghai', is_thinking: thinking,
    time_accel_factor: 1, capabilities: { message_receipts: true, correlated_audio: true, cancel_turn: false },
    state: { scene: { character_location: '图书馆自习室', user_location: null, communication_mode: 'unknown', remote_contact_confirmed: false },
      '客观情境': '图书馆内，活动待确认。', '近期综合轨迹': '整理笔记 -> 留下一个问题', P: 7, A: 4, D: 5 },
    status: { database: { ready: true }, long_term_memory: { ready: true }, scene: { ready: true, state: 'ready' }, paused: false } });
  await page.route('http://localhost:8000/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/config') return route.fulfill({ json: config() });
    if (url.pathname === '/config/time_accel') return route.fulfill({ status: 503, json: { detail: '模拟倍率保存失败' } });
    if (url.pathname === '/api/archive/list') return route.fulfill({ json: { success: true, archives: [] } });
    if (url.pathname === '/history') {
      if (historyFailure) return route.fulfill({ status: 503, json: { detail: '模拟数据库断连' } });
      if (url.searchParams.has('before')) return route.fulfill({ json: Array.from({ length: 3 }, (_, i) => ({ id: -i - 1,
        message_id: `older-${i}`, role: 'ai', timestamp: now - (23 + i) * 60000, content: `更早的合成消息 ${i}` })) });
      return route.fulfill({ json: [...history].reverse() });
    }
    return route.fulfill({ status: 404, json: { detail: 'unexpected synthetic route' } });
  });
  // Renderer fallback is intentional here; a separate smoke page below loads the real asset.
  await page.route('**/models/**', route => route.abort());
  await page.routeWebSocket('**/ws/chat*', ws => {
    socket = ws;
    ws.onMessage(raw => {
      const event = JSON.parse(raw);
      if (event.type === 'tts_request') {
        ws.send(JSON.stringify(rejectNextSpeech
          ? { type: 'tts_error', request_id: event.request_id, message_id: event.message_id, code: 'http_403', content: '语音服务拒绝请求（403）' }
          : { type: 'audio', request_id: event.request_id, message_id: event.message_id, audio_base64: 'synthetic' }));
        rejectNextSpeech = false;
        return;
      }
      if (!event.content) return;
      sent.push(event);
      if (rejectNext) { rejectNext = false; ws.send(JSON.stringify({ type: 'error', client_request_id: event.client_request_id, content: '模拟任务繁忙' })); return; }
      const receipt = { type: 'message', sender: 'user', content: event.content, message_id: event.client_request_id,
        client_request_id: event.client_request_id, timestamp: now + accepted.size * 1000 };
      ws.send(JSON.stringify(receipt));
      ws.send(JSON.stringify({ type: 'message.persisted', message_id: event.client_request_id }));
      if (accepted.has(event.client_request_id)) return;
      accepted.set(event.client_request_id, receipt);
      thinking = true;
      ws.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: `response-${accepted.size}`, mode: 'start', timestamp: now + 5000, content: '' }));
    });
  });
  const address = vite.httpServer.address();
  const url = `http://127.0.0.1:${address.port}`;
  await page.goto(url);
  await page.getByText('图书馆自习室', { exact: true }).waitFor();
  await page.locator('.message').nth(19).waitFor();
  await page.getByText('角色暂时无法显示，文字对话仍可使用。').waitFor();
  const input = page.getByRole('textbox', { name: '对依鸣说' });

  await input.fill('输入法选字');
  await input.dispatchEvent('compositionstart');
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
  assert.equal(sent.length, 0);
  await input.dispatchEvent('compositionend');
  await input.press('Shift+Enter');
  assert.equal(sent.length, 0);
  await input.fill('正常的合成输入');
  await input.press('Enter');
  await page.getByRole('button', { name: '等待本轮完成' }).waitFor();
  await input.fill('本轮结束以后再发的草稿');
  const replyId = `response-${accepted.size}`;
  for (const fragment of ['<thi', 'nk>私有推理不应展示', '</thi', 'nk>']) {
    socket.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: replyId, mode: 'append', content: fragment }));
    await page.waitForTimeout(30);
    assert.equal(await page.getByText('私有推理不应展示', { exact: false }).count(), 0);
  }
  socket.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: replyId, mode: 'append', content: '<thought>合成独白</thought><response>这是一条合成对白。</response>' }));
  thinking = false;
  socket.send(JSON.stringify({ type: 'llm.done', message_id: replyId }));
  await page.getByText('这是一条合成对白。', { exact: true }).waitFor();
  assert.equal(await input.inputValue(), '本轮结束以后再发的草稿');
  assert.equal(await page.locator('.message-user').last().locator('.delivery').innerText(), '已保存');
  socket.send(JSON.stringify({ type: 'audio', message_id: replyId, audio_base64: 'muted' }));
  await page.getByRole('button', { name: '自动声音：关' }).click();
  socket.send(JSON.stringify({ type: 'audio', message_id: replyId, audio_base64: 'stale-after-enable' }));
  assert.equal(await page.evaluate(() => window.audioStarts), 0);
  socket.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: 'fresh-audio', mode: 'start', timestamp: now + 6000, content: '' }));
  socket.send(JSON.stringify({ type: 'audio', message_id: 'fresh-audio', audio_base64: 'fresh' }));
  await page.getByRole('button', { name: '停止声音', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.audioStarts), 1);
  await page.getByRole('button', { name: '自动声音：开' }).click();
  socket.send(JSON.stringify({ type: 'audio', message_id: 'fresh-audio', audio_base64: 'late' }));
  socket.send(JSON.stringify({ type: 'llm.done' }));
  await page.locator('.message-ai').first().getByRole('button', { name: '重播这句' }).click();
  await page.getByRole('button', { name: '停止声音', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.audioStarts), 2);
  await page.getByRole('button', { name: '停止声音', exact: true }).click();

  rejectNextSpeech = true;
  await page.locator('.message-ai').first().getByRole('button', { name: '重播这句' }).click();
  await page.getByText('语音服务拒绝请求（403）', { exact: true }).waitFor();
  await page.locator('.message-ai').first().getByRole('button', { name: '重播这句' }).click();
  await page.getByRole('button', { name: '停止声音', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.audioStarts), 3);
  await page.getByRole('button', { name: '停止声音', exact: true }).click();
  await page.getByRole('button', { name: '自动声音：关' }).click();
  socket.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: 'failed-auto-voice', mode: 'replace', timestamp: now + 6500, content: '声音失败仍保留这条文字。' }));
  socket.send(JSON.stringify({ type: 'tts_error', message_id: 'failed-auto-voice', code: 'http_403', content: '语音服务拒绝请求（403）' }));
  await page.getByText('语音服务拒绝请求（403）', { exact: true }).waitFor();
  await page.getByText('声音失败仍保留这条文字。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '自动声音：开' }).click();

  const list = page.locator('.conversation-scroll');
  await list.evaluate(element => { element.scrollTop = 140; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const top = await list.evaluate(element => element.scrollTop);
  socket.send(JSON.stringify({ type: 'message', sender: 'ai', message_id: 'while-reading', mode: 'replace', timestamp: now + 7000, content: '阅读历史时新到的消息。' }));
  await page.getByRole('button', { name: '有新消息 · 回到最新' }).waitFor();
  assert.ok(Math.abs(await list.evaluate(element => element.scrollTop) - top) < 3);
  await list.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const anchorBefore = (await page.locator('[data-message-key="history-0"]').boundingBox()).y;
  await page.getByRole('button', { name: '加载更早的对话' }).click();
  await page.getByText('更早的合成消息 2', { exact: true }).waitFor();
  const anchorAfter = (await page.locator('[data-message-key="history-0"]').boundingBox()).y;
  assert.ok(Math.abs(anchorAfter - anchorBefore) < 3, 'pagination must preserve the visible reading anchor');
  const count = await page.locator('.message').count();
  historyFailure = true;
  await page.getByRole('button', { name: '同步记录' }).click();
  await page.getByRole('alert').filter({ hasText: '模拟数据库断连' }).waitFor();
  historyFailure = false;
  await page.getByRole('button', { name: '重试加载' }).click();
  await page.getByRole('alert').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('.message').count(), count);

  rejectNext = true;
  await input.fill('失败后重试的合成输入');
  await input.press('Enter');
  await page.getByRole('button', { name: '重试发送' }).waitFor();
  const failedId = sent.at(-1).client_request_id;
  await page.getByRole('button', { name: '重试发送' }).click();
  await page.locator('.message-user').last().locator('.delivery').filter({ hasText: '已保存' }).waitFor();
  assert.equal(sent.at(-1).client_request_id, failedId);
  thinking = false;
  socket.send(JSON.stringify({ type: 'llm.done' }));
  await input.fill('刷新后仍在的合成草稿');

  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('dialog', { name: '设置' }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.equal(await page.getByRole('button', { name: '设置', exact: true }).evaluate(element => document.activeElement === element), true);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('dialog', { name: '设置' }).waitFor();
  await page.getByRole('slider', { name: '世界时间倍率', exact: true }).fill('4');
  await page.getByRole('button', { name: '应用时间倍率' }).click();
  await page.getByText('时间倍率未修改：模拟倍率保存失败', { exact: true }).waitFor();
  assert.equal(await page.getByRole('slider', { name: '世界时间倍率', exact: true }).inputValue(), '1');
  await page.keyboard.press('Escape');
  await page.reload();
  await input.waitFor();
  assert.equal(await input.inputValue(), '刷新后仍在的合成草稿');

  const checked = [];
  for (const width of [320, 390, 768, 1366, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    for (const view of ['舞台优先', '对话优先']) {
      await page.getByRole('button', { name: view, exact: true }).click();
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert.ok(dimensions.scroll <= dimensions.width + 1, `horizontal overflow: ${width} ${view}`);
      checked.push(`${width}:${view}`);
      assert.equal(await input.inputValue(), '刷新后仍在的合成草稿');
    }
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.getByRole('button', { name: '舞台优先', exact: true }).click();
  await page.screenshot({ path: resolve(artifacts, 'desktop-fallback.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(artifacts, 'mobile.png'), fullPage: true });
  assert.deepEqual(errors, []);
  const renderPage = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const renderErrors = [];
  renderPage.on('console', message => { if (message.type() === 'error') renderErrors.push(message.text()); });
  await renderPage.route('http://localhost:8000/**', route => route.fulfill({ json: route.request().url().includes('/config') ? config() : [...history].reverse() }));
  await renderPage.routeWebSocket('**/ws/chat*', () => {});
  await renderPage.goto(url);
  try { await renderPage.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor({ timeout: 30000 }); }
  catch (error) { console.error(JSON.stringify({ rendererErrors: renderErrors })); throw error; }
  await renderPage.screenshot({ path: resolve(artifacts, 'desktop-live2d.png'), fullPage: true });
  await renderPage.locator('.live2d-viewer canvas').evaluate(canvas => canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true })));
  await renderPage.getByRole('button', { name: '重新加载角色' }).click();
  await renderPage.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor({ timeout: 30000 });
  await renderPage.close();
  console.log(JSON.stringify({ result: 'passed', scenarios: ['Chinese IME', 'drafts', 'receipts', 'mute/late audio', 'manual replay', 'speech errors and retry',
    'history reading', 'pagination', 'history errors', 'same-ID retry', 'modal focus', 'failed time setting', 'refresh'], responsive: checked }));
} finally {
  await browser?.close();
  await vite.close();
}
