from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import IntEnum
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen


class ToolPermission(IntEnum):
    READONLY = 0
    READWRITE = 1
    DESTRUCTIVE = 2


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: Any = None, **metadata) -> "ToolResult":
        return cls(True, data=data, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata) -> "ToolResult":
        return cls(False, error=error, metadata=metadata)


class BaseTool(ABC):
    name = ""
    description = ""
    short_description = ""
    permission = ToolPermission.READONLY
    timeout = 20.0
    summarize_limit = 200
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    examples: list[dict[str, Any]] = []

    def __init__(self) -> None:
        if not self.name or not self.description:
            raise ValueError("工具必须声明 name 和 description")

    def validate_params(self, params: Any) -> tuple[bool, str | None]:
        if not isinstance(params, dict):
            return False, "parameters 必须是 JSON 对象"
        properties = self.parameters.get("properties", {})
        for name in self.parameters.get("required", []):
            if name not in params:
                return False, f"缺少必需参数: {name}"
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, value in params.items():
            expected = properties.get(name, {}).get("type")
            python_type = type_map.get(expected)
            if python_type is not None and not isinstance(value, python_type):
                return False, f"参数 {name} 类型错误，期望 {expected}"
        return True, None

    def prompt_description(self) -> str:
        description = self.short_description or self.description[:30]
        required = self.parameters.get("required", [])
        required_text = f" 必需:{','.join(required)}" if required else ""
        return f"- {self.name}: {description}{required_text}"

    def examples_text(self) -> str:
        if not self.examples:
            return ""
        lines = ["  示例:"]
        for example in self.examples[:2]:
            user_message = example.get("user", "")
            params = json.dumps(
                example.get("parameters", {}), ensure_ascii=False
            )
            lines.append(f"    用户: {user_message}")
            lines.append(
                f'    调用: <tool>{{"name": "{self.name}", '
                f'"parameters": {params}}}</tool>'
            )
        return "\n".join(lines)

    def summarize(self, result: ToolResult, max_length: int = 1200) -> str:
        if not result.success:
            return f"执行失败：{result.error}"
        if isinstance(result.data, str):
            text = result.data
        else:
            text = json.dumps(result.data, ensure_ascii=False, default=str)
        return text if len(text) <= max_length else text[:max_length] + "…"

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def prompt(self) -> str:
        if not self._tools:
            return ""
        tools = list(self._tools.values())
        lines = ["【可用工具】"]
        lines.extend(tool.prompt_description() for tool in tools)
        lines.extend(["", "【使用示例】"])
        for tool in tools:
            example = tool.examples_text()
            if example:
                lines.append(example)
        lines.extend([
            "",
            "【格式规范】",
            '- 工具调用: <tool>{"name": "工具名", "parameters": {...}}</tool>',
            "- 参数必须符合工具要求",
            '- 请直接在 <tool> 标签内输出 JSON 对象，严禁使用 <arg_value> 或任何其他嵌套标签。输出格式必须严格遵守：<tool>{"name":...}</tool>，不要在中间插入任何额外字符。',
            "- 注意，如果当前对话的上下文不足以让你生成自然的回答，你应该优先使用工具来获取信息或进行操作，而不是直接回复",
            "- 请在一次对话内调用完所有的工具，最多可以调用5个工具",
            "- 调用工具的信息需要单独发送",
            "- 如果你检测到对话历史中已有工具调用，你必须立刻根据工具结果生成回复，【绝对禁止】再次调用任何工具。",
            "- 请确保<tool></tool>的工具调用紧接在<thought>之后，以便于筛除。",
        ])
        return "\n".join(lines)

    def names(self) -> list[str]:
        return list(self._tools)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        max_permission: ToolPermission = ToolPermission.READONLY,
    ) -> None:
        self.registry = registry
        self.max_permission = max_permission
        self._pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="ember-tool")

    def execute(self, name: str, params: Any) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult.fail(f"工具未注册: {name}")
        if tool.permission > self.max_permission:
            return ToolResult.fail(f"工具权限不足: {name}")
        valid, error = tool.validate_params(params)
        if not valid:
            return ToolResult.fail(error or "参数验证失败")
        future = self._pool.submit(tool.execute, params)
        try:
            return future.result(timeout=max(1.0, float(tool.timeout)))
        except FutureTimeoutError:
            future.cancel()
            return ToolResult.fail(f"工具执行超时: {name}", timeout=True)
        except Exception as error:
            return ToolResult.fail(f"工具执行异常: {type(error).__name__}: {error}")

    def execute_many(
        self,
        calls: list[dict[str, Any]],
    ) -> list[ToolResult]:
        scheduled = []
        for call in calls:
            name = call.get("name", "")
            params = call.get("parameters", {})
            tool = self.registry.get(name)
            if tool is None:
                scheduled.append(ToolResult.fail(f"工具未注册: {name}"))
                continue
            if tool.permission > self.max_permission:
                scheduled.append(ToolResult.fail(f"工具权限不足: {name}"))
                continue
            valid, error = tool.validate_params(params)
            if not valid:
                scheduled.append(ToolResult.fail(error or "参数验证失败"))
                continue
            scheduled.append((tool, self._pool.submit(tool.execute, params)))

        results = []
        for item in scheduled:
            if isinstance(item, ToolResult):
                results.append(item)
                continue
            tool, future = item
            try:
                results.append(
                    future.result(timeout=max(1.0, float(tool.timeout)))
                )
            except FutureTimeoutError:
                future.cancel()
                results.append(
                    ToolResult.fail(
                        f"工具执行超时: {tool.name}", timeout=True
                    )
                )
            except Exception as error:
                results.append(
                    ToolResult.fail(
                        f"工具执行异常: {type(error).__name__}: {error}"
                    )
                )
        return results

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class ToolCallProcessor:
    TOOL_PATTERN = re.compile(r"<tool>\s*(.*?)\s*</tool>", re.I | re.S)

    def __init__(self, registry: ToolRegistry, max_calls: int = 3) -> None:
        self.registry = registry
        self.executor = ToolExecutor(registry)
        self.max_calls = int(max_calls)

    def extract(self, text: str) -> list[dict[str, Any]]:
        calls = []
        for match in self.TOOL_PATTERN.finditer(text):
            try:
                value = json.loads(match.group(1))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                continue
            params = value.get("parameters", {})
            calls.append({"name": value["name"], "parameters": params})
        return calls[: self.max_calls]

    def remove_calls(self, text: str) -> str:
        return self.TOOL_PATTERN.sub("", text).strip()

    def execute(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limited_calls = calls[: self.max_calls]
        results = self.executor.execute_many(limited_calls)
        return [
            {
                "name": call["name"],
                "parameters": call.get("parameters", {}),
                "result": result,
            }
            for call, result in zip(limited_calls, results)
        ]

    def results_prompt(self, results: list[dict[str, Any]]) -> str:
        lines = ["\n[工具执行结果]"]
        for item in results:
            tool = self.registry.get(item["name"])
            result = item["result"]
            summary = (
                tool.summarize(result, tool.summarize_limit)
                if tool
                else str(result.error)
            )
            if result.success:
                lines.append(f"- {item['name']}: {summary}")
            else:
                lines.append(f"- {item['name']}: 失败 - {result.error}")
        return "\n".join(lines)

    @staticmethod
    def public_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "parameters": item["parameters"],
                "success": item["result"].success,
                "error": item["result"].error,
            }
            for item in results
        ]

    def shutdown(self) -> None:
        self.executor.shutdown()


class MemoryQueryTool(BaseTool):
    name = "memory_query"
    description = "检索长期记忆，获取与当前话题相关的历史信息。适用于用户询问过去的事、特定实体、或需要维持长期人设连贯性时。"
    short_description = "注意，上下文不代表你的所有记忆，如果你发现当前的上下文不足以回应用户的对话，或者用户询问了过去的经历、特定实体的信息，或者你需要维持长期人设的连贯性，请使用这个工具来检索相关的长期历史记忆。"
    timeout = 15.0
    summarize_limit = 3000
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "待检索的历史记忆内容描述，使用完整的陈述句描述目标记忆。例如：'第一次在南京大学见面时的场景和对话'、'马骏老师问题求解课程的上课地点'。**必须包含具体的人名、地名、时间等关键信息**。",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关键词和实体列表（10个以内）。**必须**包含**具体的**人名、地名、物品、事件、情感词等。例如：['南京大学', '第一次', '喜欢', '依鸣', '逸夫馆']。用于语义匹配和图谱查询。",
            },
        },
        "required": ["query", "keywords"],
    }
    examples = [
        {
            "scenario": "用户询问课程地点",
            "parameters": {
                "query": "马骏老师的问题求解课程在哪个教室上课",
                "keywords": ["马骏", "问题求解", "教室", "上课地点", "逸夫馆"],
            },
        },
        {
            "scenario": "用户询问过去的经历",
            "parameters": {
                "query": "开学第一天在南京大学帮忙抬行李的场景",
                "keywords": ["开学", "南京大学", "行李", "帮忙", "九月", "初遇"],
            },
        },
    ]

    def __init__(self, memory_manager) -> None:
        super().__init__()
        self._memory_manager = memory_manager

    def execute(self, params: dict[str, Any]) -> ToolResult:
        query = str(params.get("query", "")).strip()
        keywords = [
            str(value).strip()
            for value in params.get("keywords", [])[:10]
            if str(value).strip()
        ]
        if not query:
            return ToolResult.fail("query 不能为空")
        result = self._memory_manager.query_memory(query, keywords, keywords)
        return ToolResult.ok(result)

    def summarize(self, result: ToolResult, max_length: int = 200) -> str:
        if not result.success:
            return f"检索失败: {result.error}"
        # 命中时全给：返回完整记忆内容（含情景记忆、实体、关系），
        # 让对话模型基于完整上下文作答，避免用残缺片段脑补。
        text = str(result.data or "").strip()
        if not text:
            return "未找到相关记忆"
        return text[:max_length]


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        classes = values.get("class", "").split()
        if tag == "a" and "result__a" in classes and len(self.results) < self.limit:
            self._current = {"title": "", "snippet": "", "url": values.get("href", "")}
            self._field = "title"
        elif self._current is not None and "result__snippet" in classes:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None and self._field == "title":
            self._field = None
        elif self._current is not None and self._field == "snippet" and tag in {"a", "div"}:
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] += data

    def close(self) -> None:
        self._finish_current()
        super().close()

    def _finish_current(self) -> None:
        if self._current is None:
            return
        item = {key: value.strip() for key, value in self._current.items()}
        if item["title"]:
            parsed = urlparse(item["url"])
            redirect = parse_qs(parsed.query).get("uddg")
            if redirect:
                item["url"] = unquote(redirect[0])
            self.results.append(item)
        self._current = None
        self._field = None


class WebSearchTool(BaseTool):
    name = "search_web"
    description = "搜索互联网以获取最新新闻、天气资讯或专业知识百科。当你遇到不知道的事情时调用。"
    short_description = "检索互联网最新信息"
    timeout = 15.0
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要搜索的关键词或问题，尽量精简提取核心实体",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认3",
            },
        },
        "required": ["query"],
    }
    examples = [
        {
            "scenario": "查询今日南京天气",
            "parameters": {"query": "南京 今日 天气"},
        },
        {
            "scenario": "了解大模型最新发布情况",
            "parameters": {"query": "AI LLM 最新发布 模型 2024"},
        },
    ]

    def execute(self, params: dict[str, Any]) -> ToolResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult.fail("query 不能为空")
        limit = max(1, min(int(params.get("max_results", 3)), 5))
        request = Request(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            headers={
                "User-Agent": "Mozilla/5.0 (Android) Ember/1.0",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                html = response.read().decode("utf-8", errors="replace")
            parser = _DuckDuckGoParser(limit)
            parser.feed(html)
            parser.close()
        except Exception as error:
            return ToolResult.fail(f"网页搜索失败: {error}")
        return ToolResult.ok(parser.results or "未找到相关网页结果")

    def summarize(self, result: ToolResult, max_length: int = 1800) -> str:
        if not result.success:
            return f"搜索失败: {result.error}"
        if isinstance(result.data, str):
            return result.data
        lines = ["互联网搜索结果摘要："]
        for index, item in enumerate(result.data, 1):
            lines.append(f"[{index}] {item['title']}: {item['snippet']}")
        text = "\n".join(lines)
        return text if len(text) <= max_length else text[:max_length] + "..."


def create_mobile_tool_processor(memory_manager) -> ToolCallProcessor:
    registry = ToolRegistry()
    registry.register(MemoryQueryTool(memory_manager))
    registry.register(WebSearchTool())
    # 与电脑端 Brain.MAX_TOOL_CALLS_PER_TURN=5 一致
    return ToolCallProcessor(registry, max_calls=5)
