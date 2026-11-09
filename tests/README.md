# Ember 验证说明

最新（2026-11-08）：对话重整完整后端回归 **362 项**、末次时钟与睡眠边界定向 **66 项**、前端单元 **32 项**通过；五组浏览器、lint 与构建通过。新增 `test_role_continuity.py` 覆盖情绪事件、活动、记忆编码与真实测试向量库召回、投递预算、提醒、读档与时间回退。见[完整交付](../docs/dialogue-policy-completion.md)及[模型评阅](../docs/dialogue-policy-evaluation.md)。

此前表达层第一阶段后端 **334 项**、前端 **32 项**通过，详见[阶段记录](../docs/dialogue-expression-phase1.md)。

此前面对面保存与异常回复修复后，后端完整回归 **316 项**、前端单元 **32 项**通过，五组浏览器回归、lint 与构建通过。新增 `test_reply_repair.py` 覆盖主动间隔持久化、未回应抑制及回复上下文；`scene.browser.mjs` 检查共同位置、忙碌反馈及失败重试，见[修复与验证说明](../docs/scene-save-and-reply-repair.md)。

此前场景直接保存与网页主动陪伴解耦后，后端完整回归 **306 项**、前端单元 **32 项**通过，见[使用说明](../docs/scene-and-proactive-settings.md)。

2026-11-07 心跳与主动发言检查：完整后端 **287 项通过**，新增 17 项主动路径验证，前端主动轮次与自动音频检查通过，详见[心跳检查说明](../docs/heartbeat-proactive-check.md)。

2026-11-07 自动播放修复：前端 32 项单元、四组浏览器检查、lint 与构建通过，详见[语音修复记录](../docs/tts-recovery.md)。新增 `audio.browser.mjs` 使用有效合成 WAV 与原生媒体元素，不请求语音供应商。

2026-11-04 完整 Python 回归为 270 项，前端 27 项单元测试、lint 与构建通过。新增[共同材料与衔接](../docs/continuity-second-pass.md)和[常驻容器验证](../docs/service-deployment.md)，包括真实容器的登录、设备撤销、进程恢复与备份。角色上下文见[在场感修正](../docs/presence-first-pass.md)，活动见[共同手册原型](../docs/shared-project-prototype.md)，推理隔离修复见[语音修复记录](../docs/tts-recovery.md)。

## 数据隔离

Python 套件需要可用的 PostgreSQL/pgvector。测试框架从配置取得管理员连接后，只创建并清理随机的 `ember_test_*` 数据库；配置、角色状态和短期记忆复制到临时目录。所有自动测试的模型使用模拟值，不发送真实模型请求。

不要用测试清空业务库。数据库未就绪时应先处理连接问题；框架不会退回业务库执行测试。

## 常规检查

项目根目录：

```powershell
python -m pytest tests -q
```

前端目录：

```powershell
npm run lint
npm test
npm run build
npm run test:browser
```

浏览器检查需要 Playwright 与 Chromium。可通过 `PLAYWRIGHT_MODULE_PATH` 和 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指向已有安装。它们自行启动临时 Vite，使用合成 API、WebSocket、语音和对话；不会启动业务后端。已有 Live2D 素材另有真实加载检查。

## 新增覆盖

- `test_player_experience.py`：现实计时、重新进入暂停、来源记录、失败原子保存、纪元过滤、坏任务日志、跨重启恢复要求、路径限制与并发锁。
- `test_restore_experience.py`：真实存档任务、等待对话、活动往返、内容校验、失败导入回滚及无错误成功提示。
- `frontend/tests/world.test.mjs`：纪元、乱序/重复、快照版本、最终回复修复、播放所有权等待。
- `frontend/tests/remaining.browser.mjs`：专注、收藏、约定、来源阅读锚点、任务阶段、无刷新恢复、草稿取回、双标签页与 1000 条消息。
- `frontend/tests/soak.browser.mjs`：默认使用真实 30 分钟，循环流式回复、播放、视图、角色重载与世界重建，检查声音、实例、全局监听器及 JS 堆。运行 `node tests/soak.browser.mjs`，输出 `test-results/soak-report.json`。
- `utils/capture_experience.mjs`：真实角色素材与九种合成场景、专注、深色及手机视图的视觉截图。

截图和运行报告位于被 Git 忽略的 `frontend/test-results/`。内存检查针对 JS 堆，不等价于所有 GPU/进程资源测量。

## Docker 不可用时的独立测试实例

WSL Ubuntu 可使用 `utils/start_isolated_test_postgres.sh`，只在 `/var/tmp/ember-pg.*` 解包官方测试依赖并启动端口 55433，不安装系统包、不移动 Docker 数据。按脚本输出保留运行目录，用 `utils/stop_isolated_test_postgres.sh` 停止它。

测试进程使用：

```powershell
$env:PG_HOST='127.0.0.1'
$env:PG_PORT='55433'
$env:PG_USER='ember_test_admin'
$env:PG_PASSWORD='test-only'
python -m pytest tests -q
```

这些变量仅用于测试进程，不能写回业务 `.env`。临时实例不会承载原有聊天。

## 真实模型抽查与 CI

`python utils/smoke_experience.py --real-model` 显式开启两轮合成对话抽查，使用临时角色数据，不连接业务数据库。它会调用配置中的真实模型；不属于自动测试默认路径。

GitHub Actions 在主分支、开发分支、`upload/**` 和 PR 上运行后端与前端快速检查；手动工作流可选择 30 分钟检查。CI 不运行真实模型抽查。工作流已配置，本轮没有向远端推送以触发 CI。
