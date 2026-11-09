// Real media elements and valid WAV data; no TTS provider or business API is used.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vite = await createServer({ root: project, envDir: false, envPrefix: 'EMBER_SYNTHETIC_',
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('http://localhost:8000') },
  server: { host: '127.0.0.1', port: 0 } });
await vite.listen();
const wav = Buffer.alloc(44 + 16000 * 2 * 3);
wav.write('RIFF'); wav.writeUInt32LE(wav.length-8,4); wav.write('WAVEfmt ',8);
wav.writeUInt32LE(16,16); wav.writeUInt16LE(1,20); wav.writeUInt16LE(1,22);
wav.writeUInt32LE(16000,24); wav.writeUInt32LE(32000,28); wav.writeUInt16LE(2,32);
wav.writeUInt16LE(16,34); wav.write('data',36); wav.writeUInt32LE(wav.length-44,40);
for (let index=0; index<(wav.length-44)/2; index++) wav.writeInt16LE(Math.round(200 * Math.sin(index * 2*Math.PI*220/16000)),44+index*2);
const sample = wav.toString('base64');
let browser;
try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    args: ['--autoplay-policy=document-user-activation-required', '--disable-features=MediaEngagementBypassAutoplayPolicies',
      '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const errors = [];
  const setup = async (enabled, noLocks, blockFirst = false) => {
    const context = await browser.newContext();
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ enabled, noLocks, blockFirst }) => {
      localStorage.setItem('autoPlay', JSON.stringify(enabled));
      if (noLocks) Object.defineProperty(Navigator.prototype, 'locks', { value: undefined, configurable: true });
      const NativeAudio = window.Audio;
      window.speechStarts = 0; window.primePlays = 0; window.mediaElements = [];
      window.Audio = function(source) {
        const audio = source === undefined ? new NativeAudio() : new NativeAudio(source);
        const play = audio.play.bind(audio);
        audio.play = () => {
          if (audio.src.startsWith('data:audio/wav;base64,UklGRiY')) window.primePlays++;
          if (blockFirst && audio.src.length > 1000) {
            blockFirst = false;
            return Promise.reject(new DOMException('Synthetic browser policy block', 'NotAllowedError'));
          }
          return play();
        };
        window.mediaElements.push(audio);
        audio.addEventListener('playing', () => { if (audio.src.length > 1000) window.speechStarts++; });
        return audio;
      };
    }, { enabled, noLocks, blockFirst });
    await page.route('http://localhost:8000/**', route => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({ json: path === '/auth/session' ? { required: false } : path === '/config' ? {
        display_name: '依鸣', logical_time: '2026-11-07 18:00:00', is_thinking: false,
        state: { scene: { communication_mode: 'unknown' } }, capabilities: { correlated_audio: true },
        status: { database: { ready: true }, scene: { ready: true }, long_term_memory: { ready: true } },
      } : [] });
    });
    await page.route('**/models/**', route => route.abort());
    let socket, sequence = 0, manualRequests = 0;
    const reply = () => {
      const id = `speech-${++sequence}`;
      socket.send(JSON.stringify({ type: 'message', sender: 'ai', mode: 'replace', message_id: id, content: `合成音频验证 ${sequence}` }));
      socket.send(JSON.stringify({ type: 'audio', message_id: id, format: 'wav', audio_base64: sample }));
    };
    await page.routeWebSocket('**/ws/chat*', ws => {
      socket = ws;
      ws.onMessage(raw => {
        const message = JSON.parse(raw);
        if (message.type === 'tts_request') {
          manualRequests++;
          ws.send(JSON.stringify({ type: 'audio', request_id: message.request_id, message_id: message.message_id, format: 'wav', audio_base64: sample }));
        } else if (message.content) setTimeout(reply, 150);
      });
    });
    await page.goto(`http://127.0.0.1:${vite.httpServer.address().port}`);
    await page.locator('.connection-online').waitFor();
    return { context, page, reply, manualRequests: () => manualRequests };
  };
  const blocked = await setup(true, false, true);
  blocked.reply();
  try { await blocked.page.getByRole('button', { name: '启用声音并播放', exact: true }).click(); }
  catch (error) {
    console.log(await blocked.page.evaluate(() => ({ text: document.body.innerText.slice(-1700), starts: window.speechStarts,
      activation: navigator.userActivation.hasBeenActive, audio: window.mediaElements.map(audio => ({ paused: audio.paused, error: audio.error?.message })) })));
    throw error;
  }
  await blocked.page.waitForFunction(() => window.speechStarts === 1);
  await blocked.page.waitForFunction(async () => {
    const { speechAnalyser } = await import('/src/speechAudio.js');
    const analyser = speechAnalyser(window.mediaElements[0]);
    if (!analyser) return false;
    const samples = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(samples);
    return samples.some(value => value > 0);
  });
  assert.equal(blocked.manualRequests(), 0, 'Recovery plays cached audio without another TTS request');
  await blocked.page.getByRole('button', { name: '停止声音', exact: true }).click();
  blocked.reply();
  await blocked.page.waitForFunction(() => window.speechStarts === 2);
  assert.equal(await blocked.page.evaluate(() => window.mediaElements.length), 1);
  await blocked.context.close();

  const lan = await setup(false, true);
  await lan.page.getByRole('button', { name: '自动声音：关', exact: true }).click();
  assert.equal(await lan.page.evaluate(() => window.primePlays), 1, 'Enabling sound primes the media element inside the gesture');
  const input = lan.page.getByRole('textbox', { name: '对依鸣说' });
  await input.fill('合成自动播放验证'); await input.press('Enter');
  await lan.page.waitForFunction(() => window.speechStarts === 1);
  assert.equal(await lan.page.evaluate(() => navigator.locks), undefined);
  await lan.page.getByRole('button', { name: '自动声音：开', exact: true }).click();
  lan.reply();
  await new Promise(resolve => setTimeout(resolve,300));
  assert.equal(await lan.page.evaluate(() => window.speechStarts), 1, 'Muting drops new automatic speech');
  await lan.page.locator('.message-ai').last().getByRole('button', { name: '重播这句' }).click();
  await lan.page.waitForFunction(() => window.speechStarts === 2);
  assert.equal(lan.manualRequests(), 1);
  await lan.context.close();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks: ['policy rejection with real media recovery', 'audible graph has nonzero signal', 'cached audio without TTS regeneration',
    'reused media element', 'automatic speech without Web Locks', 'mute and manual replay'] }));
} finally { await browser?.close(); await vite.close(); }
