import test from 'node:test';
import assert from 'node:assert/strict';
import { SpeechAudioOutput, speechAnalyser } from '../src/speechAudio.js';
import { PlaybackOwner } from '../src/playbackOwner.js';

test('permission preparation reuses the player and cannot pause a later sentence', async () => {
  const original = globalThis.Audio;
  let finishPrime, created = 0, pauses = 0;
  globalThis.Audio = class {
    constructor() { created++; }
    getAttribute() { return this.src; }
    play() { return new Promise(resolve => { finishPrime = resolve; }); }
    pause() { pauses++; }
  };
  const output = new SpeechAudioOutput();
  try {
    output.prepare();
    const first = output.source('data:audio/mp3;base64,first');
    finishPrime(); await Promise.resolve();
    assert.equal(pauses, 0);
    assert.equal(output.source('data:audio/mp3;base64,second'), first);
    assert.equal(created, 1);
  } finally { output.close(); globalThis.Audio = original; }
});

test('suspended lip-sync context never captures the audible media path', () => {
  const original = globalThis.AudioContext;
  let routed = 0;
  globalThis.AudioContext = class {
    state = 'suspended';
    createMediaElementSource() { routed++; }
  };
  try { assert.equal(speechAnalyser({}), null); assert.equal(routed, 0); }
  finally { globalThis.AudioContext = original; }
});

test('HTTP without Web Locks can autoplay with a lease and excludes another tab', async () => {
  const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const oldStorage = globalThis.localStorage;
  const data = new Map();
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  globalThis.localStorage = { getItem: key => data.get(key) || null,
    setItem: (key,value) => data.set(key,value), removeItem: key => data.delete(key) };
  const first = new PlaybackOwner('synthetic-http', () => {});
  const second = new PlaybackOwner('synthetic-http', () => {});
  try {
    assert.equal(await first.claim(false), true);
    assert.equal(await second.claim(false), false);
    first.release();
    assert.equal(await second.claim(false), true);
    second.close();
    assert.equal(await second.claim(false), false);
  } finally {
    first.close(); second.close();
    if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator);
    else delete globalThis.navigator;
    if (oldStorage) globalThis.localStorage = oldStorage;
    else delete globalThis.localStorage;
  }
});
