# 存档系统使用指南

## 概述

Ember 存档系统提供完整的存档创建、加载、管理功能，支持保存和恢复角色的完整状态。

## 功能特性

- ✅ 完整状态保存（PAD状态、短期记忆、情景记忆、知识图谱）
- ✅ 多存档槽管理
- ✅ 快速存档/读档
- ✅ 存档预览
- ✅ 版本兼容性检查
- ✅ 存档完整性验证

## API 接口

### 获取存档列表

```http
GET /api/archive/list
```

响应：
```json
{
  "success": true,
  "archives": [
    {
      "slot_name": "slot_1",
      "display_name": "slot_1",
      "created_at": "2026-03-20T10:00:00",
      "logical_time": "2026-03-06 14:15:00",
      "description": "图书馆初遇",
      "file_size": 12345,
      "is_valid": true
    }
  ]
}
```

### 创建存档

```http
POST /api/archive/create
Content-Type: application/json

{
  "slot_name": "slot_1",
  "description": "图书馆初遇"
}
```

### 加载存档

```http
POST /api/archive/load
Content-Type: application/json

{
  "slot_name": "slot_1"
}
```

### 删除存档

```http
DELETE /api/archive/slot_1
```

### 预览存档

```http
GET /api/archive/slot_1/preview
```

### 快速存档

```http
POST /api/archive/quick-save
```

### 快速读档

```http
POST /api/archive/quick-load
```

## 存档文件结构

```
data/archives/
├── slot_1.ember          # 存档压缩包
├── slot_2.ember
└── quick_save.ember      # 快速存档

.ember 文件内容 (ZIP):
├── manifest.json         # 元数据
├── state.json            # PAD状态
├── chat_memory.json      # 短期记忆
├── episodic_memory.sql   # 情景记忆
├── message_list.sql      # 消息历史
├── state_list.sql        # 状态历史
├── knowledge_entities.sql  # 图谱实体
└── knowledge_relations.sql # 图谱关系
```

## 代码示例

### Python 使用

```python
from archive import ArchiveManager

# 创建存档管理器
manager = ArchiveManager(
    event_bus=event_bus,
    hippocampus=hippocampus,
    heartbeat=heartbeat,
    state_manager=state_manager,
)

# 创建存档
result = manager.create_archive("slot_1", "图书馆初遇")
if result.success:
    print(f"存档创建成功: {result.slot_name}")

# 加载存档
result = manager.load_archive("slot_1")
if result.success:
    print(f"存档加载成功: {result.manifest.logical_time}")

# 列出存档
slots = manager.list_archives()
for slot in slots:
    print(f"- {slot.slot_name}: {slot.description}")

# 快速存档
manager.quick_save()

# 快速读档
manager.quick_load()
```

### 前端集成示例

```javascript
// 创建存档
async function createArchive(slotName, description) {
  const response = await fetch('/api/archive/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slot_name: slotName, description }),
  });
  return response.json();
}

// 加载存档
async function loadArchive(slotName) {
  const response = await fetch('/api/archive/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ slot_name: slotName }),
  });
  return response.json();
}

// 获取存档列表
async function listArchives() {
  const response = await fetch('/api/archive/list');
  return response.json();
}
```

## 注意事项

1. **存档操作期间系统会暂停**：创建/加载存档时，心跳和状态更新会暂时停止
2. **数据库数据会被清空**：加载存档会清空现有数据库数据并恢复存档内容
3. **版本兼容性**：不同版本的存档可能不兼容，系统会自动检查
4. **存档文件损坏**：如果存档文件损坏，系统会返回错误信息

## 事件

存档系统会发布以下事件：

- `archive.start` - 开始创建存档
- `archive.complete` - 存档创建完成
- `archive.restore.start` - 开始恢复存档
- `archive.restore.complete` - 存档恢复完成
- `archive.error` - 存档操作错误
- `state.reload` - 状态重载完成
