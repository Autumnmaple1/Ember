// Visual QA only: synthetic facts/dialogue with the real bundled Live2D assets.
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve('vite')));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const server = await createServer({ root, envDir: false, envPrefix: 'EMBER_SYNTHETIC_', define: {
  'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') }, server: { host: '127.0.0.1', port: 0 } });
await server.listen();
let browser;
try {
  browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}),
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const history = [{ id: 1, message_id: 'visual-user', role: 'user', content: '今天想按自己的节奏读几页书。', timestamp: 1793602800000 },
    { id: 2, message_id: 'visual-reply', role: 'ai', content: '好呀。你先慢慢读，有问题随时说。', timestamp: 1793602810000 }];
  let background = 'library', period = 'day';
  const places = { library: '图书馆', dorm: '宿舍', campus: '校园室外' };
  const config = () => ({ epoch: `visual-${background}-${period}`, event_seq: 0, state_version: 0, display_name: '依鸣', timezone: 'Asia/Shanghai',
    logical_time: '2026-11-02 ' + ({ day: '15:00:00', dusk: '18:00:00', night: '22:00:00' })[period], time_accel_factor: 1, turn: null,
    capabilities: { world_epoch: true, player_records: true, archive_tasks: true, correlated_audio: true },
    state: { scene: { character_location: places[background], user_location: null, communication_mode: 'unknown' } },
    experience: { version: 1, revision: 0, quiet: true, favorites: [], agreements: [], activities: [{ id: 'visual-focus', kind: 'study', status: 'running', elapsed_seconds: 480, target_seconds: 1500, shared: false }] },
    presentation: { background, period, emotion: 'normal' }, status: { scene: { ready: true }, database: { ready: true }, long_term_memory: { ready: true }, paused: false } });
  await page.route('http://localhost:8000/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path === '/config' ? config() : path === '/sync' ? {
      config: config(), history: [...history].reverse(), boundary: { epoch: config().epoch, event_seq: 0 }, tail: { complete: true, events: [] }
    } : path === '/history' ? [...history].reverse() : [] });
  });
  await page.routeWebSocket('**/ws/chat*', ws => ws.send(JSON.stringify({ type: 'world.snapshot', epoch: config().epoch, event_seq: 0 })));
  const output = resolve(root, 'test-results'); await mkdir(output, { recursive: true });
  const address = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (background of ['library', 'dorm', 'campus']) {
    for (period of ['day', 'dusk', 'night']) {
      await page.goto(address);
      await page.getByRole('button', { name: '舞台优先', exact: true }).click();
      await page.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor({ timeout: 40000 });
      await page.screenshot({ path: resolve(output, `scene-${background}-${period}.png`), fullPage: true });
    }
  }
  background = 'library'; period = 'day'; await page.goto(address);
  await page.locator('.live2d-viewer[data-renderer-state="ready"]').waitFor({ timeout: 40000 });
  await page.getByRole('button', { name: '专注陪伴', exact: true }).click();
  await page.getByRole('button', { name: '暂停计时', exact: true }).waitFor();
  await page.screenshot({ path: resolve(output, 'focus-live2d.png'), fullPage: true });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: resolve(output, 'focus-dark.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(output, 'focus-mobile-live2d.png'), fullPage: true });
  console.log(JSON.stringify({ result: 'captured', sceneVariants: 9, focusVariants: 3 }));
} finally { await browser?.close(); await server.close(); }
