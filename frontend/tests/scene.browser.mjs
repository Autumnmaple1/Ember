// Direct UI settings: synthetic APIs only; no production backend or model calls.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';
import { createServer } from 'vite';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vite = await createServer({ root, envDir: false, envPrefix: 'EMBER_SYNTHETIC_',
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') },
  server: { host: '127.0.0.1', port: 0 } });
await vite.listen();
let browser;
try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(10000);
  const errors = [], sent = [];
  page.on('pageerror', error => errors.push(error.message));
  const epoch = 'synthetic-scene-world';
  let seq = 0, version = 0, sceneRequests = 0, failSave = true, failQuiet = true, thinking = false, socket, failReminder = true;
  let state = { P: 5, A: 5, D: 5, '客观情境': '活动待确认', scene: {
    character_location: null, user_location: null, communication_mode: 'unknown', remote_contact_confirmed: false },
    role_continuity: { events: [{ id: 'role-event', at: 1794121200, description: '结束了这一轮自己的笔记整理', origin: 'offline_simulation' },
      { id: 'future-event', at: 1794221200, description: '不应展示的未来事件', origin: 'online_simulation', timeline_status: 'future_snapshot' }] } };
  const experience = { revision: 0, quiet: false, activities: [], projects: [], materials: [], favorites: [], agreements: [{
    id: 'reminder', title: '合成休息约定', status: 'confirmed', notification_enabled: false,
    source: { message_id: 'source', content: '合成原文', role: 'user' } }] };
  const boundary = () => ({ epoch, event_seq: seq, state_version: version });
  const config = () => ({ ...boundary(), state, experience, is_thinking: thinking, logical_time: '2026-11-07 21:30:00',
    capabilities: { world_epoch: true, direct_scene: true, web_proactive: true, player_records: true },
    status: { scene: { ready: true }, database: { ready: true }, long_term_memory: { ready: true } } });
  await page.route('http://localhost:8000/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/auth/session') return route.fulfill({ json: { required: false } });
    if (path === '/config') return route.fulfill({ json: config() });
    if (path === '/sync') return route.fulfill({ json: { config: config(), history: [], boundary: boundary(), tail: { complete: true, events: [] } } });
    if (path === '/scene') {
      sceneRequests++;
      const request = route.request().postDataJSON();
      assert.equal(request.epoch, epoch);
      if (request.scene.communication_mode === 'face_to_face') assert.equal(request.scene.character_location, request.scene.user_location);
      if (failSave) { failSave = false; return route.fulfill({ status: 503, json: { detail: '合成保存失败，请重试' } }); }
      if (JSON.stringify(request.expected_scene) !== JSON.stringify(state.scene)) return route.fulfill({ status: 409,
        json: { detail: '场景已发生变化，请载入最新场景后重新确认' } });
      state = { ...state, scene: request.scene, state_source: 'user_ui' }; version++;
      return route.fulfill({ json: { ...boundary(), state } });
    }
    if (path === '/experience') {
      const request = route.request().postDataJSON();
      if (request.action === 'agreement_reminder') {
        if (failReminder) { failReminder = false; return route.fulfill({ status: 503, json: { detail: '合成提醒保存失败' } }); }
        assert.equal(request.record_id, 'reminder');
        if (request.enabled) assert.equal(request.when, '2026-11-08T16:00');
        experience.agreements[0].notification_enabled = request.enabled;
        if (request.enabled) experience.agreements[0].reminder_at = Date.parse(request.when + ':00+08:00') / 1000;
        experience.revision++;
        return route.fulfill({ json: { experience, world_paused: false, ...boundary() } });
      }
      if (failQuiet) { failQuiet = false; return route.fulfill({ status: 503, json: { detail: '合成权限保存失败，请重试' } }); }
      assert.equal(request.action, 'quiet'); experience.quiet = request.enabled; experience.revision++;
      return route.fulfill({ json: { experience, world_paused: false, ...boundary() } });
    }
    return route.fulfill({ json: [] });
  });
  await page.route('**/models/**', route => route.abort());
  await page.routeWebSocket('**/ws/chat*', ws => { socket = ws; ws.onMessage(raw => sent.push(JSON.parse(raw))); });
  const url = `http://127.0.0.1:${vite.httpServer.address().port}`;
  await page.goto(url); await page.locator('.connection-online').waitFor();
  const draft = page.getByRole('textbox', { name: /对.*说/ });
  await draft.fill('保留这条尚未发送的草稿');
  await page.getByRole('button', { name: '查看当前情境' }).click();
  const dialog = page.getByRole('dialog', { name: '当前情境' });
  await dialog.getByLabel('角色位置', { exact: true }).fill('图书馆');
  await dialog.getByLabel('你的位置', { exact: true }).fill('宿舍');
  await dialog.getByLabel('交流方式', { exact: true }).selectOption('face_to_face');
  const shared = dialog.getByLabel('共同位置（面对面）');
  assert.equal(await shared.inputValue(), '图书馆');
  assert.equal(await dialog.getByLabel('你的位置', { exact: true }).count(), 0);
  await shared.fill('');
  await dialog.getByRole('button', { name: '保存场景', exact: true }).click();
  await dialog.getByRole('alert').filter({ hasText: '共同位置' }).waitFor();
  assert.equal(sceneRequests, 0);
  await shared.fill('图书馆');
  thinking = true;
  await dialog.getByText('当前回复或场景更新尚未结束，填写内容会保留，请稍后保存。').waitFor();
  assert.equal(await shared.isEnabled(), true, 'Background thinking must not freeze scene editing');
  await dialog.getByRole('button', { name: '保存场景', exact: true }).click();
  assert.equal(sceneRequests, 0);
  thinking = false;
  await dialog.getByText('当前回复或场景更新尚未结束，填写内容会保留，请稍后保存。').waitFor({ state: 'hidden' });
  await dialog.getByRole('button', { name: '保存场景', exact: true }).click();
  await dialog.getByRole('alert').filter({ hasText: '合成保存失败' }).waitFor();
  assert.equal(await shared.inputValue(), '图书馆');
  const feedback = await dialog.getByRole('alert').boundingBox();
  assert.ok(feedback.y >= 0 && feedback.y + feedback.height <= 845, 'Save feedback must be visible near the button');
  await dialog.getByRole('button', { name: '保存场景', exact: true }).click();
  await dialog.getByText('场景已保存。', { exact: true }).waitFor();
  await mkdir(resolve(root, 'test-results'), { recursive: true });
  await page.screenshot({ path: resolve(root, 'test-results/scene-direct-mobile.png'), fullPage: true });
  assert.equal(sent.filter(item => item.content).length, 0);

  await dialog.getByLabel('交流方式', { exact: true }).selectOption('unknown');
  await dialog.getByLabel('角色位置', { exact: true }).fill('尚未保存的编辑');
  state = { ...state, scene: { ...state.scene, character_location: '食堂', user_location: null, communication_mode: 'unknown' } };
  version++;
  socket.send(JSON.stringify({ type: 'state_update', state, epoch, event_seq: ++seq, state_version: version }));
  await dialog.getByText('当前场景已有更新，保存前请重新核对。').waitFor();
  await dialog.getByRole('button', { name: '保存场景', exact: true }).click();
  await dialog.getByRole('alert').filter({ hasText: '场景已发生变化' }).waitFor();
  assert.equal(await dialog.getByLabel('角色位置', { exact: true }).inputValue(), '尚未保存的编辑');
  await dialog.getByRole('button', { name: '载入最新场景并替换表单' }).click();
  assert.equal(await dialog.getByLabel('角色位置', { exact: true }).inputValue(), '食堂');
  await page.getByRole('button', { name: '关闭当前情境' }).click();
  assert.equal(await draft.inputValue(), '保留这条尚未发送的草稿');

  await page.getByRole('button', { name: '设置', exact: true }).click();
  const allowed = page.getByLabel('允许网页内主动发言');
  assert.equal(await allowed.isChecked(), true);
  // A failed save can restore the checkbox before Playwright's uncheck
  // postcondition runs; assert the server-confirmed rollback below instead.
  await allowed.click();
  await page.getByRole('alert').filter({ hasText: '合成权限保存失败' }).waitFor();
  assert.equal(await allowed.isChecked(), true, 'Failed preference save restores the confirmed value');
  await allowed.uncheck();
  await page.waitForFunction(() => document.querySelector('dialog input[type=checkbox]')?.disabled === false);
  assert.equal(experience.quiet, true);
  await page.screenshot({ path: resolve(root, 'test-results/proactive-permission-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭设置' }).click();
  await page.getByRole('button', { name: '专注陪伴', exact: true }).click();
  assert.equal(await page.getByLabel('勿扰 · 暂停主动发言').isChecked(), true);
  await page.reload(); await page.locator('.connection-online').waitFor();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  assert.equal(await page.getByLabel('允许网页内主动发言').isChecked(), false);
  await page.getByRole('button', { name: '关闭设置' }).click();
  await page.getByRole('button', { name: '舞台优先', exact: true }).click();
  await page.getByRole('button', { name: '查看当前情境' }).click();
  assert.equal(await page.getByLabel('角色位置', { exact: true }).inputValue(), '食堂');
  assert.equal(await page.getByLabel('交流方式', { exact: true }).inputValue(), 'unknown');
  await page.getByRole('button', { name: '关闭当前情境' }).click();
  await page.getByRole('button', { name: '回忆', exact: true }).click();
  await page.getByRole('button', { name: '约定', exact: true }).click();
  const reminder = page.getByRole('form', { name: '网页提醒：合成休息约定' });
  await reminder.getByLabel('网页提醒时间（世界时间）').fill('2026-11-08T16:00');
  await reminder.getByRole('button', { name: '开启网页提醒' }).click();
  await reminder.getByRole('alert').filter({ hasText: '合成提醒保存失败' }).waitFor();
  assert.equal(await reminder.getByLabel('网页提醒时间（世界时间）').inputValue(), '2026-11-08T16:00');
  await reminder.getByRole('button', { name: '开启网页提醒' }).click();
  await reminder.getByRole('button', { name: '关闭网页提醒' }).waitFor();
  await page.screenshot({ path: resolve(root, 'test-results/reminder-mobile.png'), fullPage: true });
  await reminder.getByRole('button', { name: '关闭网页提醒' }).click();
  await reminder.getByRole('button', { name: '开启网页提醒' }).waitFor();
  await page.getByRole('button', { name: '活动回顾', exact: true }).click();
  await page.getByText('她的近况 · 虚构世界', { exact: true }).waitFor();
  await page.getByText('结束了这一轮自己的笔记整理', { exact: true }).waitFor();
  assert.equal(await page.getByText('不应展示的未来事件', { exact: true }).count(), 0);
  assert.ok(await page.getByText('离线规则推演').count());
  await page.screenshot({ path: resolve(root, 'test-results/role-life-mobile.png'), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks: ['direct scene save', 'validation and failure retry', 'no chat or draft changes',
    'concurrent scene conflict', 'permission and focus silence agree', 'refresh persistence', 'explicit reminder and retry', 'role life provenance', 'mobile layout'] }));
} finally { await browser?.close(); await vite.close(); }
