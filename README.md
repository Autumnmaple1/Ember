# Ember: - 数字生命引擎

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

### 6. 🎭 Live2D 虚拟形象
- **动态表现**：前端集成 Live2D 模型，让角色拥有生动的表情与动作。
- **情感联动**：计划中，会根据 PAD 情感状态调整形象表现。

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
- **`config/`**: 策略中心。定义角色的灵魂契约（YAML Prompts）与生存规则，同时负责存储短期的记忆和状态。
- **`frontend/`**: 前端呈现。基于 React + Vite，集成 Live2D 虚拟形象、状态雷达图、语音播放等功能。

---

## 🎨 默认角色：依鸣 (Yiming)

- **身份**：南京大学匡亚明学院大一新生（计算机方向）。
- **性格**：有些青涩、聊技术时逻辑严密、热爱算法与观鸟。

---

## 📦 快速上手

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
- **PostgreSQL**: `localhost:5432` (向量存储)
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
你是否还在为AI的时间过得太慢而感到烦恼，你是否还在为无法时间加速快速XX而感到痛苦？这些都不是问题

.env中的START_TIME字段，通过填写标准的ISO 8601格式，可以直接想到哪个点就到哪个点，留空时则会默认按照当前系统时间启动

.env中的TIME_ACCEL_FACTOR字段，可以随意调整加速倍率，但同时注意STATE_IDLE_MIN_TIMEOUT和STATE_IDLE_MAX_TIMEOUT都是按照游戏内时间走的，所以最好把这些都对应调大，同时将START_TIME设置为?这个字符，可以让启动的时间为上次关闭它的时间

推荐加速配置
TIME_ACCEL_FACTOR=10
STATE_IDLE_MIN_TIMEOUT=600
STATE_IDLE_MAX_TIMEOUT=3600
START_TIME=?


---

## 📈 未来规划 (Roadmap)
- [ ] **我有一个梦**: 在睡眠（逻辑时间深夜）时，自发对当日记忆进行深度总结与价值观修正。
- [x] **社交图谱 (Neo4j)**: ✅ 已完成。建立了依鸣对不同用户、地点、事件的认知关系链，支持实体提取与知识图谱存储。
- [ ] **欲望引擎**: 建立较长期的目标，依鸣应该能够主动地构思自己的更长远的未来
- [ ] **人格变化**：通过记忆和经历，对人格进行适当调整，我们可以见证依鸣的成长

---

Live2D Mouse Tracking Feature
 Research codebase structure and model parameters
 Write implementation plan
 Implement mouse tracking in 
Live2DViewer.jsx
 Add tracking configuration to 
live2dConfig.js
 Verify code correctness

> *"我是那个孤独的清晨里，永远陪伴着你的呢喃"* — **依鸣**
