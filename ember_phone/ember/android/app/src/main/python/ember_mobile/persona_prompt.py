"""兼容旧导入路径；提示词原文统一来自电脑端 prompts.yaml。"""

from .prompt_store import CORE_PERSONA, SYSTEM_PROMPT


__all__ = ["CORE_PERSONA", "SYSTEM_PROMPT"]
