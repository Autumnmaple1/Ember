"""
工具调用处理器

提供独立的工具调用处理能力，可被 Brain、StateManager 等组件复用。
"""

import concurrent.futures
import json
import logging
import re
from typing import Optional, List, TYPE_CHECKING

from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from tools.builtin.memory_query_tool import MemoryQueryTool

if TYPE_CHECKING:
    from memory.memory_process import Hippocampus
    from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class ToolCallProcessor:
    """
    独立的工具调用处理器

    提供：
    - 从文本中提取工具调用
    - 并行执行工具调用
    - 格式化工具结果
    - 移除工具调用标签

    可被 Brain、StateManager 等组件复用。
    """

    TOOL_CALL_PATTERN = re.compile(
        r"<tool>\s*(.*?)\s*</tool>",
        re.DOTALL | re.IGNORECASE,
    )

    DEFAULT_MAX_CALLS = 3
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        tool_registry: ToolRegistry,
        max_calls: int = DEFAULT_MAX_CALLS,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """
        初始化处理器

        Args:
            tool_registry: 工具注册中心
            max_calls: 单次最大调用次数
            timeout: 单个工具超时时间
        """
        self.tool_registry = tool_registry
        self.tool_executor = ToolExecutor(tool_registry)
        self.max_calls = max_calls
        self.timeout = timeout
        # 缓存增强后的 system prompt，避免每次请求都重新构建
        self._cached_prompt: Optional[str] = None
        self._cached_base_prompt: Optional[str] = None

    @classmethod
    def create_with_memory_tool(
        cls,
        hippocampus: "Hippocampus",
        max_calls: int = DEFAULT_MAX_CALLS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "ToolCallProcessor":
        """
        创建带有 memory_query 工具的处理器，并自动发现插件工具

        Args:
            hippocampus: 海马体实例
            max_calls: 最大调用次数
            timeout: 超时时间

        Returns:
            配置好的处理器实例
        """
        from tools.plugin import auto_discover_tools

        registry = ToolRegistry()
        if hippocampus:
            registry.register(MemoryQueryTool(hippocampus=hippocampus))
            logger.info("已注册内置工具: memory_query")

        # 自动发现 plugins 目录下的工具
        plugin_count = auto_discover_tools(registry)
        if plugin_count > 0:
            logger.info(f"自动发现并注册了 {plugin_count} 个插件工具")

        return cls(registry, max_calls, timeout)

    def extract_tool_calls(self, text: str) -> List[dict]:
        """
        从文本中提取工具调用

        Args:
            text: LLM 输出的文本

        Returns:
            工具调用列表 [{"name": str, "parameters": dict}]
        """
        calls = []

        for match in self.TOOL_CALL_PATTERN.finditer(text):
            try:
                call_data = json.loads(match.group(1))
                if "name" in call_data:
                    calls.append(
                        {
                            "name": call_data["name"],
                            "parameters": call_data.get("parameters", {}),
                        }
                    )
            except json.JSONDecodeError as e:
                logger.warning(f"JSON格式解析工具调用失败: {e}")

        return calls

    def has_tool_calls(self, text: str) -> bool:
        """
        检查文本中是否包含工具调用

        Args:
            text: 文本内容

        Returns:
            是否包含工具调用
        """
        return bool(self.TOOL_CALL_PATTERN.search(text))

    def remove_tool_calls(self, text: str) -> str:
        """
        从文本中移除工具调用标签

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        text = self.TOOL_CALL_PATTERN.sub("", text)
        return text.strip()

    def execute_tool_calls(
        self, calls: List[dict], caller: str = "unknown", cancel_check=None
    ) -> List[dict]:
        """
        并行执行工具调用列表

        Args:
            calls: 工具调用列表 [{"name": str, "parameters": dict}]
            caller: 调用来源标识（如 "Brain._llm_speak", "StateManager._update_state_due_to_idle"）

        Returns:
            执行结果列表 [{"tool_name": str, "result": ToolResult}]
        """
        if not calls:
            return []

        results = []

        # 检查调用数量限制
        if len(calls) > self.max_calls:
            logger.warning(
                f"[{caller}] 工具调用数量 {len(calls)} 超过限制 {self.max_calls}，"
                f"只执行前 {self.max_calls} 个"
            )
            calls = calls[: self.max_calls]

        # 记录调用来源
        tool_names = [c["name"] for c in calls]
        logger.info(f"[{caller}] 开始执行 {len(calls)} 个工具调用: {tool_names}")

        # 并行执行所有工具调用
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
            # 提交所有任务
            future_to_call = {}
            for call in calls:
                if cancel_check:
                    cancel_check()
                future = executor.submit(
                    self.tool_executor.execute, call["name"], call["parameters"]
                )
                future_to_call[future] = call

            # 收集结果
            for future in concurrent.futures.as_completed(future_to_call):
                call = future_to_call[future]
                try:
                    result = future.result(timeout=self.timeout)
                    results.append(
                        {
                            "tool_name": call["name"],
                            "result": result,
                        }
                    )
                    logger.info(f"[{caller}] 工具 {call['name']} 执行完成")
                except Exception as e:
                    logger.error(f"[{caller}] 工具 {call['name']} 执行失败: {e}")
                    results.append(
                        {
                            "tool_name": call["name"],
                            "result": ToolResult.fail(f"执行异常: {str(e)}"),
                        }
                    )

        return results

    def format_tool_results_for_prompt(self, results: List[dict]) -> str:
        """
        格式化工具结果为 prompt 文本

        Args:
            results: 工具执行结果

        Returns:
            格式化后的文本
        """
        if not results:
            return ""

        lines = ["\n[工具执行结果]"]

        for item in results:
            tool_name = item["tool_name"]
            result = item["result"]

            if result.success:
                # 尝试使用工具的摘要方法
                tool = self.tool_registry.get(tool_name)
                if tool and hasattr(tool, 'format_result_for_model'):
                    lines.append(f"- {tool_name}: {tool.format_result_for_model(result)}")
                elif tool:
                    summary = tool.summarize_result(result, max_length=200)
                    lines.append(f"- {tool_name}: {summary}")
                else:
                    data_str = json.dumps(result.data, ensure_ascii=False, default=str)
                    if len(data_str) > 200:
                        data_str = data_str[:200] + "..."
                    lines.append(f"- {tool_name}: {data_str}")
            else:
                lines.append(f"- {tool_name}: 失败 - {result.error}")

        return "\n".join(lines)

    def process_llm_output(
        self, text: str, execute: bool = True, caller: str = "unknown"
    ) -> dict:
        """
        处理 LLM 输出的完整流程

        Args:
            text: LLM 输出的文本
            execute: 是否执行工具调用
            caller: 调用来源标识

        Returns:
            {
                "has_tool_calls": bool,
                "tool_calls": List[dict],
                "tool_results": List[dict],
                "results_text": str,
                "clean_text": str,
            }
        """
        tool_calls = self.extract_tool_calls(text)
        has_tool_calls = bool(tool_calls)

        if not has_tool_calls:
            return {
                "has_tool_calls": False,
                "tool_calls": [],
                "tool_results": [],
                "results_text": "",
                "clean_text": text,
            }

        tool_results = []
        results_text = ""

        if execute:
            tool_results = self.execute_tool_calls(tool_calls, caller=caller)
            results_text = self.format_tool_results_for_prompt(tool_results)

        clean_text = self.remove_tool_calls(text)

        return {
            "has_tool_calls": True,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "results_text": results_text,
            "clean_text": clean_text,
        }

    def build_system_prompt_with_tools(self, base_prompt: str) -> str:
        """
        构建包含工具说明的 system prompt（带缓存）

        Args:
            base_prompt: 基础 system prompt

        Returns:
            增强后的 system prompt
        """
        # 检查缓存是否有效
        if self._cached_prompt and self._cached_base_prompt == base_prompt:
            return self._cached_prompt

        if len(self.tool_registry) == 0:
            self._cached_base_prompt = base_prompt
            self._cached_prompt = base_prompt
            return base_prompt

        tool_guidelines = self.tool_registry.get_tools_description_for_prompt(
            compact=True,
            include_examples=True,
        )

        result = f"""{base_prompt}

{tool_guidelines}
"""

        # 更新缓存
        self._cached_base_prompt = base_prompt
        self._cached_prompt = result
        logger.debug("[ToolProcessor] 已缓存增强后的 System Prompt")

        return result
