# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Ember (依鸣) is a digital life simulation engine — an AI companion with continuous consciousness, emotional state evolution (PAD model), self-driven heartbeat behavior, hierarchical memory, and persistent save/load. The frontend renders a Live2D avatar with real-time emotion display.

## Commands

### Running the project
- **Full stack**: `run_all.bat` (Windows) — starts Docker containers, backend (port 8000), and frontend (port 5173)
- **Backend only**: `python server.py` (FastAPI + Uvicorn)
- **Frontend only**: `cd frontend && npm run dev`

### Tests
- **All tests**: `python run_tests.py`
- **Verbose**: `python run_tests.py -v`
- **Single suite**: `python run_tests.py -k thread -v` (keyword filter)
- **Stop on first failure**: `python run_tests.py -x`
- **With coverage**: `python run_tests.py --cov`

### Lint
- **Frontend**: `cd frontend && npm run lint` (ESLint)
- **Backend style check**: `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`

## Architecture

### Event-driven core
All components communicate through `core/event_bus.py` (`EventBus`). Key events:
- `user.input` → triggers `Brain._on_user_input` → dialogue processing
- `llm.started` / `llm.chunk` / `llm.finished` → streaming LLM output to WebSocket clients
- `system.tick` → heartbeat-driven idle state evolution
- `user_interaction` → post-dialogue state update (every N turns, configurable via `STATE_UPDATE_INTERVAL`)
- `idle_speak` → AI self-initiated speech when idle
- `state.update` → broadcast new PAD state to frontend
- `memory.preprocess` / `memory.sleep` → memory consolidation triggers

### Module responsibilities
- **`core/`**: EventBus (pub/sub + logical time simulation with configurable acceleration), Heartbeat timer
- **`brain/`**: LLM interaction (`brain/core.py` — dialogue flow, tool call loops), LLM client (`brain/llm_client.py` — OpenAI-compatible streaming), TTS, tag utils
- **`persona/`**: `StateManager` — PAD emotional state, idle evolution, dialogue-triggered state updates. State is persisted to `config/state.json`. The `state_zip` property injects a compact state summary into prompts.
- **`memory/`**: ShortTermMemory (rolling context window), EpisodicMemory, Hippocampus (memory consolidation/judging), DBMemory (PostgreSQL + pgvector for semantic search), Neo4j knowledge graph, entity extraction
- **`tools/`**: Plugin architecture — `registry.py`, `base.py`, `executor.py`, `processor.py`. Built-in: `memory_query_tool.py`. Tool calls are XML-tag-based (not function-calling API).
- **`archive/`**: Full save/load system — JSON + PostgreSQL + Neo4j exporters/importers, compression, validation, gallery management
- **`config/`**: `settings.py` (env-driven config), `prompts.yaml` (character persona + system prompts), `state_default.json` (initial PAD state)

### LLM configuration
Two model tiers via OpenAI-compatible API:
- **LARGE_LLM** (e.g., qwen3.5-plus): dialogue generation
- **SMALL_LLM** (e.g., qwen3.5-flash): state updates, memory judging, entity extraction
- **EMBEDDING_MODEL**: semantic memory vectorization

### Logical time system
`EventBus.logical_now` simulates time with a configurable acceleration factor (`TIME_ACCEL_FACTOR`). All state updates and prompts use logical time, not wall clock time. The factor can be changed at runtime via the API.

### Threading model
- `Brain` uses a lock to serialize dialogue processing (`_is_processing` flag)
- `StateManager` uses a class-level `ThreadPoolExecutor` (2 workers) for async state updates
- `safe_broadcast` bridges sync EventBus callbacks to the async WebSocket loop via `call_soon_threadsafe`

### Frontend (React + Vite)
- `App.jsx`: main chat UI, WebSocket client, emotion radar chart (Recharts), sidebar with timeline/logs
- `Live2DViewer.jsx`: pixi.js + pixi-live2d-display avatar with expression switching
- `ArchiveModal.jsx`: save/load gallery
- No TypeScript — plain JSX

### Data flow
1. User sends message via WebSocket → `server.py` publishes `user.input`
2. `Brain` streams LLM response, publishes chunks → server broadcasts to WebSocket clients
3. After response, `Brain` publishes `user_interaction` → `StateManager` decides whether to update state
4. Heartbeat publishes `system.tick` → `StateManager` checks idle timeout → may trigger idle evolution
5. Idle evolution can trigger `idle_speak` (AI talks on its own) or `memory.sleep` (consolidation)

## Configuration

All runtime config is in `.env` (see `.env.example`). Character persona is in `config/prompts.yaml`. Initial state in `config/state_default.json` (copied to `state.json` on first run).

Databases are managed via Docker Compose: PostgreSQL (pgvector) on 5432, Neo4j on 7687.

## Key conventions

- Python 3.11, async I/O for server, threads for background LLM calls
- Tool calls use XML tags (e.g., `<tool_call name="...">...</tool_call`), not OpenAI function-calling format — processed by `ToolCallProcessor`
- The project language is primarily Chinese (comments, prompts, log messages); code identifiers use English
- CI runs on push to `main`/`develop`: pytest + flake8 + bandit security scan
