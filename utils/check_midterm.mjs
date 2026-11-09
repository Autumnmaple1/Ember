// Real local HTTP/WS and the packaged production UI. Synthetic data only.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.EMBER_DEMO_URL || 'http://127.0.0.1:8765';
const out = resolve(process.env.EMBER_DEMO_QA || 'output/midterm/qa');
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const failures = [];
let page;
try {
  page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(base);
  await page.getByRole('note').filter({ hasText: '中期展示模式' }).waitFor();
  await page.getByText('已连接', { exact: true }).waitFor();
  const input = page.getByRole('textbox', { name: '对依鸣说' });
  await input.fill('要不要一起试做观察手册？');
  await input.press('Enter');
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  await page.locator('.message-ai').last().getByText('【预设台词】', { exact: false }).waitFor();
  await page.screenshot({ path: resolve(out, '01-stage.png'), fullPage: true });
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  await page.locator('.project-start').waitFor();
  if (!(await page.locator('.project-start').getAttribute('open')) && !(await page.getByLabel('共同试作的对话来源').isVisible())) await page.locator('.project-start summary').click();
  await page.getByLabel('共同试作的对话来源').selectOption({ index: 1 });
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '开始共同试作', exact: true }).click();
  await page.getByRole('button', { name: /从窗边速写开始/ }).click();
  await page.getByRole('button', { name: /补充核对/ }).click();
  await page.getByLabel('这一页的标题').fill('还不能命名的轮廓');
  await page.getByLabel('你的页边注（可留空）').fill('证据不足时先保留问题。下次继续核对。');
  await page.getByRole('button', { name: '留下这一页', exact: true }).click();
  await page.getByRole('button', { name: '保存页边注', exact: true }).waitFor();
  await page.screenshot({ path: resolve(out, '02-project.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭一起做点事', exact: true }).click();
  await page.getByRole('button', { name: '回忆', exact: true }).click();
  await page.getByRole('button', { name: '搜索对话', exact: true }).last().click();
  await page.getByText('要不要一起试做观察手册？', { exact: true }).last().waitFor();
  await page.screenshot({ path: resolve(out, '03-history.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭回忆', exact: true }).click();
  await page.getByRole('button', { name: '存档', exact: true }).click();
  const slot = `中期验证-${Date.now()}`;
  await page.getByLabel('存档名称').fill(slot);
  await page.getByRole('button', { name: '创建新存档', exact: true }).click();
  await page.getByText('展示快照已保存', { exact: false }).waitFor();
  await page.getByRole('button', { name: '关闭存档管理' }).click();
  await input.fill('我要离开图书馆了');
  await input.press('Enter');
  await page.getByRole('button', { name: '发送', exact: true }).waitFor();
  await page.getByRole('button', { name: '存档', exact: true }).click();
  await page.locator('article').filter({ has: page.getByRole('heading', { name: slot, exact: true }) }).getByRole('button', { name: '预览并加载', exact: true }).click();
  await page.getByRole('button', { name: '备份当前进度并加载', exact: true }).click();
  await page.getByText('展示快照已恢复', { exact: false }).waitFor();
  await page.screenshot({ path: resolve(out, '04-archive.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭存档管理' }).click();
  await page.reload();
  await page.getByText('已连接', { exact: true }).waitFor();
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  assert.equal(await page.getByLabel('这一页的标题').inputValue(), '还不能命名的轮廓');
  await page.getByRole('button', { name: '关闭一起做点事', exact: true }).click();
  for (const width of [390, 768, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `overflow at ${width}`);
    await page.screenshot({ path: resolve(out, `layout-${width}.png`), fullPage: true });
  }
  assert.deepEqual(failures, []);
  const result = { passed: true, base, checks: ['same-origin HTTP/WS', 'scripted streaming and receipts',
    'real shared project rules', 'editable result', 'history search', 'snapshot restore', 'refresh persistence',
    '390/768/1366 layout', 'no page errors'], pageErrors: failures };
  await writeFile(resolve(out, 'browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} catch (error) {
  if (page) { await page.screenshot({ path: resolve(out, 'failure.png'), fullPage: true }); console.log(await page.locator('.ember-dialog').innerText().catch(() => 'no dialog')); }
  throw error;
} finally { await browser.close(); }
