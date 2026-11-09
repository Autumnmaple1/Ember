// Actual local HTTP/WS plus production player rules; only synthetic records.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.EMBER_DEMO_URL || 'http://127.0.0.1:8765';
const out = resolve('output/continuity-20261103/browser');
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
let page;
try {
  page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByText('已连接', { exact: true }).waitFor();
  const input = page.getByRole('textbox', { name: '对依鸣说' });
  const material = '合成题目，第一段只有一个条件。\n第二段：条件是 a > 0。没有提供其他书页。';
  await input.fill(material); await input.press('Enter');
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  await page.locator('.message-user').last().getByRole('button', { name: '一起看这段', exact: true }).click();
  await page.getByLabel('材料标题').fill('合成证明第二段');
  await page.getByLabel('共同查看的原文').fill('条件是 a > 0。');
  await page.getByRole('button', { name: '确认一起看这段', exact: true }).click();
  await page.getByRole('heading', { name: '合成证明第二段', exact: true }).waitFor();
  assert.equal(await page.locator('.shared-material-text').innerText(), '条件是 a > 0。');
  await page.getByRole('button', { name: '关闭接着上次' }).click();
  await input.fill('还没发出的个人草稿');
  await page.getByRole('button', { name: /^接着上次/ }).click();
  await page.getByRole('button', { name: '围绕这段继续聊' }).click();
  assert.ok((await input.inputValue()).startsWith('还没发出的个人草稿\n我们接着看'));
  await page.reload();
  await page.getByText('已连接', { exact: true }).waitFor();
  await page.getByRole('button', { name: /^接着上次/ }).click();
  assert.equal(await page.locator('.shared-material-text').innerText(), '条件是 a > 0。');
  await page.getByRole('button', { name: '先收起这段' }).click();
  await page.getByText('之前一起看过的材料', { exact: true }).click();
  let fail = true;
  await page.route('**/experience', async route => {
    if (fail && route.request().postDataJSON()?.action === 'material_focus') {
      fail = false; return route.fulfill({ status: 503, json: { detail: '合成断连，材料未切换' } });
    }
    return route.continue();
  });
  await page.getByRole('button', { name: '重新一起看这段' }).last().click();
  await page.getByRole('alert').filter({ hasText: '合成断连' }).waitFor();
  assert.equal(await page.locator('.shared-material-text').count(), 0);
  await page.getByRole('button', { name: '重新一起看这段' }).last().click();
  await page.locator('.shared-material-text').waitFor();
  await page.screenshot({ path: resolve(out, 'material.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭接着上次' }).click();
  await page.getByRole('button', { name: '存档', exact: true }).click();
  await page.getByRole('button', { name: '快速存档', exact: true }).click();
  await page.getByText('展示快照已保存', { exact: false }).waitFor();
  await page.getByRole('button', { name: '关闭存档管理' }).click();
  await page.getByRole('button', { name: /^接着上次/ }).click();
  await page.getByRole('button', { name: '先收起这段' }).click();
  await page.getByRole('button', { name: '关闭接着上次' }).click();
  await page.getByRole('button', { name: '存档', exact: true }).click();
  await page.getByRole('button', { name: '快速读档 · 先预览', exact: true }).click();
  await page.getByRole('button', { name: '备份当前进度并加载', exact: true }).click();
  await page.getByText('展示快照已恢复', { exact: false }).waitFor();
  await page.getByRole('button', { name: '关闭存档管理' }).click();
  await page.getByRole('button', { name: /^接着上次/ }).click();
  await page.locator('.shared-material-text').waitFor();
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 900 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `overflow at ${width}`);
    const bounds = await page.getByRole('dialog', { name: '接着上次', exact: true }).boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.getByRole('button', { name: '查看材料来源' }).click();
  await page.locator('.message-highlight').waitFor();
  assert.ok((await page.locator('.message-highlight').innerText()).includes('第二段：条件是 a > 0'));
  // Seed a user-confirmed record to check the return shortcut opens the right tab.
  let config = await (await page.request.get(base + '/config')).json();
  const focused = config.experience.materials.find(item => item.status === 'focused');
  let response = await page.request.post(base + '/experience', { data: { epoch: config.epoch, action: 'agreement',
    message_id: focused.source.message_id, title: '合成约定 时间未定' } });
  const agreement = (await response.json()).experience.agreements.at(-1);
  await page.request.post(base + '/experience', { data: { epoch: config.epoch, action: 'agreement_status', record_id: agreement.id, status: 'confirmed' } });
  await page.reload();
  await page.getByText('已连接', { exact: true }).waitFor();
  await page.getByRole('button', { name: /^接着上次/ }).click();
  await page.locator('article').filter({ has: page.getByRole('heading', { name: '合成约定 时间未定' }) }).getByRole('button', { name: '打开已有记录' }).click();
  assert.equal(await page.getByRole('button', { name: '约定', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.deepEqual(errors, []);
  const result = { passed: true, checks: ['persisted source selection', 'verbatim material', 'draft preservation',
    'refresh persistence', 'put away/refocus', 'HTTP failure/retry', 'archive world restore',
    '320/390/768 layouts', 'source navigation', 'agreement shortcut', 'no page errors'] };
  await writeFile(resolve(out, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} catch (error) {
  if (page) await page.screenshot({ path: resolve(out, 'failure.png'), fullPage: true });
  throw error;
} finally { await browser.close(); }
