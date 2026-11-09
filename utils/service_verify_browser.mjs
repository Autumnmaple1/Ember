// Isolated full-stack Compose check. This never uses the business stack or real models.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const execute = promisify(execFile);
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = 'http://127.0.0.1:8086';
const out = resolve('output/service-verify/browser');
const accessCode = (await readFile('output/service-verify/private/owner-code.txt', 'utf8')).trim();
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const errors = [];
const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
await phone.addInitScript(() => localStorage.setItem('autoPlay', 'false'));
const page = await phone.newPage();
page.on('pageerror', error => errors.push(error.message));
page.setDefaultTimeout(25000);
const desktop = await browser.newContext();
const json = async (context, path) => {
  const response = await context.request.get(base + path);
  assert.equal(response.status(), 200, `${path}: ${response.status()}`);
  return response.json();
};
const post = async (context, path, data) => {
  const session = await json(context, '/auth/session');
  const response = await context.request.post(base + path, { data, headers: { 'X-Ember-CSRF': session.csrf } });
  assert.ok(response.ok(), `${path}: ${response.status()}`);
  return response.json();
};
const waitReady = async () => {
  for (let i=0; i<90; i++) {
    try { if ((await phone.request.get(base + '/health/ready', { timeout: 3000 })).ok()) return; } catch { /* restarting */ }
    await new Promise(done => setTimeout(done, 1000));
  }
  throw new Error('Service did not recover');
};
try {
  await waitReady();
  assert.equal((await phone.request.get(base + '/config')).status(), 401);
  assert.equal((await phone.request.post(base + '/experience', { data: {} })).status(), 401);
  await page.goto(base);
  await page.getByRole('heading', { name: '连接依鸣' }).waitFor();
  await page.getByLabel('设备名称').fill('合成手机');
  await page.getByLabel('访问码').fill('synthetic-wrong');
  await page.getByRole('button', { name: '连接这个世界' }).click();
  await page.getByRole('alert').filter({ hasText: '访问码不正确' }).waitFor();
  await page.getByLabel('访问码').fill(accessCode);
  await page.getByRole('button', { name: '连接这个世界' }).click();
  await page.getByText('已连接', { exact: true }).waitFor();
  const cookie = (await phone.cookies()).find(item => item.name === 'ember_session');
  assert.ok(cookie?.httpOnly && cookie.sameSite === 'Strict');
  const phoneId = (await json(phone, '/auth/session')).session_id;
  const response = await desktop.request.post(base + '/auth/login', { data: { access_code: accessCode, label: '合成电脑' } });
  assert.ok(response.ok());
  assert.equal((await desktop.request.post(base + '/experience', { data: {} })).status(), 403);
  const input = page.getByRole('textbox', { name: '对依鸣说' });
  await input.fill('部署合成材料：两次选择可以留下成果。'); await input.press('Enter');
  await page.getByText('这是部署验证的合成回复，消息与活动会保留。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  await page.locator('.message-user').last().getByRole('button', { name: '一起看这段' }).click();
  await page.getByLabel('材料标题').fill('部署合成材料');
  await page.getByRole('button', { name: '确认一起看这段' }).click();
  await page.getByRole('heading', { name: '部署合成材料', exact: true }).waitFor();
  await page.getByRole('button', { name: '关闭接着上次' }).click();
  const before = await json(phone, '/history');
  const config = await json(phone, '/config');
  await page.reload(); await page.getByText('已连接', { exact: true }).waitFor();
  assert.equal((await json(phone, '/history')).length, before.length);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByText('合成手机（当前设备）', { exact: true }).waitFor();
  assert.ok((await json(desktop, '/auth/devices')).items.some(item => item.id === phoneId));
  await page.screenshot({ path: resolve(out, 'phone-service.png'), fullPage: true });
  await post(desktop, `/auth/devices/${phoneId}/revoke`, {});
  await page.getByRole('heading', { name: '连接依鸣' }).waitFor();
  assert.equal(await page.getByRole('region', { name: '聊天记录' }).count(), 0);
  await page.getByLabel('访问码').fill(accessCode);
  await page.getByRole('button', { name: '连接这个世界' }).click();
  await page.getByText('已连接', { exact: true }).waitFor();
  // Graceful process restart, then a real process crash. Both reuse named volumes.
  const compose = ['compose','--env-file','output/service-verify/service.env','-f','compose.service.yml','-f','deploy/compose.verify.yml','-p','ember-service-verify-20261104'];
  await execute('docker', [...compose, 'restart', 'ember'], { timeout: 180000 });
  await waitReady(); await page.reload(); await page.getByText('已连接', { exact: true }).waitFor();
  assert.equal((await json(phone, '/history')).length, before.length);
  assert.ok((await json(phone, '/config')).experience.materials.some(item => item.title === '部署合成材料'));
  try { await execute('docker', ['exec','ember-service-verify-20261104-ember-1','python','-c','import os,signal; os.kill(1,signal.SIGKILL)'], { timeout: 15000 }); } catch { /* PID 1 exit is expected. */ }
  await new Promise(done => setTimeout(done, 2000));
  await waitReady(); await page.reload(); await page.getByText('已连接', { exact: true }).waitFor();
  const restored = await json(phone, '/config');
  assert.notEqual(restored.epoch, config.epoch);
  assert.deepEqual((await json(phone, '/history')).map(item => item.message_id), before.map(item => item.message_id));
  const archives = await json(phone, '/api/archive/list');
  assert.ok(archives.archives.some(item => item.slot_name.startsWith('auto_backup_service_') && item.is_valid));
  assert.ok((await json(desktop, '/auth/session')).authenticated);
  await page.getByRole('button', { name: /^接着上次/ }).click();
  await page.getByRole('heading', { name: '部署合成材料', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.deepEqual(errors, []);
  const result = { passed: true, checks: ['anonymous blocked', 'password login', 'HttpOnly cookie', 'CSRF required',
    'same-origin HTTP/WS', 'synthetic reply persistence', 'shared materials', 'mobile settings', 'device revocation',
    'graceful restart', 'process crash auto restart', 'session/history/material recovery', 'automatic valid backup', 'no page errors'] };
  await writeFile(resolve(out, 'result.json'), JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} catch (error) {
  console.log(JSON.stringify({ pageErrors: errors, visibleText: (await page.locator('body').innerText().catch(() => '')).slice(0, 1500) }));
  await page.screenshot({ path: resolve(out, 'failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally { await browser.close(); }
