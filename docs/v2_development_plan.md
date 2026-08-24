# Ember v2 Development Plan

本文是 Ember v2 的开发计划与工期估算。当前策略是：核心架构尽量重写，旧项目只复用稳定底层能力和已经验证过的实现细节。

## 估算前提

- 第一阶段暂不重做前端。
- 第一阶段暂不重做存档系统。
- 第一阶段暂不重做工具调用系统。
- 第一阶段只保证一个玩家 Agent、一个非玩家 Agent、一个默认 Environment 跑通。
- 旧系统可以作为参考和 fallback，但 v2 核心运行时不继续沿用 `Brain` / `StateManager` 的类结构。
- LLM、embedding、PostgreSQL、Neo4j 的底层连接能力可以复用或轻包装。

## 总体工期估算

如果由熟悉本项目的人单人开发：

```text
MVP 可运行内核:        2 - 3 周
Alpha 多场景/多角色:   4 - 6 周
Beta 记忆与睡眠完善:   7 - 10 周
大版本发布候选:        10 - 14 周
```

如果每天只能业余开发，整体时间通常需要乘以 2 到 3。

最容易拖慢进度的部分：

- LLM 输出结构稳定性。
- 状态更新脏数据处理。
- 记忆迁移和数据库 schema 调整。
- 多 Agent 调度的行为调优。
- 旧前端/API 接入。

## 开发策略

### 重写

这些模块应该重写，保证 v2 简洁：

- `Runtime / MainRoutine`
- `WorldState`
- `AgentState` / `AgentProfile`
- `EnvironmentProfile` / runtime `Environment` / `EnvironmentHistory`
- `Scheduler`
- `ConversationArbiter`
- `PreProcessPipeline`
- `ContextLoadPipeline`
- `GenerateSpeechPipeline`
- `PostProcessPipeline`
- `Versioned StateStore`
- v2 事件命名与任务模型

### 复用或轻包装

这些模块可以复用底层能力：

- `brain/llm_client.py`
- `brain/tag_utils.py`
- `core/event_bus.py`
- `core/heartbeat.py`
- `memory/db_pool.py`
- `memory/neo4j_memory.py`

### 参考后重写/适配

这些模块的思路保留，但不要直接作为 v2 中心：

- `brain/core.py`
- `persona/state_manager.py`
- `memory/short_term.py`
- `memory/episodic_memory.py`
- `memory/memory_process.py`
- `memory/entity_extraction.py`
- `memory/db_memory.py`

## Phase 0: 架构冻结与脚手架

预计：2 - 4 天

目标：

- 冻结第一版 v2 的核心字段和模块边界。
- 建立 v2 包结构。
- 建立最小测试框架。
- 明确 v1 复用 adapter 边界。

交付物：

- `ember_v2/` 包目录。
- 核心 dataclass / pydantic model。
- 最小单元测试。
- 明确 `AgentState` / `EnvironmentProfile` / runtime `Environment` schema。

验收标准：

- 不调用 LLM，也能构造 Runtime、WorldState、Agent、Environment。
- `move_agent`、`apply_agent_patch`、participants 一致性维护有测试。

## Phase 1: 最小世界运行时

预计：4 - 6 天

目标：

- 实现 Runtime、WorldState、EnvironmentHistory、StateStore。
- 支持玩家输入写入当前 EnvironmentHistory。
- 支持 heartbeat tick 进入 Scheduler。

交付物：

- `Runtime` 可启动/关闭。
- 默认 Environment。
- 玩家 Agent。
- 非玩家 Agent。
- EnvironmentHistory append / recent messages。
- 版本化 AgentState 更新。
- runtime Environment 维护 participants 和 history。

验收标准：

- 不接 LLM 时，用 fake pipeline 可以跑完一次 player message。
- 状态版本冲突能正确丢弃旧 patch。
- tick 不会阻塞主线程。

## Phase 2: 对话主链路

预计：5 - 8 天

目标：

- 实现 PreProcess -> Arbiter -> ContextLoad -> GenerateSpeech -> PostProcess 主链路。
- 先使用 fake LLM 跑通，再接入真实 `LLMClient`。
- 支持流式 speech 事件。

交付物：

- `AgentIntent` 结构。
- `SpeechOutput` 结构。
- prompt builder。
- `speech.started` / `speech.chunk` / `speech.finished` 事件。
- PostProcess 生成 AgentState patch。

验收标准：

- 玩家输入后，NPC 能生成一条回复。
- 回复写入 EnvironmentHistory。
- AgentState 能根据本轮对话更新。
- 用 fake LLM 的测试稳定通过。

## Phase 3: idle 行为与调度

预计：4 - 7 天

目标：

- Scheduler 支持 tick 触发 idle candidate。
- PreProcess 支持 tick trigger。
- Arbiter 支持每个 Environment 每轮最多一个 speaker。
- idle 回复后进入同一套 PostProcess。

交付物：

- idle timeout / cooldown 策略。
- tick -> candidate agent -> intent -> speech 的链路。
- 用户输入打断 idle 任务的基础机制。

验收标准：

- NPC 在空闲后可以主动说话。
- 玩家输入可以优先于 idle 发言。
- 过时 idle 状态更新不会覆盖新对话状态。

## Phase 4: 记忆第一版

预计：7 - 12 天

目标：

- 把旧 ShortTermMemory 的角色替换为 EnvironmentHistory。
- 实现 MemoryCoordinator。
- 接入 EpisodicMemoryStore adapter。
- 情景记忆增加 `agent_id` / `environment_id` 作用域。

交付物：

- memory query 接口。
- episode consolidation task。
- 环境历史切片 -> 记忆编码 -> episodic store。
- ContextLoad 可以按 AgentIntent 加载记忆。

验收标准：

- 对话后可以触发记忆整理。
- 后续对话可以检索到相关情景记忆。
- 记忆检索不会阻塞主对话超时太久。

## Phase 5: 睡眠与图谱整理

预计：5 - 10 天

目标：

- 实现 sleep action。
- sleep action 在 v2 第一版中只是记忆整理触发器，不代表角色剧情上睡觉。
- 复用 clarity 衰退逻辑。
- 接入 GraphConsolidationWorker。
- 接入 Neo4j graph store。

交付物：

- sleep task queue。
- episodic clarity 衰退。
- 未整理情景记忆 -> 实体关系抽取 -> Neo4j。
- MemoryCoordinator 合并 graph context。

验收标准：

- sleep 后情景记忆 clarity 会变化。
- 图谱能抽取实体和关系。
- 后续检索能同时返回 episodic 和 graph 信息。

## Phase 6: 多 Environment / 多 Agent

预计：6 - 10 天

目标：

- 支持多个 Environment。
- 支持多个非玩家 Agent。
- 支持场景迁移。
- 支持每个 Environment 独立历史。

交付物：

- create environment。
- move agent。
- participants 一致性维护。
- environment-level arbitration。

验收标准：

- 两个场景的对话历史不会串。
- Agent 迁移场景后只读取新场景历史。
- 一个场景每轮最多一个 Agent 发言。

## Phase 7: API / 前端兼容接入

预计：5 - 10 天

目标：

- 让旧前端能接 v2 最小事件。
- 保持基础聊天、状态展示、TTS。
- 暂不追求完整前端重构。

交付物：

- v2 WebSocket event bridge。
- state update payload compatibility adapter。
- speech event -> old llm event compatibility if needed。
- TTS 接入。

验收标准：

- 前端能看到流式回复。
- 前端能看到状态更新。
- TTS 能拿到 speech text 和 emotion hint。

## Phase 8: 迁移、清理与发布候选

预计：7 - 14 天

目标：

- 迁移旧 state / chat memory / episodic memory。
- 清理旧模块依赖。
- 补测试。
- 写发布说明。

交付物：

- migration script。
- schema version。
- 最小回归测试矩阵。
- v2 README / upgrade notes。

验收标准：

- 旧 `state.json` 可迁移为默认 AgentState。
- 旧 `chat_memory.json` 可迁移为默认 EnvironmentHistory。
- 旧 episodic memory 至少可以兼容读取或迁移。
- `python run_tests.py` 的相关测试通过。

## 推荐里程碑

### Milestone 1: v2-core MVP

范围：

- Phase 0 - Phase 2

预计：

- 2 - 3 周。

结果：

- 一个玩家、一个 NPC、一个场景，能完成完整对话和状态更新。

### Milestone 2: v2-life alpha

范围：

- Phase 3 - Phase 5

预计：

- 4 - 7 周累计。

结果：

- 有 idle 主动行为、情景记忆、睡眠、图谱整理。

### Milestone 3: v2-world beta

范围：

- Phase 6 - Phase 7

预计：

- 7 - 10 周累计。

结果：

- 支持多 Agent、多 Environment，并能接入现有前端。

### Milestone 4: v2 release candidate

范围：

- Phase 8

预计：

- 10 - 14 周累计。

结果：

- 可迁移、可测试、可发布。

## 建议的第一步

先做 Phase 0 和 Phase 1，不碰真实 LLM：

1. 创建 `ember_v2/` 目录。
2. 写 `AgentState`、`EnvironmentProfile`、runtime `Environment`、`WorldState`、`StateStore`。
3. 写 fake pipeline 跑通一次玩家输入。
4. 写版本冲突测试。

这样可以先确认 v2 骨架没有问题，再把 LLM、记忆、睡眠一点点接进来。
