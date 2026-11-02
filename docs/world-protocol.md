# 世界恢复、玩家记录与存档任务协议

版本：2026-11-02。兼容原 `/history` 数组与文本消息字段。

## 会话边界

每次后端启动和成功读档生成新的 `epoch`。失败读档及回滚后也会使旧临时结果失效。纪元用于阻止迟到内容混入当前状态，不提供任意消息分支或任意时间点数据库重放。

事件信封包含 `epoch`、单调递增的 `event_seq` 和 `state_version`。状态更新递增状态版本；客户端按消息标识去重，并拒绝已退休纪元。未见过的同纪元事件可在乱序时补入；状态与任务另用版本判断，避免旧状态覆盖新状态。

`GET /config` 增加：

```json
{
  "epoch": "opaque-uuid",
  "event_seq": 42,
  "state_version": 3,
  "capabilities": {
    "world_epoch": true,
    "player_records": true,
    "archive_tasks": true
  },
  "experience": {},
  "archive_task": null,
  "world_paused": false,
  "presentation": {},
  "status": { "paused": false, "recovery_required": false }
}
```

`world.snapshot` 在新协议 WebSocket 建连时返回边界，`world.reset` 要求重新恢复。发送输入、取消、手动语音及玩家操作携带 `epoch`；陈旧输入不会进入新世界。旧客户端可继续使用原文本协议。

## 一致恢复

`GET /sync?epoch=...&after=...` 返回：

- `config`：当前状态及任务/活动。
- `boundary`：获取快照时的纪元与事件游标。
- `live_reply`：当前尚未完成回复的文本快照。
- `history`：最近 100 条持久记录。
- `tail`：边界之后的有限事件尾部及覆盖是否完整。

快照边界和流式缓冲在同一协议锁中读取；数据库查询使用独立连接。恢复过程中如果纪元变化或世界处于导入阶段，返回明确的非成功状态，由客户端重试。

客户端清空旧声音、旧轮次缓冲和阅读锚点，合并历史与新事件，再开放输入。不重播音频尾部；音频只接受当前纪元的实时事件或明确发起的重播。事件过多超出 512 条窗口时重新取快照。

## 搜索与来源

- `GET /history/search?q=...&day=YYYY-MM-DD&before_id=...`：每页最多 50 条，返回 `items`、`has_more` 与 `epoch`。关键词按字面匹配，参数绑定，不拼接用户 SQL。
- `GET /history/source/{message_id}`：返回来源消息周围的对话及纪元。来源不存在时不伪造卡片。
- `GET /experience/memories`：最近 50 条模型整理的角色记忆，标记 `model_memory`；不冒充用户收藏或已核验共同经历。

## 玩家记录

`POST /experience` 包含 `action`、`epoch` 及对应参数：

| action | 参数与行为 |
|---|---|
| `favorite` | `message_id`, `enabled`；只收藏当前库可查到的原始消息 |
| `agreement` | `message_id`, `title`, 可选 `when`, `location`；初始待确认 |
| `agreement_status` | `record_id`, `status`；pending/confirmed/completed/cancelled |
| `start` | `duration`（1–240 分钟）, `shared`, 可选 `message_id`；共享模式要求已完成角色回复来源 |
| `pause`, `resume` | 暂停或继续当前现实计时 |
| `complete`, `interrupt` | 结束活动，可附 `summary`；不自动完成 |
| `quiet` | `enabled`；仅控制主动说话 |
| `world_pause` | `enabled`；冻结/恢复世界时钟和自主流程 |

原子保存成功后才广播 `experience.update`。写入失败会撤回内存修改。存读档期间、来源无效或状态不允许时返回可恢复错误。用户整理约定不会自动提升关系值、改写角色记忆或移动角色。

## 存档任务

`POST /api/archive/tasks`：

```json
{
  "operation": "load",
  "slot_name": "checkpoint",
  "description": "optional",
  "epoch": "current-epoch"
}
```

支持 `create`、`load`、`quick-save`、`quick-load`。接受后返回 HTTP 202 和 `task_id`；这只是任务已接受。完成需检查 `status`，不能把 202 当作存档成功。

`GET /api/archive/tasks/current`、`/config.archive_task` 和 WebSocket `archive.task` 均提供最近任务：

```json
{
  "task_id": "opaque-uuid",
  "revision": 17,
  "operation": "load",
  "status": "running",
  "phase": "恢复数据库和知识图谱",
  "can_cancel": false,
  "recovery_required": false,
  "result": null
}
```

任务修订号跨任务递增，防止旧 HTTP/事件覆盖新任务。阶段来自实际管理器步骤；界面不把步骤编号显示成预计耗时百分比。任务开始会阻止新世界操作，等待已接受的对话和记忆写入，再备份、校验、导入及重建。

成功读档在恢复锁内旋转纪元，再释放任务。失败时回滚旧状态；回滚失败保持世界暂停并公开恢复入口。服务异常退出时保留最近任务日志；未确认完成的恢复不会在重启后被改报为成功。

## 测试与兼容

新前端通过 capability 判断是否启用新能力。旧后端仍可聊天；没有真实任务能力时不提供假存读档入口。前后端应配套发布。

可执行的验证包括 `tests/test_player_experience.py`、`tests/test_restore_experience.py`、`frontend/tests/world.test.mjs` 与 `frontend/tests/remaining.browser.mjs`。
