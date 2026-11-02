"""
JSON 文件导出器

负责导出配置文件数据 (state.json, chat_memory.json)
"""

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional
import logging

from archive.exporters.base import BaseExporter, ExportResult

logger = logging.getLogger(__name__)


class JsonExporter(BaseExporter):
    """JSON 配置文件导出器"""

    name = "json"
    description = "JSON 配置文件导出器"

    # 需要导出的配置文件
    CONFIG_FILES = [
        ("working_memory.json", "working_memory.json"),
        ("experience.json", "experience.json"),
        ("state.json", "state.json"),
        ("chat_memory.json", "chat_memory.json"),
        ("chat_history.log", "chat_history.log"),
    ]

    def __init__(self, output_dir: str, config_dir: str = "./config"):
        """
        初始化 JSON 导出器

        Args:
            output_dir: 输出目录
            config_dir: 配置文件目录
        """
        super().__init__(output_dir)
        self.config_dir = config_dir

    def export(self) -> ExportResult:
        """导出 JSON 配置文件"""
        try:
            exported_files = []
            stats = {"files_count": 0, "total_size": 0}

            for source_name, target_name in self.CONFIG_FILES:
                source_path = Path(self.config_dir) / source_name
                target_path = Path(self.output_dir) / target_name

                if source_path.exists():
                    # 直接复制文件
                    shutil.copy2(source_path, target_path)
                    file_size = source_path.stat().st_size

                    exported_files.append(target_name)
                    stats["files_count"] += 1
                    stats["total_size"] += file_size

                    self.logger.debug(f"已导出: {source_name} -> {target_name}")
                else:
                    self.logger.warning(f"配置文件不存在，跳过: {source_name}")

            return ExportResult(
                success=True,
                data=exported_files,
                stats=stats,
            )

        except Exception as e:
            self.logger.error(f"导出 JSON 文件失败: {e}")
            return ExportResult(success=False, error=str(e))

    def export_state(self) -> Optional[dict]:
        """仅导出状态数据"""
        try:
            state_path = Path(self.config_dir) / "state.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None
        except Exception as e:
            self.logger.error(f"读取状态文件失败: {e}")
            return None

    def export_chat_memory(self) -> Optional[list]:
        """仅导出短期记忆数据"""
        try:
            memory_path = Path(self.config_dir) / "chat_memory.json"
            if memory_path.exists():
                with open(memory_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return []
        except Exception as e:
            self.logger.error(f"读取短期记忆文件失败: {e}")
            return None
