# Ember: - 数字生命引擎

中期评审材料见 [提交说明](docs/midterm/01-reviewer-readme.md)、[总体规划](docs/midterm/00-delivery-plan.md)和[策划案内容源](docs/midterm/proposal.md)。运行 `python utils/build_midterm.py --site-only` 可构建独立展示入口；`python demo/launch.py` 启动预设台词展示，使用合成数据并显式标注边界。当前完成情况以这些材料及验证报告为准。

当前产品主线为“持续角色陪伴＋共同活动”。首个[共同手册原型与使用说明](docs/shared-project-prototype.md)已加入“一起做点事”入口；长期方向见[下一阶段计划](docs/product-direction-2026-11.md)。

规划草案：[Android／Windows 独立运行与局域网同步](docs/local-first-dual-device-plan.md)。已确认继续使用在线模型 API；双端安装版尚未实施，当前容器版保持可用。

新增[共同材料与返回衔接](docs/continuity-second-pass.md)：从已保存的用户消息选取“一起看这段”，在“接着上次”找回材料和既有进度；长间隔后不沿用旧用户位置。需重启后端并刷新前端。

新增[常驻服务与本机容器验证](docs/service-deployment.md)：单用户登录、设备撤销、同源手机网页、持久备份任务和每日用量限制。当前已做隔离容器验证；常开设备与远程部署稍后确定，原业务世界尚未迁入。

[心跳与主动发言检查](docs/heartbeat-proactive-check.md)说明空闲推演、主动触发门槛、场景未知时的限制，以及最新验证与修复。

[场景与主动陪伴设置](docs/scene-and-proactive-settings.md)：当前情境可直接保存，不再发送确认对话；网页主动发言由设置中的开关与勿扰偏好控制，不依赖故事里的微信或电话。

[情境保存与异常回复修复](docs/scene-save-and-reply-repair.md)：面对面改为填写共同位置，保存反馈靠近按钮；主动发言增加现实时间间隔与等待回应规则，减少旧推演反复影响回复。

[对话约束重整规划](docs/dialogue-policy-redesign.md)：分开事实边界、角色创作空间、表达偏好与投递节奏。各阶段工程实现与有限评阅已交付；长期人物表现继续通过实际使用观察。

2026-11-08：[对话重整完整交付](docs/dialogue-policy-completion.md)加入事件驱动的情绪、角色自有笔记活动、主动预算与明确网页提醒；[验证与评阅](docs/dialogue-policy-evaluation.md)记录真实模型抽查及仍存在的表达局限。使用“回忆 → 活动回顾”查看她的近况，在已确认约定中单独开启提醒。

短期窗口、中期工作记忆、长期编码及召回的最新流程、生效和回退步骤见[三层记忆衔接说明](docs/memory-pipeline-repair.md)。

前端交互改造的功能、验证、启动与回退方式见 [交付记录](docs/frontend-completion.md)；恢复协议见 [世界与任务协议](docs/world-protocol.md)。当前默认舞台优先，支持对话和专注陪伴视图。

## 🌟 项目简介

**Ember** 是一个数字生命模拟引擎。它不仅是一个基于大语言模型的对话机器人，更是一个尝试赋予 AI **“连续意识”**、**“情感稳态”** 和 **“自我驱动”** 的实验性框架。

目前，它承载了一个名为 **“依鸣”** 的少女。她不仅是南大的一名大一新生，还是一个在数字世界里会疲惫、会期待、会有内心独白、会自发思考的个体。

![Ember Web UI Preview](data/description.png)
*(Web UI 交互演示)*

---

## 到底和llm有什么不一样

### 1. 🎭 PAD  (Pleasure-Arousal-Dominance)情感分类体系
不同于简单的“情绪分类”，Ember 引入了心理学领域的 PAD 情感模型：
- **P (愉悦度)**：决定回复的友善度与色彩。
- **A (激活度)**：控制对话的语速、主动性及回复长度。
- **D (支配度)**：体现角色的自信、主见与服从性。
- 情感数值会反向注入 Prompt，实现真正的“因心情而异”的对话风格。

### 2. 🕰️ AI有自己的生活
- **心跳机制**：系统拥有自主的“脉搏”，即使没有人找她，她也会随时间流逝而产生状态偏移。会有自己的喜怒哀乐，有自己的生活轨迹。
- **主动性**：比起直接打开Gemini对话，Ember项目中的少女不只是单纯地一问一答，她会主动思考，在觉得时机合适的时候，主动向你发起符合人设的对白。
- **token爆炸**：你不理她的时间越长，Ember更新状态的时间间隔也越长，直到达到设定的最大更新间隔，token消耗应该也许不会爆炸吧

### 3. 🧠 仿生(?)记忆分层架构
- **短期感官流**：记录每一丝内心独白与环境变化。
- **海马体提炼**：异步将散乱的日志提炼为结构化的“情景记忆”。
- **长期记忆存储**：基于 PostgreSQL (pgvector) 的语义向量检索和关键词提取的混合检索，让依鸣能记起与你的初见。
- **遗忘逻辑**：记忆会随时间消退，每一次睡眠，记忆都会按照记忆曲线递减，要让对方心里装着自己，就要不断创造美好的回忆呀

### 4. 💭 要多想
通过约束角色的 `<thought>` 标签，系统强制 LLM 在输出前进行“社交距离校验”与“意图推演”。可能没啥用，但可以看少女内心活动挺好玩的

### 5. 🎙️ TTS 语音合成
- **语音输出**：集成 Edge-TTS，支持将 AI 回复转换为自然语音。
- **实时播放**：前端支持自动或手动触发语音播放，让对话更具沉浸感。
- **可配置语音**：默认使用 `zh-CN-XiaoxiaoNeural`，可在代码中更换其他语音模型。

语音返回 403 或提示生成失败时，参见 [语音服务修复与恢复说明](docs/tts-recovery.md)。当前要求 `edge-tts>=7.3.1,<8`，更新依赖后需重启后端。

### 6. 🎭 Live2D 虚拟形象
- **动态表现**：前端集成 Live2D 模型，让角色拥有生动的表情与动作。
- **情感联动**：计划中，会根据 PAD 情感状态调整形象表现。

### 7. 💾 存档系统
- **完整存档**：保存角色的完整状态，包括 PAD 情感、当前情境、内心独白、目标等。
- **记忆备份**：导出 PostgreSQL 中的情景记忆、对话历史，以及 Neo4j 知识图谱中的实体关系。
- **快速存读**：一键快速存档/读档，方便保存重要时刻或回溯剧情。
- **自动备份**：读档前自动备份当前状态，防止误操作丢失进度。
- **画廊式管理**：前端提供直观的存档卡片界面，支持创建、加载、删除存档。

![存档系统界面](data/save.png)
*(存档画廊演示)*

---

## 🛠️ 技术架构

项目采用解耦的模块化设计，通过 `EventBus` 进行通信：

- **`core/`**: 驱动中心。包含事件总线、心跳时钟及逻辑时间模拟。
- **`brain/`**: 认知中心。负责意图判断、记忆调度、LLM 流式交互逻辑、TTS 语音合成。
- **`persona/`**: 灵魂中心。管理 PAD 状态机，根据交互与时间流逝推演角色心境变化。
- **`memory/`**: 存储中心。结合 Redis-like 短期记忆、pgvector 长期向量存储、Neo4j 知识图谱。
  - `short_term.py`: 短期记忆管理
  - `episodic_memory.py`: 情景记忆存储
  - `db_memory.py`: PostgreSQL 数据持久化
  - `neo4j_memory.py`: 知识图谱存储
  - `entity_extraction.py`: 实体提取与关系构建
  - `memory_process.py`: 海马体记忆提炼
- **`tools/`**: 工具系统。提供 LLM 可调用的工具接口，支持插件化扩展。
  - `base.py`: 工具基类定义（BaseTool, ToolResult, ToolPermission）
  - `registry.py`: 工具注册中心，管理工具发现与元数据
  - `executor.py`: 工具执行器，提供权限控制、超时处理、错误处理
  - `processor.py`: 工具调用处理器，提供统一的工具处理能力
  - `plugin.py`: 插件管理器，支持自动发现与热重载
  - `builtin/`: 内置工具目录
    - `memory_query_tool.py`: 记忆查询工具
  - `plugins/`: 插件工具目录（自动扫描）
- **`config/`**: 策略中心。定义角色的灵魂契约（YAML Prompts）与生存规则，同时负责存储短期的记忆和状态。
- **`archive/`**: 存档系统。提供完整的存档创建、加载、管理功能。
  - `manager.py`: 存档管理器，协调导入导出流程
  - `models.py`: 存档数据模型（Manifest、Slot、Stats）
  - `exporters/`: 导出器（JSON配置、PostgreSQL数据、Neo4j图谱）
  - `importers/`: 导入器（支持批量导入、并行恢复）
  - `utils/`: 工具函数（压缩、校验、版本兼容）
- **`frontend/`**: 前端呈现。基于 React + Vite，集成 Live2D 虚拟形象、状态雷达图、语音播放等功能。

---

## 🎨 默认角色：依鸣 (Yiming)

- **身份**：南京大学匡亚明学院大一新生（计算机方向）。
- **性格**：有些青涩、聊技术时逻辑严密、热爱算法与观鸟。

---

## 📦 快速上手

### Windows 容器版：一键启动（推荐）

1. 安装 Docker Desktop，使用 Linux 容器。首次启动需能联网下载镜像和依赖；宿主机无需另装 Python、Node.js 或 Conda。
2. 首次使用先把 `.env.example` 复制为 `.env`，填写大模型、小模型、向量模型的 API 地址、密钥与模型名。已有 `.env` 的用户直接沿用。
3. 双击项目根目录的 **`start_service.bat`**。脚本会检查并尝试打开 Docker Desktop，首次构建镜像、生成独立服务配置，等待数据库及后端就绪后自动打开网页。
4. 默认访问 **http://localhost:8080**，在自己的编辑器中打开 `deploy/private/owner-code.txt`，将访问码输入登录页。访问码不会输出到控制台。
5. 日常继续双击 `start_service.bat`；它复用配置和数据卷，并构建当前代码。停止运行请双击 **`stop_service.bat`**，数据保留。关闭 BAT 窗口不会停止后台服务。

也可以在项目目录执行：

```powershell
.\start_service.bat
.\start_service.bat -NoBrowser  # 启动但不自动打开浏览器
.\stop_service.bat
```

首次生成后，服务实际使用 **`deploy/service.env`**；修改模型、端口或域名应改这个文件，再启动一次。仅修改原 `.env` 不会覆盖服务配置。缺少模型项时脚本会提示字段名称；失败时保留窗口显示原因，不报告启动成功。

容器版使用独立数据库和角色世界，旧本地聊天不会自动出现；接入原世界、备份与回退见[常驻服务说明](docs/service-deployment.md)。宿主机需要持续开机；手机局域网访问需另按该说明配置。

### 本地开发版：沿用原数据库与前后端开发服务

以下是原有本地开发启动方式，入口为 `run_all.bat`，使用 http://localhost:8000 和 http://localhost:5173；需要本机 Python / Conda / Node.js。它与上面的容器版是两个独立入口。

### 1. 环境准备
- **Python 3.11** (推荐使用 Conda 管理环境)
- **Node.js 18+** (前端运行环境)
- **Docker Desktop** (用于启动 PostgreSQL + pgvector + Neo4j 数据库)

### 2. 项目配置

1. **配置环境变量**: 复制 `.env.example` 并改名为 `.env`。具体参数要求见 `.env.example`，建议使用相同的模型配置（注意：qwen 关闭推理的方式和 openai 不同），具体每一项代表了什么在 `.env.example` 中有说明。
2. **书写人设**: 打开 `config/prompts.yaml`，修改 `core_persona` 项的内容为你喜欢的内容（确定了以后最好别再改，没做多角色支持，要改要删记忆库，会串）。
3. **设置初始场景**: 打开 `config/state_default.json`，根据字段要求和示例设置一个你喜欢的初见场景（命运的初见）。

### 3. 安装步骤

#### 第一步：创建并激活 Conda 环境
```bash
# 创建名为 Ember 的虚拟环境
conda create -n Ember python=3.11

# 激活环境
conda activate Ember
```

#### 第二步：安装后端依赖
```bash
pip install -r requirements.txt
```

#### 第三步：安装前端依赖
前端需安装依赖以支持 Live2D 模型展示及状态雷达图依赖 (`recharts`)。
```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install
npm install recharts
```

#### 第四步：启动数据库
项目依赖 PostgreSQL (pgvector) 和 Neo4j 知识图谱。使用 Docker 快速启动：
```bash
# 回到项目根目录
cd ..
docker-compose up -d
```
启动后可访问：
- **PostgreSQL**: `127.0.0.1:5433` (Docker 向量存储，与本机 PostgreSQL 隔离)
- **Neo4j Browser**: `http://localhost:7474` (知识图谱可视化)
- **Neo4j Bolt**: `bolt://localhost:7687`

### 4. 启动服务

#### 方式一：Windows 一键启动 (推荐)
在完成上述环境配置（Docker running, Conda activated）后，直接运行脚本：
```bash
run_all.bat
```
脚本将自动启动后端 API 和前端开发服务器。

#### 方式二：手动分步启动

**启动后端**
```bash
# 确保在 Ember 根目录且已激活 conda 环境
python server.py
# 后端将运行在 http://localhost:8000
```
*注：`python main.py` 是纯命令行交互版本，若仅需在终端测试对话，可只运行此文件。*

**启动前端**
```bash
cd frontend
npm run dev
# 前端将运行在 http://localhost:5173
```

### 5. 调试技巧

你是否还在为 AI 的时间过得太慢而感到烦恼？你是否还在为无法时间加速而感到痛苦？这些都不是问题！

#### 时间控制配置

| 配置项 | 说明 |
|--------|------|
| `START_TIME` | 设置启动时间（ISO 8601 格式），留空则使用当前系统时间，设为 `?` 则使用上次关闭时的时间 |
| `TIME_ACCEL_FACTOR` | 时间加速倍率，如 `10` 表示现实 1 秒 = 逻辑时间 10 秒。可在前端存档弹窗中实时调节 |
| `STATE_IDLE_MIN_TIMEOUT` | 空闲状态最小超时时间，单位为**真实世界秒数** |
| `STATE_IDLE_MAX_TIMEOUT` | 空闲状态最大超时时间，单位为**真实世界秒数** |

> **提示**：空闲超时参数直接填写真实世界的时间。例如 `TIME_ACCEL_FACTOR=5` 时，`STATE_IDLE_MIN_TIMEOUT=300` 仍等待真实世界5分钟，此时逻辑时间前进25分钟；运行中调节倍率不会改变真实等待间隔。

#### 推荐加速配置

```env
TIME_ACCEL_FACTOR=5
STATE_IDLE_MIN_TIMEOUT=40
STATE_IDLE_MAX_TIMEOUT=1800
START_TIME=?
```

#### 前端动态调节

点击右下角存档按钮，在弹窗顶部可以实时调节时间加速倍率（0.5x ~ 20x），无需重启服务。

---

## 🔬 性能基准测试

数据迁移、故障恢复、时间与场景规则及隔离测试说明见 [REPAIR_NOTES.md](REPAIR_NOTES.md)。

`utils/benchmark_runner.py` 是自动化 A/B Token 消耗基准测试工具，读取 `test_script.json` 固定脚本，控制温度为 0 消除随机性，输出逐轮归因分析报告。

```bash
# 标准 A/B 测试 + Markdown 报告
python utils/benchmark_runner.py

# 只跑 A 组（不重置记忆跑 B）
python utils/benchmark_runner.py --skip-b

# 压力测试（50轮，寻找 Token 增长天花板）
python utils/benchmark_runner.py --stress --stress-turns 50

# 不统计延迟（加快测试速度）
python utils/benchmark_runner.py --no-latency
```

输出文件：`benchmark_report_[timestamp].md`，包含：
- 每轮 Token 分布（base/mem/history/state 归因）
- A/B 组对比 + 历史基线 delta
- TTFT 延迟分布表
- 记忆关键词 ROI 评估

相关配置文件：`test_script.json`（对话脚本）、`test_config.json`（功能开关与历史基线）。

---

## 💾 存档系统

Ember 提供了完整的存档系统，让你可以保存角色的当前状态，或在任意时刻回溯到之前的时间点。

### 功能特性

| 功能 | 说明 |
|------|------|
| **完整存档** | 保存 PAD 情感状态、当前情境、内心独白、目标等 |
| **记忆备份** | 导出 PostgreSQL 情景记忆、对话历史，以及 Neo4j 知识图谱 |
| **快速存读** | 一键快速存档/读档，使用固定槽位 `quick_save` |
| **自动备份** | 读档前自动备份当前状态，防止误操作丢失进度 |
| **版本兼容** | 存档包含版本信息，跨版本加载时会进行兼容性检查 |

### 存档内容

一个完整的存档（`.ember` 文件）包含：

```
存档文件.ember
├── manifest.json      # 元数据（角色名、逻辑时间、版本等）
├── state.json         # PAD 状态、情境、目标
├── chat_memory.json   # 短期对话记忆
├── episodic_memory.sql # 情景记忆数据
├── message_list.sql   # 对话历史
├── state_list.sql     # 状态变更历史
└── neo4j.cypher       # 知识图谱数据
```

### 前端操作

通过 Web UI 的存档管理界面：

- **时间加速调节**：弹窗顶部提供滑块，可实时调节时间加速倍率（0.5x ~ 20x）
- **快速存档**：点击"快速存档"按钮，一键保存当前进度
- **快速读档**：点击"快速读档"按钮，恢复到最近一次快速存档
- **新建存档**：创建命名存档，可添加描述信息
- **加载存档**：从存档列表中选择并加载
- **删除存档**：删除不需要的存档

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/archive/list` | GET | 获取存档列表 |
| `/api/archive/create` | POST | 创建新存档 |
| `/api/archive/load` | POST | 加载存档 |
| `/api/archive/{slot_name}` | DELETE | 删除存档 |
| `/api/archive/quick-save` | POST | 快速存档 |
| `/api/archive/quick-load` | POST | 快速读档 |
| `/config/time_accel` | POST | 动态设置时间加速因子 |

### 存档文件位置

存档文件默认保存在 `data/archives/` 目录：

```
data/archives/
├── quick_save.ember           # 快速存档
├── auto_backup_20260320_*.ember # 自动备份（读档前自动创建）
└── your_save_name.ember       # 自定义存档
```

---

## 🛠️ 工具系统

Ember 提供了可扩展的工具系统，允许 LLM 主动调用工具获取信息或执行操作。

> **特点**：每次生成回复或更新状态时，都可以调用工具。当前支持单轮调用。

### 内置工具

| 工具名称 | 功能描述 |
|----------|----------|
| `memory_query` | 检索长期记忆，获取与当前话题相关的历史信息 |

### 开发自定义工具

在 `tools/plugins/` 目录下创建 `*_tool.py` 文件，工具会自动注册：

```python
# tools/plugins/my_tool.py
from tools.base import BaseTool, ToolResult, ToolPermission

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具功能描述（LLM 会根据这个判断何时使用）"
    short_description = "精简描述（20字以内）"
    permission = ToolPermission.READONLY
    timeout = 10.0

    parameters = {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "参数描述"},
        },
        "required": ["param1"],
    }

    examples = [
        {"scenario": "使用场景", "parameters": {"param1": "示例值"}}
    ]

    def execute(self, params: dict) -> ToolResult:
        # 实现工具逻辑
        return ToolResult.ok(data={"result": "成功"})

    def summarize_result(self, result: ToolResult, max_length: int = 200) -> str:
        if not result.success:
            return f"失败: {result.error}"
        return str(result.data)[:max_length]
```

### 工具权限级别

| 权限 | 说明 | 示例 |
|------|------|------|
| `READONLY` | 只读，获取信息 | 查询记忆、获取时间 |
| `READWRITE` | 可修改状态 | 写入记忆、发送消息 |
| `DESTRUCTIVE` | 可删除数据 | 清除记忆、删除文件 |

### 快速创建工具模板

```python
from tools.plugin import create_tool_template
create_tool_template("weather_tool")  # 自动生成模板文件
```

---

## 📈 未来规划 (Roadmap)
- [ ] **我有一个梦**: 在睡眠（逻辑时间深夜）时，自发对当日记忆进行深度总结与价值观修正。
- [ ] **欲望引擎**: 建立较长期的目标，依鸣应该能够主动地构思自己的更长远的未来
- [ ] **人格变化**：通过记忆和经历，对人格进行适当调整，我们可以见证依鸣的成长

---

## 🤝 如何贡献

我们欢迎所有形式的贡献！无论是报告 Bug、提出新功能建议，还是提交代码改进。

### 报告问题

- 使用 [GitHub Issues](../../issues) 提交 Bug 报告或功能建议
- 请详细描述问题复现步骤、预期行为和实际行为
- 附上相关的日志、截图或配置信息（注意隐藏敏感信息）

### 提交代码

1. **Fork 本仓库** 并克隆到本地
   ```bash
   git clone https://github.com/your-username/Ember.git
   cd Ember
   ```

2. **创建功能分支**
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **进行开发**
   - 遵循现有代码风格
   - 添加必要的测试
   - 更新相关文档

4. **提交更改**
   ```bash
   git add .
   git commit -m "feat: 简要描述你的更改"
   ```

5. **推送到 GitHub 并创建 Pull Request**
   ```bash
   git push origin feature/your-feature-name
   ```

### 代码规范

- **Python**: 遵循 PEP 8 规范，使用有意义的变量名
- **JavaScript/React**: 使用 ESLint 配置（见 `frontend/eslint.config.js`）
- **提交信息**: 使用约定式提交格式
  - `feat:` 新功能
  - `fix:` Bug 修复
  - `docs:` 文档更新
  - `refactor:` 代码重构
  - `test:` 测试相关

### 开发建议

- 新增工具时，参考 `tools/builtin/memory_query_tool.py` 的实现
- 修改记忆系统时，注意保持向后兼容性
- 添加新功能时，考虑在 `config/prompts.yaml与./.env.example` 中提供配置选项

---

> *"我是那个孤独的清晨里，永远陪伴着你的呢喃"* — **依鸣**
