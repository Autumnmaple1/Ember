// Keep one media element per session: browser playback permission is per element.
const graphs = new WeakMap();
const SILENCE = 'data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEARKwAAESsAAABAAgAZGF0YQIAAACAgA==';

function graphFor(audio) {
  let graph = graphs.get(audio);
  if (!graph || graph.context.state === 'closed') {
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!Context) return null;
    let context;
    try { context = new Context(); } catch { return null; }
    graph = { context, analyser: null };
    graphs.set(audio, graph);
  }
  return graph;
}

export function speechAnalyser(audio) {
  const graph = graphFor(audio);
  // Never route audible media through a suspended context: it would become silent.
  if (!graph || graph.context.state !== 'running') return null;
  if (!graph.analyser) {
    try {
      const analyser = graph.context.createAnalyser();
      analyser.fftSize = 512;
      const source = graph.context.createMediaElementSource(audio);
      source.connect(analyser);
      analyser.connect(graph.context.destination);
      graph.analyser = analyser;
    } catch { return null; } // Mouth animation must never break text or playback.
  }
  return graph.analyser;
}

export class SpeechAudioOutput {
  constructor() { this.audio = null; this.generation = 0; }
  element() {
    if (!this.audio) { this.audio = new Audio(); this.audio.preload = 'auto'; }
    return this.audio;
  }
  resume() {
    const audio = this.element();
    const graph = graphFor(audio);
    if (graph) {
      const resumed = graph.context.resume();
      Promise.resolve(resumed).then(() => {
        if (this.audio === audio && graph.context.state === 'running') speechAnalyser(audio);
      }).catch(() => {});
    }
  }
  prepare() {
    const audio = this.element();
    this.resume();
    // A real play() call inside the click/key gesture enables delayed TTS playback.
    // Do not interrupt a current or paused sentence just to prepare permissions.
    const source = audio.getAttribute?.('src') || audio.src;
    if (source && source !== SILENCE) return;
    const generation = ++this.generation;
    audio.src = SILENCE;
    try {
      Promise.resolve(audio.play()).then(() => {
        if (generation === this.generation) audio.pause();
      }).catch(() => {});
    } catch { /* Permission preparation must never block sending text. */ }
  }
  source(source) {
    this.generation += 1;
    const audio = this.element();
    audio.src = source;
    return audio;
  }
  cancelPreparation() { this.generation += 1; }
  reset() {
    this.cancelPreparation();
    if (this.audio) { this.audio.pause(); this.audio.removeAttribute('src'); this.audio.load(); }
  }
  close() {
    this.cancelPreparation();
    if (this.audio) {
      this.audio.pause();
      graphs.get(this.audio)?.context.close().catch(() => {});
      this.audio = null;
    }
  }
}
