# Ember 测试套件

## 核心测试（全部通过 ✅）

```
tests/
├── conftest.py          # pytest 配置和共享 fixtures
├── test_thread_safety.py # 线程安全测试（验证修复）✅
├── test_tag_utils.py    # 标签处理工具测试 ✅
├── test_integration.py  # 集成测试 ✅
├── test_security.py     # 安全测试 ✅
├── test_heartbeat.py    # 心跳机制测试 ✅
├── test_config.py       # 配置加载测试 ✅
└── test_llm_client.py   # LLM 客户端测试 ✅
```

## 运行测试

### 运行所有测试（推荐）
```bash
python run_tests.py
```

### 验证修复的线程安全问题
```bash
python run_tests.py -k thread -v
```

### 验证安全设置
```bash
python run_tests.py -k security -v
```

### 遇到失败立即停止
```bash
python run_tests.py -x
```

## 测试覆盖

| 测试文件 | 覆盖内容 | 测试数量 |
|---------|---------|---------|
| `test_thread_safety.py` | 线程池复用、并发处理标志、锁机制 | 5 |
| `test_tag_utils.py` | thought 标签修复、内容提取 | 10 |
| `test_integration.py` | 事件总线、逻辑时间、内存集成 | 9 |
| `test_security.py` | CORS、SQL 注入、图谱查询参数化、密钥保护 | 6 |
| `test_heartbeat.py` | 心跳启动/停止、事件发布 | 6 |
| `test_config.py` | 环境变量、配置加载 | 6 |
| `test_llm_client.py` | API 调用、流式响应、错误处理 | 9 |

**总计：49 个测试，全部通过，运行时间约 3-4 秒**

## 持续集成

GitHub Actions 配置在 `.github/workflows/ci.yml`
- 每次 push/PR 自动运行测试
- Python 3.11 环境
- 运行时间约 3-4 秒
