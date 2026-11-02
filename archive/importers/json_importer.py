"""
JSON 文件导入器

负责导入配置文件数据 (state.json, chat_memory.json)
"""

import json
import shutil
from pathlib import Path
from typing import Optional
import logging

from archive.importers.base import BaseImporter, ImportResult

logger = logging.getLogger(__name__)


class JsonImporter(BaseImporter):
    """JSON 配置文件导入器"""

    name = "json"
    description = "JSON 配置文件导入器"

    # 需要导入的配置文件
    CONFIG_FILES = [
        ("working_memory.json", "working_memory.json"),
        ("experience.json", "experience.json"),
        ("state.json", "state.json"),
        ("chat_memory.json", "chat_memory.json"),
        ("chat_history.log", "chat_history.log"),
    ]

    def __init__(self, source_dir: str, config_dir: str = "./config"):
        """
        初始化 JSON 导入器

        Args:
            source_dir: 源目录 (解压后的存档目录)
            config_dir: 配置文件目标目录
        """
        super().__init__(source_dir)
        self.config_dir = config_dir

    def import_data(self) -> ImportResult:
        """导入 JSON 配置文件"""
        try:
            imported_files = []
            cleared_files = []
            stats = {"files_count": 0, "cleared_count": 0}

            for source_name, target_name in self.CONFIG_FILES:
                source_path = Path(self.source_dir) / source_name
                target_path = Path(self.config_dir) / target_name

                if source_path.exists():
                    # 备份现有文件
                    if target_path.exists():
                        backup_path = target_path.with_suffix(".json.bak")
                        shutil.copy2(target_path, backup_path)
                        self.logger.debug(f"已备份: {target_name}")

                    # 复制文件
                    shutil.copy2(source_path, target_path)
                    imported_files.append(target_name)
                    stats["files_count"] += 1

                    self.logger.debug(f"已导入: {source_name} -> {target_name}")
                else:
                    # 存档中没有该文件，清空目标文件
                    if target_path.exists():
                        if target_name.endswith(".json"):
                            # JSON 文件写入空对象或空数组
                            with open(target_path, "w", encoding="utf-8") as f:
                                if target_name == "chat_memory.json":
                                    f.write("[]")
                                else:
                                    f.write("{}")
                        else:
                            # 非JSON文件（如 .log）直接清空
                            with open(target_path, "w", encoding="utf-8") as f:
                                f.write("")
                        cleared_files.append(target_name)
                        stats["cleared_count"] += 1
                        self.logger.info(f"已清空: {target_name}（存档中无此文件）")

            return ImportResult(
                success=True,
                message=f"已导入 {stats['files_count']} 个配置文件，清空 {stats['cleared_count']} 个",
                data={"imported": imported_files, "cleared": cleared_files},
                stats=stats,
            )

        except Exception as e:
            self.logger.error(f"导入 JSON 文件失败: {e}")
            return ImportResult(success=False, error=str(e))

    def import_state(self) -> Optional[dict]:
        """仅导入状态数据"""
        try:
            source_path = Path(self.source_dir) / "state.json"
            if source_path.exists():
                with open(source_path, "r", encoding="utf-8") as f:
                    state = json.load(f)

                # 写入目标位置
                target_path = Path(self.config_dir) / "state.json"
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)

                return state
            return None
        except Exception as e:
            self.logger.error(f"导入状态文件失败: {e}")
            return None

    def import_chat_memory(self) -> Optional[list]:
        """仅导入短期记忆数据"""
        try:
            source_path = Path(self.source_dir) / "chat_memory.json"
            if source_path.exists():
                with open(source_path, "r", encoding="utf-8") as f:
                    memory = json.load(f)

                # 写入目标位置
                target_path = Path(self.config_dir) / "chat_memory.json"
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(memory, f, ensure_ascii=False, indent=2)

                return memory
            return []
        except Exception as e:
            self.logger.error(f"导入短期记忆文件失败: {e}")
            return None
