from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re


_BLOCK_HEADER = re.compile(r"^([a-z][a-z0-9_]*):\s*\|\s*$")


@lru_cache(maxsize=1)
def load_desktop_prompts() -> dict[str, str]:
    """读取随 APK 打包的电脑端 prompts.yaml，不对提示词内容做改写。"""
    path = Path(__file__).with_name("prompts.yaml")
    lines = path.read_text(encoding="utf-8").splitlines()
    prompts: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = _BLOCK_HEADER.match(lines[index])
        if match is None:
            index += 1
            continue
        key = match.group(1)
        index += 1
        block: list[str] = []
        while index < len(lines) and _BLOCK_HEADER.match(lines[index]) is None:
            line = lines[index]
            if line.startswith("  "):
                block.append(line[2:])
            elif not line.strip():
                block.append("")
            else:
                raise ValueError(f"不支持的 prompts.yaml 行: {line}")
            index += 1
        while block and block[-1] == "":
            block.pop()
        prompts[key] = "\n".join(block) + "\n"
    return prompts


def desktop_prompt(name: str) -> str:
    try:
        return load_desktop_prompts()[name]
    except KeyError as error:
        raise KeyError(f"电脑端提示词不存在: {name}") from error


CORE_PERSONA = desktop_prompt("core_persona")
SYSTEM_RULES_PROMPT = desktop_prompt("system_prompt")
# 与电脑端 config.settings 完全一致：SYSTEM_PROMPT 已包含 CORE_PERSONA。
SYSTEM_PROMPT = CORE_PERSONA + SYSTEM_RULES_PROMPT
STATE_UPDATE_PROMPT = desktop_prompt("state_update_prompt")
IDLE_STATE_UPDATE_PROMPT = desktop_prompt("idle_state_update_prompt")
IDLE_SPEAKING_UPDATE_PROMPT = desktop_prompt("idle_speaking_update_prompt")
MEMORY_ENCODING_PROMPT = desktop_prompt("memory_encoding_prompt")
MEMORY_JUDGE_PROMPT = desktop_prompt("memory_judge_prompt")
GRAPH_CONSOLIDATION_PROMPT = desktop_prompt("graph_consolidation_prompt")
PRE_ROUTING_PROMPT = desktop_prompt("pre_routing_prompt")
