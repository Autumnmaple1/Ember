# Ember v2 Refactor Plan

This document is the living plan for the Ember v2 refactor. It records the
decisions already made during architecture discussion and the open questions
that still need to be resolved before implementation.

The target architecture is described in `docs/v2_architecture.md`.
The development plan and timeline are described in `docs/v2_development_plan.md`.

## Goal

Ember v2 should evolve the current single-companion runtime into a multi-agent,
multi-environment life simulation engine.

The refactor is not only about making the code cleaner. The new version should
preserve the current project's strongest behavior: continuous logical time,
narrative emotional state, idle self-evolution, memory consolidation, and
self-initiated speech. The new architecture should make those behaviors
available per agent and per environment.

Frontend and archive redesign are out of scope for the first architecture pass.
Tool calling is also postponed for now.

## Current System Summary

The current system is event-driven:

```text
user.input
-> Brain
-> LLM streaming
-> ShortTermMemory
-> user_interaction
-> StateManager
-> state.update
```

Idle behavior is driven by heartbeat:

```text
Heartbeat
-> system.tick
-> StateManager idle evolution
-> action_pulse
-> memory.preprocess / memory.sleep / idle_speak
```

Important current modules:

- `core/event_bus.py`: synchronous pub/sub plus logical time.
- `core/heartbeat.py`: background tick publisher.
- `brain/core.py`: user input queue, pre-routing, prompt assembly, LLM streaming,
  tool loop, dialogue completion events.
- `persona/state_manager.py`: current state holder, prompt injection, dialogue
  state update, idle state evolution, sleep and idle-speak triggers.
- `memory/short_term.py`: rolling chat context, `chat_memory.json`, and
  `chat_history.log`.
- `memory/memory_process.py`: Hippocampus, memory preprocessing and unified
  retrieval facade.
- `memory/episodic_memory.py`: PostgreSQL + pgvector episodic memory.
- `memory/db_memory.py`: message and state event log tables.
- `memory/entity_extraction.py` and `memory/neo4j_memory.py`: sleep-time graph
  consolidation and Neo4j storage.

## Problems In The Current Design

Several modules contain multiple responsibilities:

- `StateManager` is state store, state inference pipeline, idle scheduler,
  sleep trigger, idle-speak trigger, and prompt context provider.
- `Brain` is queue manager, pre-router, prompt builder, LLM streamer, tool
  executor, state updater for location/action, and dialogue event publisher.
- `ShortTermMemory` is both prompt context and persistent chat log source.
- `Hippocampus` is memory encoding trigger, vector retrieval facade, and graph
  context merger.

This is manageable for one character, but it will not scale cleanly to multiple
agents and multiple environments.

## v2 Core Concepts

## Latest Architecture Decisions

This section records the current agreed v2 direction after the first
`Ember_v2/models.py` discussion.

- The first version keeps the full dialogue chain for clarity:
  `PreProcess -> Arbiter -> ContextLoad -> GenerateSpeech -> PostProcess`.
  Latency optimization, async prefetch, and tool-call shortcuts are deferred.
- `Agent` can exist as a unified aggregate object, but it should aggregate
  profile, state, runtime metadata, and memory handles. It should not become the
  global orchestrator.
- `AgentState` remains the main dynamic narrative state. It keeps PAD,
  environment id, objective situation, recent trajectory, inner activity,
  recent goal, logical timestamp, and version.
- `EnvironmentProfile` is the stable scene definition: id, name, description.
  The first version should not maintain a separate mutable `scene_state`.
- Runtime `Environment` should hold `EnvironmentProfile`, participants,
  environment history, and version/cursor metadata.
- The first version does not need typed action classes for sleep or scene
  transition. Plain dicts / booleans are acceptable while the fields are still
  changing.
- `sleep_action` is not a character sleep behavior. It is a post-process signal
  to trigger the memory sleep / consolidation toolchain.
- Scene movement is separate from memory sleep. Use a scene transition / scene
  patch action for moving agents between environments.

### MainRoutine / Runtime

The runtime owns system lifecycle:

- Create and hold the event bus.
- Create and hold the heartbeat / runtime clock.
- Start and stop background services.
- Dispatch ticks into the scheduler.

The runtime should not directly decide who speaks, how prompts are built, or how
memory is retrieved.

### Environment

An environment is a scene or situation container, not just a chat room.

The first v2 version separates stable scene description from runtime scene
membership/history.

Stable scene profile:

```json
{
  "environment_id": "env_nju_library_study_room",
  "name": "南京大学鼓楼校区北园图书馆自习室",
  "description": "午后的图书馆自习室，靠窗座位阳光充足，适合安静学习和低声交谈。"
}
```

Runtime environment container:

- `profile`
- `participants`
- `EnvironmentHistory`
- `version` / history cursor metadata

The environment describes where interaction happens and who is present. It does
not maintain a separate first-version `scene_state`. Each agent's
`客观情境` carries that agent's current view of the situation.

In the first v2 version, each environment owns the main conversation history for
that scene. Agents read recent history from their current environment. Private
agent-level noticed logs / working-memory views are deferred.

### Agent

Every character is an agent. The player is also an agent, but the player's
speech/action comes from external input rather than autonomous LLM generation.

An agent should eventually own or reference:

- profile/persona prompt
- current agent state
- current environment id
- short-term or working memory
- episodic memory namespace
- graph memory namespace
- idle timing / scheduling metadata

Storage implementation should not be baked into the Agent model. Agents should
depend on memory store interfaces or handles.

## AgentState Decision

v2 keeps the current narrative state style. Agent state should be directly
readable by humans and directly useful for prompt injection.

The agreed shape is:

```json
{
  "agent_id": "yiming",
  "environment_id": "env_nju_library_study_room",
  "P": 8,
  "A": 6,
  "D": 5,
  "客观情境": "南京大学鼓楼校区北园，午后的图书馆自习室。依鸣坐在靠窗的位置，阳光依旧温暖。对面的人说经常见到她，让她有些意外又好奇，桌上的算法导论暂时被冷落了。",
  "近期综合轨迹": "在图书馆自习了一上午 -> 中午在附近的小食堂吃了碗热汤面 -> 下午继续整理算法课的笔记 -> 与对面的人搭话，对方态度友善 -> 对方回应'图书馆是我家'，对话变得轻松幽默 -> 依鸣追问对方是不是常驻图书馆 -> 对方说经常见到她，让她感到意外 -> 依鸣解释自己可能太专注了没注意到",
  "内心活动": "他说经常见到我？好奇怪，我明明每次来都差不多坐同一个位置，怎么完全没印象……是不是我写代码的时候真的太投入了，周围人都自动屏蔽了？不过他既然这么说，应该确实在这边待挺久的。有点不好意思，感觉像是人家认识我我却不认识人家。下次得多抬头看看周围才行。",
  "近期目标": "问问对方一般什么时候来图书馆、坐哪个区域，搞清楚为什么自己没注意到他，同时自然地了解对方的年级和专业。",
  "对应时间": "2026-03-06 14:22:13",
  "version": 12
}
```

Decisions:

- Keep `P`, `A`, `D` as top-level fields for compatibility with the existing
  PAD model and prompt style.
- Keep Chinese narrative fields because they are the state language of the
  project and work well as prompt context.
- Add `agent_id`, `environment_id`, and `version` as machine fields.
- `environment_id` is singular in v2's first version. One agent belongs to one
  active environment at a time.
- Keep `对应时间` because logical time is central to Ember's behavior.
- Do not add `sleepiness`, `energy`, or other extra scalar state in the first
  version.
- Do not add a separate `current_action` field. Current behavior should stay
  inside `客观情境` and/or `近期综合轨迹`.
- `客观情境` is the objective situation from this agent's perspective. It may
  include part of the environment, but it is still agent-specific.
- The first version does not keep `Environment.scene_state`; scene context is
  mostly carried by `EnvironmentProfile`, environment history, and each
  agent's own `客观情境`.

## Memory Mapping

Current memory concepts should map into v2 like this:

```text
ShortTermMemory
-> EnvironmentHistory in the first version
-> AgentWorkingMemory later, if private perception becomes necessary

chat_history.log -> memory.preprocess -> episodic_memory
-> Episode consolidation pipeline

EpisodicMemory
-> EpisodicMemoryStore, scoped by agent_id and possibly environment_id

Hippocampus
-> MemoryCoordinator / MemoryRetrievalFacade

DBMemory message_list/state_list
-> EventLog / MessageLog / StateLog

EntityExtractionMemory + Neo4jGraphMemory
-> GraphConsolidationWorker + GraphMemoryStore
```

Important behavior to preserve:

- Recent dialogue should feed state updates.
- Dialogue logs should be consolidated into episodic memories.
- Episodic memories should support vector and keyword retrieval.
- Sleep should decay episodic clarity and extract graph entities/relations.
- Memory retrieval should be available as structured context for dialogue and
  state evolution.
- Conversation history is scoped by environment in the first version.

## Planned Dialogue / Update Flow

For each tick or environment update:

```text
Runtime tick
-> Scheduler checks environments
-> Environment checks non-player agents
-> Candidate agents enter pre-processing
-> Pre-processing returns structured intent
-> Environment arbiter chooses one speaker
-> Context providers load required/optional context
-> Dialogue prompt is built
-> LLM generates speech and TTS-facing output
-> Environment receives update
-> Post-processing updates state, memory, sleep, and scene transition
```

Player input:

```text
player message
-> player's current environment receives update
-> relevant agents enter pre-processing
-> arbiter chooses responder
-> dialogue generation
-> post-processing
```

## Overall v2 Runtime Logic

The first v2 version should run as a small world loop:

```text
Runtime
-> owns EventBus, Clock, Heartbeat, Scheduler
-> owns WorldState

WorldState
-> environments: Environment[]
-> agents: AgentState[]

Environment
-> EnvironmentProfile
-> participants
-> EnvironmentHistory

Agent
-> persona/profile
-> AgentState
-> memory namespace / memory handle
-> current environment_id
```

The main user-driven path:

```text
External player input
-> create/update player Agent event
-> append message to current EnvironmentHistory
-> mark Environment as updated
-> Scheduler asks agents in that Environment whether they should respond
-> PreProcess returns AgentIntent candidates
-> Arbiter picks one speaker
-> ContextLoad gathers environment history, AgentState, EnvironmentProfile, and
   optional memory
-> GenerateSpeech streams response
-> append response to EnvironmentHistory
-> PostProcess updates AgentState and emits optional memory/scene actions
-> optional memory consolidation task
```

The idle path:

```text
Heartbeat tick
-> Scheduler checks environments and non-player agents
-> eligible idle agents enter PreProcess
-> if an agent wants to speak, Arbiter picks speaker per Environment
-> GenerateSpeech
-> PostProcess
```

The sleep / memory path:

```text
PostProcess decides whether to trigger memory sleep / consolidation
-> episode consolidation reads environment dialogue slices
-> episodic memories are stored with agent_id and environment_id
-> sleep consolidation decays episodic memory clarity
-> graph consolidation extracts entities and relations
```

In v2 first-version terminology, sleep is a memory-system action, not a
character behavior. It can be triggered by post-processing, but it should be
executed by memory workers.

The old system's "action_pulse" idea should survive as structured post-process
actions, not as an opaque blob embedded in StateManager.

## Reuse Strategy From v1

The refactor should reuse stable capabilities, but not copy the current
module boundaries directly.

### Reuse Mostly As-Is

- `core/event_bus.py`: the basic pub/sub model and logical time behavior are
  valuable. v2 may later add async or typed events, but the current EventBus is
  a usable starting point.
- `core/heartbeat.py`: usable as the first heartbeat implementation. It should
  eventually call the v2 scheduler rather than directly depending on
  StateManager behavior.
- `brain/llm_client.py`: keep as the LLM and embedding client boundary.
- `brain/tag_utils.py`: keep for thought/speech extraction and output cleanup.
- `memory/db_pool.py`: keep the PostgreSQL connection helper.
- `memory/neo4j_memory.py`: much of the Neo4j store implementation can be
  reused behind a v2 `GraphMemoryStore`.

### Reuse Through Adapters

- `memory/episodic_memory.py`: the table and vector retrieval logic are useful,
  but v2 needs `agent_id` and likely `environment_id` scoping. Wrap the current
  behavior first, then migrate schema.
- `memory/memory_process.py` / `Hippocampus`: reuse the retrieval facade idea,
  but split it into `MemoryCoordinator`, `EpisodicMemoryStore`, and
  `GraphMemoryStore`.
- `memory/entity_extraction.py`: reuse the sleep-time graph consolidation
  workflow, but make it a worker fed by v2 memory tasks.
- `memory/db_memory.py`: reuse the idea of message/state logs, but rename and
  reshape it into `EventLog`, `MessageLog`, and `StateLog` with environment and
  agent ids.
- `memory/short_term.py`: reuse the message append/truncate/save behavior, but
  change the owner from global singleton to `EnvironmentHistory`.
- `persona/state_manager.py`: reuse prompt concepts such as `state_zip`, idle
  evolution prompts, dialogue state update prompts, and the "action_pulse"
  concept. Do not reuse the class shape directly.
- `brain/core.py`: reuse the dialogue lessons: queueing, streaming events,
  pre-routing idea, and prompt assembly. Do not keep it as the central v2
  orchestrator.

### Do Not Reuse Directly As Architecture

- Global `settings.STATE` as the only state source. v2 needs per-agent state.
- Global `config/chat_memory.json` as the only conversation history. v2 needs
  per-environment history.
- Global `config/chat_history.log` as the only memory consolidation source.
  v2 should consolidate selected environment dialogue slices.
- Direct calls like `state_manager._update_state(...)` from Brain. v2 should
  apply state patches through a version-checked state store.
- State updates that mix agent state, environment state, UI background, and
  memory decisions in one dict.

### New Code Needed

- `Runtime` / `MainRoutine`
- `WorldState`
- `EnvironmentProfile`, runtime `Environment`, and `EnvironmentHistory`
- `AgentProfile` and `AgentState`
- `Scheduler`
- `ConversationArbiter`
- `PreProcessPipeline`
- `ContextLoadPipeline`
- `GenerateSpeechPipeline`
- `PostProcessPipeline`
- version-checked state stores
- v2 memory namespace and migration layer

## Pre-Processing Direction

The old `PRE_ROUTING_PROMPT` becomes a more general agent pre-processing stage.
It should decide:

- whether the agent wants to speak
- priority / urgency
- whether memory retrieval is needed
- memory query parameters
- whether external search is needed later
- what broad speech intent is being pursued

The output should be structured, not free-form prose.

Tool calling is postponed, but this stage should be designed so later context
providers can plug into it.

## Post-Processing Direction

The old `StateManager` idle and dialogue state updates become post-processing
pipelines.

Post-processing should output structured patches:

```json
{
  "agent_state_patch": {},
  "memory_actions": [],
  "sleep_action": false,
  "scene_patch": null
}
```

Current model direction:

- `agent_state_patch` updates PAD and narrative AgentState fields.
- `memory_actions` represent ordinary memory writes or consolidation requests.
- `sleep_action` is a boolean trigger for the memory sleep toolchain.
- `scene_patch` / scene transition describes agent movement or environment
  creation/update work. It is separate from sleep.

Only patches with a compatible `version` should be applied. Stale state updates
must be discarded.

## Dirty Data / Concurrency

v2 needs versioned mutable state:

- AgentState has `version`.
- Runtime Environment / EnvironmentHistory should have version or cursor
  metadata where needed.
- State updates carry the base version they were generated from.
- If an async LLM result returns after the state has moved on, it should be
  discarded or transformed into a new update task.

This is especially important for:

- dialogue state updates
- idle evolution
- sleep processing
- scene transitions
- memory consolidation

## First Implementation Slice

The first runnable slice should be intentionally small:

- one runtime
- one default environment
- one player agent
- one non-player agent
- player input triggers NPC response
- tick can trigger NPC idle intent
- AgentState updates after dialogue/idle
- EnvironmentHistory records messages
- no frontend redesign
- no archive redesign
- no tool-call redesign
- no full graph migration yet

## Open Questions

These are the next points to resolve:

1. What is the minimum structured output for pre-processing intent?
2. What are the exact stages of the post-processing pipeline?
3. How should old `state.json`, `chat_memory.json`, and existing database tables
   migrate into v2 schemas?
