// Synthetic world, archive and timer state; never starts a business backend.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';
import { createServer } from 'vite';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vite = await createServer({ root, envDir: false, envPrefix: 'EMBER_SYNTHETIC_',
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') }, server: { host: '127.0.0.1', port: 0 } });
await vite.listen();
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const now = Date.parse('2026-11-02T17:40:00+08:00');
  let epoch = 'world-one', seq = 0, task = null;
  let experience = { version: 1, revision: 0, quiet: false, favorites: [], agreements: [], activities: [], projects: [] };
  const projectReceipts = new Set();
  let failProjectAfterSave = false;
  let history = Array.from({ length: 10 }, (_, index) => ({ id: index + 1, message_id: `source-${index}`, role: index % 2 ? 'ai' : 'user', content: `合成历史 ${index}，一起读书吧。`, timestamp: now + index * 1000, completion_status: 'completed' }));
  const sockets = new Set(), errors = [];
  const emit = data => { const payload = JSON.stringify({ ...data, epoch: data.epoch || epoch, event_seq: ++seq }); sockets.forEach(ws => ws.send(payload)); };
  const config = () => ({ epoch, event_seq: seq, state_version: 1, display_name: '依鸣', timezone: 'Asia/Shanghai', logical_time: '2026-11-02 17:40:00', time_accel_factor: 1,
    capabilities: { world_epoch: true, player_records: true, archive_tasks: true, shared_projects: true, cancel_turn: true, correlated_audio: true, message_receipts: true },
    shared_project: { title: '窗边的一页', subtitle: '共同观察手册 · 合成测试篇', description: '两次选择，一页成果。', boundary: '这是虚构资料推演，不表示现实外出。' },
    turn: null, is_thinking: false, experience, archive_task: task, presentation: { background: 'library', period: 'dusk', emotion: 'normal' },
    state: { scene: { character_location: '图书馆', user_location: null, communication_mode: 'unknown' } },
    status: { scene: { ready: true }, database: { ready: true }, long_term_memory: { ready: true },
      working_memory: { buffered_messages: 4, notes: 2 }, memory_pipeline: { pending_batches: 1, pending_memories: 2, pending_encoding: true, last_error: '合成记忆整理失败，资料已保留' },
      paused: task?.status === 'running' } });
  const setup = async page => {
    page.setDefaultTimeout(12000);
    page.on('pageerror', failure => errors.push(failure.message));
    await page.addInitScript(() => {
      window.audioStarts = 0; window.audioPaused = 0;
      localStorage.setItem('autoPlay', 'true');
      window.Audio = class {
        async play() { if (!this.src?.startsWith('data:audio/wav;base64,UklGRiY')) window.audioStarts += 1; }
        pause() { window.audioPaused += 1; }
        removeAttribute() {} load() {}
      };
    });
    await page.route('**/models/**', route => route.abort());
    await page.route('http://localhost:8000/**', route => {
      const url = new URL(route.request().url());
      const body = route.request().postDataJSON();
      const reply = data => route.fulfill({ json: data });
      if (url.pathname === '/config') return reply(config());
      if (url.pathname === '/sync') return reply({ config: config(), boundary: { epoch, event_seq: seq }, history: [...history].reverse(), tail: { complete: true, events: [] } });
      if (url.pathname === '/history') return reply([...history].reverse());
      if (url.pathname === '/history/search') return reply({ epoch, items: history.filter(x => x.content.includes(url.searchParams.get('q') || '')).slice().reverse(), has_more: false });
      if (url.pathname.startsWith('/history/source/')) return reply({ epoch, items: history });
      if (url.pathname === '/experience' && body) {
        if (body.action.startsWith('project_') && !projectReceipts.has(body.request_id)) {
          if (body.action === 'project_start') experience.projects.push({ id: 'synthetic-page', kind: 'window-page', title: '窗边的一页', version: 0,
            status: 'active', phase: 'route', budget: 3, cards: [], decisions: [], note: '', source: history.find(x => x.message_id === body.message_id), started_at: '2026-11-02 17:40:00',
            view: { heading: '先从哪份材料入手？', choices: [{ id: 'window', label: '从窗边速写开始', hint: '轮廓需要核对', cost: 1 }] } });
          const project = experience.projects.at(-1);
          if (body.action === 'project_choose' && body.choice === 'window') {
            project.phase = 'investigate'; project.version += 1; project.budget = 2;
            project.cards.push({ id: 'first', title: '材料 A · 合成速写', text: '模拟的枝间轮廓', source: '虚构材料', uncertainty: '物种未知' });
            project.decisions.push({ choice: 'window', world_time: '2026-11-02 17:40:00' });
            project.view = { heading: '这一次，怎样取舍？', event: '材料不足，需要做出取舍。', choices: [{ id: 'verify', label: '补充核对', hint: '核对原判断', cost: 2 }] };
          }
          if (body.action === 'project_pause') { project.status = 'paused'; project.version += 1; project.pause_reason = '由你暂时合上'; }
          if (body.action === 'project_resume') { project.status = 'active'; project.version += 1; }
          if (body.action === 'project_choose' && body.choice === 'verify') {
            project.phase = 'compose'; project.version += 1; project.budget = 0;
            project.cards.push({ id: 'second', title: '补充材料 · 合成核对', text: '确认只是枝叶晃动，物种仍未知。', source: '虚构材料', uncertainty: '物种未知' });
            project.decisions.push({ choice: 'verify', world_time: '2026-11-02 17:41:00' });
            project.view = { heading: '把经历整理成一页', edition: '核对版', outcome: '保留证据与未知。', choices: [] };
          }
          if (['project_finish', 'project_edit'].includes(body.action)) {
            project.title = body.title; project.note = body.note; project.version += 1; project.status = 'completed'; project.phase = 'finished';
            project.view = { heading: '这一页已经留下来了', edition: '核对版', outcome: '保留证据与未知。', choices: [] };
          }
          projectReceipts.add(body.request_id);
          experience.revision += 1;
          if (failProjectAfterSave) { failProjectAfterSave = false; return route.fulfill({ status: 503, json: { detail: '合成回执丢失' } }); }
        }
        if (body.action === 'start') experience.activities.push({ id: 'study', status: 'running', kind: 'study', elapsed_seconds: 0, target_seconds: body.duration * 60, shared: body.shared, world_time: '2026-11-02 17:40:00' });
        if (body.action === 'pause') experience.activities.at(-1).status = 'paused';
        if (body.action === 'resume') experience.activities.at(-1).status = 'running';
        if (body.action === 'complete') { experience.activities.at(-1).status = 'completed'; experience.activities.at(-1).summary = body.summary; }
        if (body.action === 'quiet') experience.quiet = body.enabled;
        if (body.action === 'favorite') experience.favorites = body.enabled ? [...experience.favorites, history.find(x => x.message_id === body.message_id)] : experience.favorites.filter(x => x.message_id !== body.message_id);
        if (body.action === 'agreement') experience.agreements.push({ id: 'agreement', title: body.title, source: history.find(x => x.message_id === body.message_id), when: body.when, location: body.location, status: 'pending' });
        if (body.action === 'agreement_status') experience.agreements[0].status = body.status;
        experience.revision += 1;
        emit({ type: 'experience.update', experience });
        return reply({ epoch, experience, world_paused: false });
      }
      if (url.pathname === '/api/archive/list') return reply({ success: true, archives: [{ slot_name: 'checkpoint', is_valid: true, description: '合成存档', logical_time: '2026-10-21 12:00:00' }] });
      if (url.pathname.endsWith('/preview')) return reply({ success: true, manifest: { character_name: '依鸣', logical_time: '2026-10-21 12:00:00', stats: { message_count: 2 }, extra: { scene: { character_location: '宿舍' } } } });
      if (url.pathname === '/api/archive/tasks' && body) {
        task = { task_id: 'task-one', operation: body.operation, status: 'running', phase: '等待当前对话与记忆任务结束' };
        emit({ type: 'archive.task', task }); return reply(task);
      }
      if (url.pathname === '/api/archive/tasks/current') return reply({ task });
      return route.fulfill({ status: 404, json: { detail: 'Unexpected synthetic route ' + url.pathname } });
    });
    await page.routeWebSocket('**/ws/chat*', ws => {
      sockets.add(ws);
      ws.onClose(() => sockets.delete(ws));
      ws.send(JSON.stringify({ type: 'world.snapshot', epoch, event_seq: ++seq }));
      ws.onMessage(raw => {
        const data = JSON.parse(raw);
        if (data.type === 'tts_request') ws.send(JSON.stringify({ type: 'audio', epoch, event_seq: ++seq, request_id: data.request_id, message_id: data.message_id, audio_base64: 'synthetic' }));
      });
    });
  };
  const page = await context.newPage();
  await setup(page);
  const url = `http://127.0.0.1:${vite.httpServer.address().port}`;
  await page.goto(url);
  await page.locator('.message').nth(9).waitFor();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByText('合成记忆整理失败，资料已保留', { exact: true }).waitFor();
  await page.getByText(/中期缓冲 4 条/).waitFor();
  await page.getByText(/记忆整理待办 1 批/).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.scene-library.period-dusk').count(), 1);
  await page.getByRole('button', { name: '专注陪伴', exact: true }).click();
  await page.getByRole('button', { name: '开始自习计时', exact: true }).waitFor();
  assert.equal(experience.activities.length, 0, 'view changes must not start activities');
  await page.getByRole('button', { name: '开始自习计时', exact: true }).click();
  await page.getByRole('button', { name: '暂停计时', exact: true }).click();
  await page.getByRole('button', { name: '继续计时', exact: true }).waitFor();
  await page.getByLabel('可选回顾').fill('实际完成一页合成笔记');
  await page.getByRole('button', { name: '完成自习', exact: true }).click();
  await page.getByText('实际完成一页合成笔记', { exact: true }).waitFor();
  await page.locator('.message').first().getByRole('button', { name: '收藏这条' }).click();
  await page.getByRole('button', { name: '回忆', exact: true }).click();
  await page.getByRole('button', { name: '我的收藏', exact: true }).click();
  await page.getByRole('dialog').getByText('合成历史 0，一起读书吧。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '查看原始对话' }).click();
  await page.locator('.message-highlight').waitFor();
  const reading = page.locator('.conversation-scroll');
  await reading.evaluate(element => { element.scrollTop = 120; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const readingTop = await reading.evaluate(element => element.scrollTop);
  emit({ type: 'message', sender: 'ai', mode: 'replace', message_id: 'while-located', content: '定位后收到的新合成消息', timestamp: now + 12000 });
  await page.getByText('定位后收到的新合成消息', { exact: true }).waitFor();
  assert.ok(Math.abs(await reading.evaluate(element => element.scrollTop) - readingTop) < 3, 'source location must not keep stealing scroll');
  await page.getByRole('button', { name: '回忆', exact: true }).click();
  await page.getByLabel('搜索对白').fill('合成历史 1');
  await page.locator('.record-search').getByRole('button', { name: '搜索对话', exact: true }).click();
  await page.getByRole('button', { name: '记录约定' }).click();
  await page.getByLabel('约定内容').fill('下次一起读书');
  await page.getByRole('button', { name: '保存待确认约定' }).click();
  await page.getByRole('button', { name: '约定', exact: true }).click();
  await page.getByText('时间未知 · 地点未知', { exact: true }).waitFor();
  await page.getByRole('button', { name: '我已核对确认', exact: true }).click();
  await page.getByText('由你确认', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '开始共同试作', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '和依鸣商量', exact: true }).click();
  assert.match(await page.getByRole('textbox', { name: '对依鸣说' }).inputValue(), /虚构资料/);
  const freshConfirmation = { id: 100, message_id: 'fresh-project-confirmation', role: 'ai', content: '可以，一起试做一页手册吧。', timestamp: now + 20000, completion_status: 'completed' };
  history.push(freshConfirmation);
  emit({ type: 'message', sender: 'ai', mode: 'replace', ...freshConfirmation });
  emit({ type: 'message.persisted', message_id: freshConfirmation.message_id });
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  await page.getByLabel('共同试作的对话来源').selectOption(freshConfirmation.message_id);
  await page.getByLabel('我已核对这条回复，双方愿意共同试作').check();
  await page.getByRole('button', { name: '开始共同试作', exact: true }).click();
  failProjectAfterSave = true;
  await page.getByRole('button', { name: /从窗边速写开始/ }).click();
  await page.getByText('合成回执丢失', { exact: true }).waitFor();
  await page.getByRole('button', { name: '重试原操作', exact: true }).click();
  await page.getByRole('heading', { name: '这一次，怎样取舍？' }).waitFor();
  assert.equal(experience.projects[0].cards.length, 1, 'lost receipt retry must not repeat a choice');
  await page.getByRole('dialog').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: resolve(root, 'test-results', 'shared-project-choice.png') });
  await page.getByRole('button', { name: '先合上，下次继续', exact: true }).click();
  await page.reload();
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  await page.getByRole('button', { name: '继续这份手册', exact: true }).click();
  await page.getByRole('button', { name: /补充核对/ }).click();
  await page.getByLabel('这一页的标题').fill('保留疑问的这一页');
  await page.getByLabel('你的页边注（可留空）').fill('这段文字还没有提交');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  assert.equal(await page.getByLabel('你的页边注（可留空）').inputValue(), '这段文字还没有提交');
  await page.getByRole('button', { name: '留下这一页', exact: true }).click();
  await page.getByRole('heading', { name: '这一页已经留下来了' }).waitFor();
  await page.getByLabel('你的页边注（可留空）').fill('下次继续查证');
  await page.getByRole('button', { name: '保存页边注', exact: true }).click();
  await page.getByRole('heading', { name: '这一页已经留下来了' }).waitFor();
  assert.equal(experience.projects[0].note, '下次继续查证');
  await page.getByRole('dialog').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: resolve(root, 'test-results', 'shared-project-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 900 });
  await page.getByRole('dialog').evaluate(element => { element.scrollTop = 0; });
  const bookSize = await page.getByRole('dialog').evaluate(element => [element.clientWidth, element.scrollWidth]);
  assert.ok(bookSize[1] <= bookSize[0] + 1, 'shared page must fit a small screen');
  await page.screenshot({ path: resolve(root, 'test-results', 'shared-project-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.keyboard.press('Escape');
  await page.getByRole('textbox', { name: '对依鸣说' }).fill('旧世界的草稿');
  await page.reload();
  await page.getByRole('textbox', { name: '对依鸣说' }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: '对依鸣说' }).inputValue(), '旧世界的草稿', 'same-world refresh retains its draft');
  await page.getByRole('button', { name: '存档', exact: true }).click();
  await page.getByRole('button', { name: '预览并加载', exact: true }).click();
  await page.getByText('2026-10-21 12:00:00', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: '备份当前进度并加载', exact: true }).click();
  await page.getByText('等待当前对话与记忆任务结束', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog', { name: '存档管理' }).count(), 1, 'unsafe archive steps cannot be dismissed');
  const oldEpoch = epoch;
  epoch = 'world-two'; seq = 0;
  history = [{ id: 1, message_id: 'restored', content: '读档后属于这个世界的对白', role: 'ai', timestamp: now, completion_status: 'completed' }];
  experience = { ...experience, activities: [], favorites: [], agreements: [], projects: [] };
  task = { ...task, status: 'completed', phase: '存档加载完成', result: { success: true } };
  emit({ type: 'world.reset' }); emit({ type: 'archive.task', task });
  await page.getByRole('button', { name: '关闭存档管理', exact: true }).click();
  await page.getByText('读档后属于这个世界的对白', { exact: true }).waitFor();
  await page.getByRole('button', { name: '一起做点事', exact: true }).click();
  assert.equal(await page.getByRole('heading', { name: '这一页已经留下来了' }).count(), 0, 'old project must not survive a world reset');
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('textbox', { name: '对依鸣说' }).inputValue(), '');
  await page.getByRole('button', { name: '取回旧草稿', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: '对依鸣说' }).inputValue(), '旧世界的草稿');
  await page.getByRole('textbox', { name: '对依鸣说' }).fill('');
  emit({ type: 'message', sender: 'ai', mode: 'replace', message_id: 'stale', content: '不应该出现的旧世界对白', epoch: oldEpoch });
  emit({ type: 'audio', audio_base64: 'stale', message_id: 'stale', epoch: oldEpoch });
  assert.equal(await page.getByText('不应该出现的旧世界对白').count(), 0);
  assert.equal(await page.evaluate(() => window.audioStarts), 0);
  await page.locator('.message-ai').first().getByRole('button', { name: '重播这句' }).click();
  await page.getByRole('button', { name: '停止声音', exact: true }).waitFor();
  const second = await context.newPage(); await setup(second); await second.goto(url);
  await second.getByText('读档后属于这个世界的对白', { exact: true }).waitFor();
  emit({ type: 'message', sender: 'ai', mode: 'replace', message_id: 'auto', content: '用于双标签页的合成回复' });
  emit({ type: 'audio', message_id: 'auto', audio_base64: 'synthetic' });
  await second.getByText(/声音由其他标签页播放/).waitFor();
  assert.equal(await second.evaluate(() => window.audioStarts), 0);
  await second.locator('.message-ai').first().getByRole('button', { name: '重播这句' }).click();
  await second.getByRole('button', { name: '停止声音', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => window.audioPaused) > 0);
  await second.close();
  const responsive = [];
  for (const width of [320, 390, 768, 1366, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    for (const view of ['舞台优先', '对话优先', '专注陪伴']) {
      await page.getByRole('button', { name: view, exact: true }).click();
      const dimensions = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth]);
      assert.ok(dimensions[1] <= dimensions[0] + 1, `overflow ${view} ${width}`);
      responsive.push(`${width}:${view}`);
    }
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await mkdir(resolve(root, 'test-results'), { recursive: true });
  await page.screenshot({ path: resolve(root, 'test-results', 'focus-experience.png'), fullPage: true });
  const before = Date.now();
  epoch = 'performance-world'; seq = 0;
  history = Array.from({ length: 1000 }, (_, i) => ({ id: i + 1, message_id: `benchmark-${i}`, role: i % 2 ? 'ai' : 'user', content: `第 ${i} 条合成历史用于阅读性能验证`, timestamp: now + i * 1000, completion_status: 'completed' }));
  emit({ type: 'world.reset' });
  await page.locator('.message').nth(999).waitFor();
  const render1000Ms = Date.now() - before;
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'passed', scenarios: ['focus lifecycle', 'source bookmarks', 'agreement provenance', 'archive phases', 'restore without reload', 'old event isolation', 'epoch drafts', 'cross-tab ownership', '1000 message rendering', 'shared project choices', 'lost project receipt retry', 'project draft and refresh', 'project completion and revision', 'project world isolation'], render1000Ms, responsive }));
} finally { await browser?.close(); await vite.close(); }
