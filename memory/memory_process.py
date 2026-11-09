import time
import threading
import logging
import json
import concurrent.futures
import copy
import math
import uuid
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient
from memory.neo4j_memory import Neo4jGraphMemory
from memory.working_memory import WorkingMemory
from memory.journal import LOG_LOCK, consume_prefix
from core.time_utils import to_epoch

logger = logging.getLogger(__name__)


class Hippocampus:
    _file_lock = LOG_LOCK

    def __init__(self, event_bus: EventBus, working_memory=None, short_term_memory=None):
        self.event_bus = event_bus
        self.event_bus.subscribe("memory.preprocess", self._on_preprocess_request)
        self.llm_client = LLMClient()
        self.working = working_memory or WorkingMemory()
        self.short_term_memory = short_term_memory
        self._encode_lock = threading.RLock()
        self._completion_lock = threading.RLock()
        self._last_attempt = 0
        self._last_encoded = time.monotonic()
        self.last_error = None
        event_bus.subscribe('memory.stored', self._on_stored)
        event_bus.subscribe('memory.store_failed', self._on_store_failed)
        event_bus.subscribe('system.tick', self._on_tick)

        # 初始化 Neo4j 图谱连接
        self.graph_memory = None
        if settings.ENABLE_NEO4J:
            self.graph_memory = Neo4jGraphMemory(event_bus)

    def _load_experience(self):
        try:
            with self._file_lock:
                with open("./config/chat_history.log", "r", encoding="utf-8") as f:
                    content = f.read()
                return content

        except FileNotFoundError:
            logger.warning("chat_history.log not found")
            return ""

    def _on_preprocess_request(self, event: Event):
        if self.event_bus.paused or self.event_bus.world_paused or getattr(self.event_bus, 'archive_active', False):
            return
        with self.event_bus.operation_lock, self._encode_lock:
            if event.data.get('reason') in ('idle', 'checkpoint') and time.monotonic() - self._last_attempt < settings.MEMORY_RETRY_INTERVAL:
                return
            self._last_attempt = time.monotonic()
            jobs = self.working.snapshot()['jobs']
            if jobs:
                for job in jobs:
                    self._dispatch(job)
                return
            res = self._load_experience()
            if not res.strip():
                return
            # Keep complete lines and the exact prefix for transactional acknowledgement.
            lines, size = [], 0
            for line in res.splitlines(keepends=True):
                if lines and size + len(line) > 18000:
                    break
                lines.append(line)
                size += len(line)
            res = ''.join(lines)
            sources = self._sources(res)
            recent = self.short_term_memory.get_memory()['history'] if self.short_term_memory else []
            context = {'log': res, 'buffered_sources': self.working.snapshot()['sources'],
                'previous_notes': self.working.snapshot()['notes'], 'recent_dialogue': recent}
            try:
                response = self.llm_client.one_chat(settings.LARGE_LLM, [
                    {'role': 'system', 'content': settings.MEMORY_ENCODING_PROMPT},
                    {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}], call_type='memory_encode')
                parsed = self.llm_client._extract_json(response) if response else None
                raw = parsed.get('memories') if isinstance(parsed, dict) else parsed
                notes = parsed.get('working_notes', []) if isinstance(parsed, dict) else []
                if not isinstance(raw, list) or not isinstance(notes, list):
                    raise ValueError('记忆编码未返回合法列表')
                memories = self._validate_memories(raw, sources, res)
                # Explicit requests to retain something must not be silently discarded.
                if not memories and any(self._important(x) for x in sources):
                    for item in sources:
                        if self._important(item):
                            memories.append(self._attributed_memory(item, res))
                job = self.working.add_job(res, memories, notes, sources + recent)
                self._dispatch(job)
            except Exception as exc:
                self.last_error = '记忆整理失败，原始日志与中期缓冲已保留'
                try:
                    self.working.request()
                except OSError:
                    logger.warning('Memory checkpoint unavailable; original log retained')
                logger.warning('Memory encoding retained for retry (%s)', type(exc).__name__)

    @staticmethod
    def _sources(text):
        result = []
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if (isinstance(row, dict) and row.get('role') in ('user', 'assistant', 'system_event')
                    and isinstance(row.get('content'), str) and isinstance(row.get('message_id'), str)):
                result.append(row)
        return result

    @staticmethod
    def _important(source):
        return source.get('role') == 'user' and any(cue in source['content'] for cue in ('请记住', '记一下', '别忘了', '我们约好'))

    def _validate_memories(self, raw, sources, text):
        source_map = {x['message_id']: x for x in sources}
        has_legacy = False
        for line in text.splitlines():
            try:
                row = json.loads(line)
                has_legacy |= isinstance(row, dict) and row.get('source_type') == 'role_state'
            except ValueError:
                has_legacy |= bool(line.strip())
        result = []
        for value in raw:
            if not isinstance(value, dict) or not isinstance(value.get('content'), str) or not value['content'].strip():
                raise ValueError('长期记忆正文无效')
            mem = {k: copy.deepcopy(value[k]) for k in ('content', 'insight', 'keywords', 'importance', 'confidence', 'time') if k in value}
            keys = value.get('source_ids', [])
            if not isinstance(keys, list) or any(key not in source_map for key in keys):
                raise ValueError('长期记忆引用了不存在的来源')
            if sources and not keys and not (has_legacy and value.get('source_type') == 'legacy_or_simulated'):
                raise ValueError('新对话记忆缺少来源标识')
            for field, default, maximum in (('confidence', 0.5, 1), ('importance', 1, 10)):
                number = mem.get(field, default)
                if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= maximum:
                    raise ValueError('记忆权重无效')
                mem[field] = number
            evidence = [source_map[key] for key in keys]
            stamps = [x['timestamp'] for x in evidence if type(x.get('timestamp')) in (int, float) and math.isfinite(x['timestamp'])]
            if stamps:
                mem['time'] = self.event_bus.format_logical_time(max(stamps) / 1000)
                mem['time_basis'] = 'source_message_time'
            else:
                mem['time'] = self.event_bus.format_logical_time(to_epoch(mem['time'])) if mem.get('time') else None
                mem['time_basis'] = 'model_estimate' if mem['time'] else 'unknown'
            if not evidence:
                mem['confidence'] = min(mem['confidence'], 0.5)
            if not isinstance(mem.get('insight', ''), str) or not isinstance(mem.get('keywords', []), list):
                raise ValueError('记忆字段无效')
            source_type = 'player_activity_record' if evidence and all(x.get('role') == 'system_event' for x in evidence) else 'quoted_dialogue' if evidence else 'legacy_or_simulated'
            if any(row.get('source_type') == 'role_simulation_record' for row in evidence):
                source_type = 'role_simulation_record'
                mem['content'] = '角色虚构世界记录（不代表用户经历）：' + mem['content']
            mem.update(source_ids=keys, source_type=source_type,
                evidence=[{k: row.get(k) for k in ('message_id', 'role', 'timestamp', 'content', 'completion_status', 'source_type')} for row in evidence])
            mem['memory_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, text + json.dumps(mem, ensure_ascii=False, sort_keys=True)))
            result.append(mem)
        return result

    def _attributed_memory(self, source, text):
        item = {'content': '用户自述（仅作原文记录）：' + source['content'], 'confidence': 1,
            'importance': 1, 'keywords': ['用户自述'], 'source_ids': [source['message_id']]}
        return self._validate_memories([item], [source], text)[0]

    def _dispatch(self, job):
        for memory in job['memories']:
            if memory['memory_id'] in job['acknowledged']:
                continue
            receipts = self.event_bus.publish(Event('memory.store', copy.deepcopy(memory)))
            if not any(isinstance(x, dict) and x.get('accepted') and x.get('memory_id') == memory['memory_id'] for x in receipts or []):
                self.last_error = '长期记忆待办尚未被可靠接收，原始资料已保留'
        self._complete_jobs()

    def _on_stored(self, event):
        key = event.data['memory_id']
        if not any(key in {m['memory_id'] for m in j['memories']} for j in self.working.snapshot()['jobs']):
            return
        self.working.acknowledge(key)
        self._complete_jobs()

    def _on_store_failed(self, event):
        self.last_error = '长期记忆入库失败，已保留原始日志与待办，稍后自动重试'

    def _complete_jobs(self):
        with self._completion_lock:
            self._finish_acknowledged()

    def _finish_acknowledged(self):
        for job in self.working.snapshot()['jobs']:
            if set(job['acknowledged']) >= {x['memory_id'] for x in job['memories']}:
                if job.get('phase') != 'committing':
                    self.working.begin_commit(job['id'])
                consumed = not job['text'] or consume_prefix(job['text'])
                self.working.finish(job['id'])
                self.last_error = None if consumed or job.get('phase') == 'committing' else '已入库记忆的原始日志前缀已变化，保留当前日志'
                self._last_encoded = time.monotonic()

    def _on_tick(self, event):
        bus = self.event_bus
        if (bus.paused or bus.world_paused or getattr(bus, 'archive_active', False)
                or bus.pending_dialogues or time.monotonic() - self._last_attempt < settings.MEMORY_RETRY_INTERVAL):
            return
        if not bus.operation_lock.acquire(blocking=False):
            return
        try:
            data = self.working.snapshot()
            text = self._load_experience()
            sources = self._sources(text)
            legacy = any(line.strip() and not line.lstrip().startswith('{') for line in text.splitlines())
            important = any(self._important(x) for x in sources)
            if (data['jobs'] or data['requested'] or important or len(sources) >= settings.MEMORY_ENCODE_MESSAGES or
                    ((sources or legacy) and time.monotonic() - self._last_encoded >= settings.MEMORY_ENCODE_INTERVAL)):
                self._on_preprocess_request(Event('memory.preprocess', {'reason': 'checkpoint'}))
        finally:
            bus.operation_lock.release()

    def after_restore(self):
        self.working.reload()
        self.last_error = None
        self._last_attempt = 0
        self._last_encoded = time.monotonic()

    def status(self):
        return {**self.working.status(), 'last_error': self.last_error}

    def query_memory(
        self,
        query: str,
        keywords: list = None,
        entities: list = None,
        timeout: float = 10.0,
    ) -> dict:
        """
        核心记忆检索方法 - 供工具和内部调用统一入口

        Args:
            query: 检索查询语句（陈述句描述目标记忆）
            keywords: 关键词列表（用于向量检索和图谱查询）
            entities: 实体列表（用于图谱查询，可选）
            timeout: 超时时间（秒）

        Returns:
            dict: {
                "found": bool,
                "episodic_memories": ["[日期] 内容", ...],
                "graph_entities": {"名字": "描述", ...},
                "graph_relations": ["A 关系 B", ...],
                "query": str,
                "keywords": list
            }
        """
        keywords = keywords or []
        entities = entities or []

        # 从 keywords 中筛选可能的实体名（长度>=2的专有名词）
        potential_entities = entities or [kw for kw in keywords if len(kw) >= 2]

        logger.info(f"[query_memory] query='{query[:50]}...', keywords={keywords}")

        try:
            # 并行检索：向量检索 + 图谱查询
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # 任务1：PostgreSQL 向量检索
                future_episodic = executor.submit(
                    self._get_persistence_memory,
                    {"query": query, "key_words": keywords},
                )

                # 任务2：Neo4j 图谱查询
                future_graph = executor.submit(
                    self._get_graph_memory, potential_entities
                )

            # 获取向量检索结果
            retrieval = future_episodic.result(timeout=timeout)
            warnings = retrieval.get('warnings', []) if isinstance(retrieval, dict) else []
            raw_memories = retrieval.get('items', []) if isinstance(retrieval, dict) else retrieval
            simplified_memories = self._simplify_memories(raw_memories)

            # 获取图谱查询结果
            graph_context = self._simplify_graph(
                future_graph.result(timeout=timeout), query=query, key_words=keywords
            )

            # 统计结果
            episodic_count = len(simplified_memories)
            graph_entity_count = len(graph_context.get("entities", {}))
            found = episodic_count > 0 or graph_entity_count > 0

            logger.info(
                f"[query_memory] 检索完成: 情景记忆 {episodic_count} 条 | "
                f"图谱实体 {graph_entity_count} 个"
            )

            return {
                "found": found,
                "episodic_memories": simplified_memories,
                'episodic_records': raw_memories,
                'warnings': warnings,
                "graph_entities": graph_context.get("entities", {}),
                "graph_relations": graph_context.get("relations", []),
                "query": query,
                "keywords": keywords,
            }

        except concurrent.futures.TimeoutError:
            logger.error("[query_memory] 检索超时")
            return {
                "found": False,
                "episodic_memories": [],
                "graph_entities": {},
                "graph_relations": [],
                "query": query,
                "keywords": keywords,
                "error": "检索超时",
            }
        except Exception as e:
            logger.exception(f"[query_memory] 检索失败: {e}")
            return {
                "found": False,
                "episodic_memories": [],
                "graph_entities": {},
                "graph_relations": [],
                "query": query,
                "keywords": keywords,
                "error": str(e),
            }

    def load_memory(self, content):
        """
        旧接口 - 通过 LLM 判断是否需要检索记忆
        保留兼容性，内部调用 query_memory
        """
        system_prompt = settings.CORE_PERSONA + "\n" + settings.MEMORY_JUDGE_PROMPT
        logger.info(f"Loading Memory\n")
        user_prompt = f"提供的日志如下：\n\n{content}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self.llm_client.one_chat(
            settings.SMALL_LLM, messages=messages, call_type="memory_query"
        )

        # 检查 LLM 响应是否为空
        if resp is None:
            logger.error(
                "LLM returned no response during memory loading (resp is None)"
            )
            return []

        resp_json = self.llm_client._extract_json(resp)
        if resp_json is None:
            logger.error(f"Failed to extract JSON from LLM response: {repr(resp)}")
            return []

        # LLM 判断不需要检索
        if resp_json.get("need_memory", False) is False:
            logger.info("LLM judged that no memory retrieval is needed.")
            return []

        try:
            keywords = resp_json.get("keywords", [])
            query = resp_json.get("query", "")
            entities = resp_json.get("entities", [])

            # 调用统一的检索方法
            result = self.query_memory(query, keywords, entities)

            if result.get("found"):
                logger.info(
                    f"Road Memory\nQuery: {query}\nKey Words: {keywords}\nEntities: {entities}\n"
                )
                # 返回旧格式（JSON字符串）保持兼容
                return json.dumps(result, ensure_ascii=False)
            return []

        except Exception as e:
            logger.error(f"load_memory 失败: {e}")
            return []

    def _get_graph_memory(self, entities: list) -> dict:
        """从 Neo4j 图谱中检索实体及关系"""
        if not self.graph_memory or not entities:
            return {"entities": [], "relations": []}

        try:
            result = self.graph_memory.query_entities_by_names_with_aliases(entities)
            logger.info(
                f"Graph query: found {len(result['entities'])} entities, {len(result['relations'])} relations"
            )
            return result
        except Exception as e:
            logger.error(f"Graph memory query failed: {e}")
            return {"entities": [], "relations": []}

    def _simplify_memories(self, memories):
        """压缩记忆格式：每条记忆一行，减少 JSON 语法开销
        格式：[时间] 内容
        """
        lines = []
        for mem in memories:
            content = mem.get("content", "")
            time = (mem.get('time') or '时间未知')[:19]
            lines.append(f"[{time}] {content}")
        return lines

    def _simplify_graph(
        self, graph_context: dict, query: str = "", key_words: list = None
    ) -> dict:
        """语境探照灯：为每个实体的碎片字段智能选取最相关片段

        返回压缩格式：
        - entities: {"名字": "bio内容", ...}  # 只保留有描述字段的
        - relations: ["A 关系 B", ...]  # 紧凑关系字符串
        """
        entities = {}
        kws = key_words or []
        for e in graph_context.get("entities", []):
            name = e.get("name", "")
            if not name:
                continue
            # 提取描述字段
            desc_parts = []
            for field in ("bio", "vibe", "utility", "significance"):
                val = e.get(field)
                if isinstance(val, list):
                    selected = self._select_relevant_fragments(val, query, kws)
                    if selected:
                        desc_parts.append("; ".join(selected)[:100])
                elif isinstance(val, str) and val:
                    desc_parts.append(val[:80])
            if desc_parts:
                entities[name] = " | ".join(desc_parts)

        # 压缩关系格式："A 关系 B"
        relations = []
        for r in graph_context.get("relations", []):
            src = r.get("source", "")
            tgt = r.get("target", "")
            rel = r.get("relation", "")
            if src and tgt and rel:
                relations.append(f"{src} {rel} {tgt}")

        return {"entities": entities, "relations": relations}

    def _select_relevant_fragments(
        self, fragments: list, query: str, key_words: list
    ) -> list:
        """从碎片列表选出最相关的片段

        策略：
          1. 锚点保留：始终保留第 0 条（核心身份定义）
          2. 语境扫描：对剩余碎片按关键词命中评分
             - 内容段（类别|时间|内容 的第3段）命中 +2
             - 类别段（第1段）命中 +1
          3. 取得分 > 0 的最高 2 条；得分均为 0 则取最新 2 条（末尾）
        碎片格式：类别|时间|内容
        """
        if not fragments:
            return []

        anchor = fragments[0]
        rest = fragments[1:]

        if not rest:
            return [anchor]

        # 构建关键词集合（key_words 直接用；query 按空白分词补充；≥2 字）
        kws = set(w.lower() for w in key_words if len(w) >= 2)
        kws.update(w.lower() for w in query.split() if len(w) >= 2)

        def score_frag(frag: str) -> int:
            parts = frag.split("|", 2)
            category = parts[0].lower() if len(parts) >= 1 else ""
            content = parts[2].lower() if len(parts) == 3 else frag.lower()
            s = 0
            for kw in kws:
                if kw in content:
                    s += 2
                if kw in category:
                    s += 1
            return s

        scored = [(score_frag(f), f) for f in rest]
        scored.sort(key=lambda x: x[0], reverse=True)

        matched = [f for sc, f in scored if sc > 0]
        if matched:
            tail = matched[:2]
        else:
            tail = rest[-2:]  # 兜底：最新 2 条

        return [anchor] + tail

    def _get_persistence_memory(self, query_data):
        future = concurrent.futures.Future()

        def on_memory_retrieved(memories):
            if not future.done():
                logger.info(f"Retrieved relevant memories: {len(memories)} items")
                future.set_result(memories)

        query_data["callback"] = on_memory_retrieved
        self.event_bus.publish(Event("memory.query", query_data))
        try:
            result = future.result(timeout=5)
            if isinstance(result, dict) and result.get('error'):
                raise RuntimeError(result['error'])
            return result
        except concurrent.futures.TimeoutError:
            logger.error("查询持久化记忆超时，跳过查询以维持对话。")
            raise RuntimeError('长期记忆检索超时')
        except Exception as e:
            logger.error(f"查询持久化记忆时发生异常: {e}")
            raise
