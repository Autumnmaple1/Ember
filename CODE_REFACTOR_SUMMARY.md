# 代码优化总结

## 主要问题及修复

### 1. 代码组织与重复代码 (DRY原则)

**问题：**
- `separate_thought_and_speech` 函数在多个文件中重复定义
- `_ensure_connection` 方法在 `db_memory.py` 和 `entity_extraction.py` 中几乎相同

**修复：**
- 创建 `memory/base_memory.py` 基础类 `BasePostgresMemory`
- 统一封装数据库连接、查询和写入操作
- `DBMemory` 和 `EntityExtractionMemory` 现在继承自基础类
- 基础类提供上下文管理器支持 (`__enter__` / `__exit__`)

### 2. 导入顺序规范 (PEP 8)

**问题：**
- `main.py:3` 中 logger 定义在 import 语句中间
- `core/heartbeat.py` 中 logger 在函数内部动态导入

**修复：**
- 将所有 import 语句移到文件顶部
- logger 定义在所有 import 之后

### 3. 魔法数字/硬编码值

**问题：**
- `server.py:66` TTS 并发限制硬编码为 3
- `server.py:253` TTS 最大文本长度硬编码为 500
- `brain/core.py:112` max_chunks 硬编码为 10000
- `brain/tts.py:16` 超时时间硬编码为 30.0

**修复：**
- 所有配置值移到 `config/settings.py`
- 新增环境变量：
  - `TTS_MAX_CONCURRENT` (默认: 3)
  - `TTS_MAX_TEXT_LENGTH` (默认: 500)
  - `TTS_TIMEOUT_SECONDS` (默认: 30.0)
  - `LLM_MAX_CHUNKS` (默认: 10000)
  - `LLM_STREAM_TIMEOUT` (默认: 60.0)
- 更新 `.env.example` 添加新配置项

### 4. 异常处理改进

**问题：**
- 多处使用裸 `except Exception` 捕获所有异常
- 应该捕获更具体的异常类型

**修复：**
- `memory/short_term.py`：使用 `(IOError, OSError, TypeError)` 替代裸异常
- `core/event_bus.py`：分级异常处理，先捕获具体异常，再捕获通用异常
- `persona/state_manager.py`：文件写入使用 `(IOError, OSError, TypeError)`

### 5. 线程安全改进

**问题：**
- `persona/state_manager.py:128-129` 中 `_state_lock` 在方法内延迟初始化，存在竞态条件

**修复：**
- 将 `_state_lock` 初始化移到 `__init__` 方法中
- 移除动态检查 `hasattr` 的代码

### 6. 性能优化

**问题：**
- `memory/short_term.py:104` `copy.deepcopy` 在方法内动态导入

**修复：**
- 将 `copy` 导入移到文件顶部

### 7. 类型注解补充

**新增类型注解：**
- `brain/core.py:77` `_llm_speak` 方法
- `memory/short_term.py:52,62,72,93,102,107` 多个方法
- `brain/tts.py:16` `generate_base64` 方法
- `memory/db_memory.py` 多个方法

### 8. 资源管理

**新增：**
- `BasePostgresMemory` 类提供 `close()` 方法用于关闭数据库连接
- 支持上下文管理器协议 (`with` 语句)

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `main.py` | 修改 | 修复导入顺序 |
| `config/settings.py` | 修改 | 添加新配置项，修复类型问题 |
| `.env.example` | 修改 | 添加新环境变量 |
| `server.py` | 修改 | 使用配置替代硬编码 |
| `brain/core.py` | 修改 | 使用配置，添加类型注解 |
| `brain/tts.py` | 修改 | 使用配置，添加类型注解 |
| `core/heartbeat.py` | 修改 | 修复导入顺序 |
| `core/event_bus.py` | 修改 | 改进异常处理 |
| `memory/short_term.py` | 修改 | 修复异常处理，优化导入 |
| `memory/db_memory.py` | 重写 | 继承 BasePostgresMemory |
| `memory/entity_extraction.py` | 重写 | 继承 BasePostgresMemory |
| `persona/state_manager.py` | 修改 | 修复线程安全问题 |
| `memory/base_memory.py` | 新增 | 数据库连接基础类 |

## 验证结果

- 所有文件语法检查通过
- 所有模块导入测试通过
- 配置加载测试通过
