"""
存档管理器

提供完整的存档创建、加载、管理功能。
"""

import os
import json
import shutil
import tempfile
import threading
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Callable

from archive.models import ArchiveManifest, ArchiveSlot, ArchiveStats, ArchiveResult
from archive.exceptions import (
    ArchiveError,
    ArchiveNotFoundError,
    ArchiveCorruptedError,
    ArchiveVersionError,
    ArchiveInProgressError,
)
from archive.exporters import JsonExporter, PostgresExporter
from archive.importers import JsonImporter, PostgresImporter
from archive.utils.compress import compress_archive, extract_archive, get_archive_size
from archive.utils.validate import validate_archive, calculate_dir_checksum
from archive.utils.compat import check_version_compatibility, get_current_version

logger = logging.getLogger(__name__)


class ArchiveManager:
    """
    存档管理器

    负责存档的创建、加载、删除和列表管理。

    使用示例:
        manager = ArchiveManager(event_bus, hippocampus)

        # 创建存档
        result = manager.create_archive("slot_1", "图书馆初遇")

        # 加载存档
        result = manager.load_archive("slot_1")

        # 列出存档
        slots = manager.list_archives()
    """

    ARCHIVE_EXTENSION = ".ember"
    ARCHIVE_DIR = "data/archives"
    CONFIG_DIR = "config"

    def __init__(
        self,
        event_bus=None,
        hippocampus=None,
        heartbeat=None,
        state_manager=None,
        short_term_memory=None,
        episodic_memory=None,
        db_memory=None,
    ):
        """
        初始化存档管理器

        Args:
            event_bus: 事件总线 (可选)
            hippocampus: 海马体实例 (可选)
            heartbeat: 心跳实例 (可选)
            state_manager: 状态管理器 (可选)
            short_term_memory: 短期记忆实例 (可选)
            episodic_memory: 情景记忆实例 (可选)
            db_memory: 数据库记忆实例 (可选)
        """
        self.event_bus = event_bus
        self.hippocampus = hippocampus
        self.heartbeat = heartbeat
        self.state_manager = state_manager
        self.short_term_memory = short_term_memory
        self.episodic_memory = episodic_memory
        self.db_memory = db_memory

        self.archive_dir = Path(self.ARCHIVE_DIR)
        self.config_dir = Path(self.CONFIG_DIR)

        # 确保存档目录存在
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        # 操作锁
        self._lock = threading.Lock()
        self._in_progress = False
        self._current_operation = ""

        # 进度回调
        self._progress_callback: Optional[Callable[[str, int], None]] = None

    def set_progress_callback(self, callback: Callable[[str, int], None]):
        """
        设置进度回调函数

        Args:
            callback: 回调函数 (message: str, progress: int 0-100)
        """
        self._progress_callback = callback

    def _report_progress(self, message: str, progress: int, details: dict = None):
        """
        报告进度

        Args:
            message: 进度消息
            progress: 进度百分比 (0-100)
            details: 详细信息
        """
        if self._progress_callback:
            self._progress_callback(message, progress)

        detail_str = f" | {details}" if details else ""
        logger.info(f"[Archive] {message} ({progress}%{detail_str})")

    def _publish_event(self, event_name: str, data: dict = None):
        """发布事件"""
        if self.event_bus:
            from core.event_bus import Event

            self.event_bus.publish(Event(name=event_name, data=data or {}))

    def create_archive(
        self,
        slot_name: str,
        description: str = "",
    ) -> ArchiveResult:
        """
        创建存档

        Args:
            slot_name: 存档槽名称
            description: 存档描述

        Returns:
            ArchiveResult 创建结果
        """
        # 检查是否有操作正在进行
        with self._lock:
            if self._in_progress:
                raise ArchiveInProgressError(self._current_operation)
            self._in_progress = True
            self._current_operation = "create"

        try:
            self._publish_event("archive.start", {"slot_name": slot_name})
            self._report_progress("开始创建存档", 0)

            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Step 1: 暂停系统 (0-5%)
                self._pause_system()
                self._report_progress("暂停系统组件", 5)

                try:
                    # Step 2: 导出 JSON 配置 (5-15%)
                    self._report_progress("导出配置文件", 10)
                    json_exporter = JsonExporter(str(temp_path), str(self.config_dir))
                    json_result = json_exporter.run()
                    if not json_result.success:
                        raise ArchiveError(f"导出配置文件失败: {json_result.error}")
                    self._report_progress(
                        "配置文件导出完成",
                        15,
                        {"files": json_result.stats.get("files_count", 0)},
                    )

                    # Step 3: 导出 PostgreSQL 数据（包含实体关系图谱）
                    self._report_progress("导出数据库", 20)
                    pg_exporter = PostgresExporter(str(temp_path))
                    pg_result = pg_exporter.run()
                    if not pg_result.success:
                        logger.warning(f"导出数据库部分失败: {pg_result.error}")
                    self._report_progress(
                        "数据库导出完成",
                        55,
                        {
                            "tables": pg_result.stats.get("tables_count", 0),
                            "rows": pg_result.stats.get("total_rows", 0),
                        },
                    )

                    # Step 4: 生成元数据
                    self._report_progress("生成元数据", 78)
                    table_rows = pg_result.stats.get("table_rows", {})
                    manifest = self._create_manifest(
                        description=description,
                        stats=ArchiveStats(
                            message_count=table_rows.get("message_list", 0),
                            memory_count=table_rows.get("episodic_memory", 0),
                            entity_count=table_rows.get("knowledge_entities", 0),
                            relation_count=table_rows.get("knowledge_relations", 0),
                        ),
                    )

                    # 计算校验和
                    manifest.checksum = calculate_dir_checksum(
                        temp_dir,
                        exclude_files=["manifest.json"],
                    )

                    # 写入 manifest
                    manifest_path = temp_path / "manifest.json"
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        f.write(manifest.to_json())
                    self._report_progress("元数据生成完成", 80)

                    # Step 5: 压缩打包
                    self._report_progress("压缩存档", 85)
                    archive_path = (
                        self.archive_dir / f"{slot_name}{self.ARCHIVE_EXTENSION}"
                    )
                    if not compress_archive(temp_dir, str(archive_path)):
                        raise ArchiveError("压缩存档失败")

                    # 获取存档大小
                    archive_size = get_archive_size(str(archive_path))
                    self._report_progress(
                        "存档压缩完成", 95, {"size": f"{archive_size / 1024:.1f}KB"}
                    )

                    self._report_progress("存档创建完成", 100)

                    self._publish_event(
                        "archive.complete",
                        {
                            "slot_name": slot_name,
                            "manifest": manifest.to_dict(),
                        },
                    )

                    return ArchiveResult(
                        success=True,
                        message=f"存档 '{slot_name}' 创建成功",
                        slot_name=slot_name,
                        manifest=manifest,
                    )

                finally:
                    # 恢复系统
                    self._resume_system()

        except ArchiveError:
            raise
        except Exception as e:
            logger.error(f"创建存档失败: {e}")
            self._publish_event("archive.error", {"error": str(e)})
            return ArchiveResult(
                success=False,
                message=f"创建存档失败: {e}",
                error=str(e),
            )
        finally:
            with self._lock:
                self._in_progress = False
                self._current_operation = ""

    def load_archive(self, slot_name: str) -> ArchiveResult:
        """
        加载存档

        Args:
            slot_name: 存档槽名称

        Returns:
            ArchiveResult 加载结果
        """
        # 检查是否有操作正在进行
        with self._lock:
            if self._in_progress:
                raise ArchiveInProgressError(self._current_operation)
            self._in_progress = True
            self._current_operation = "load"

        # 备份信息 (用于回滚)
        backup_info = {
            "state": None,
            "chat_memory": None,
            "backup_dir": None,
        }

        try:
            self._publish_event("archive.restore.start", {"slot_name": slot_name})
            self._report_progress("开始加载存档", 0)

            # Step 1: 验证存档文件 (0-5%)
            archive_path = self.archive_dir / f"{slot_name}{self.ARCHIVE_EXTENSION}"
            if not archive_path.exists():
                raise ArchiveNotFoundError(slot_name)
            archive_size = get_archive_size(str(archive_path))
            self._report_progress(
                "验证存档文件", 5, {"size": f"{archive_size / 1024:.1f}KB"}
            )

            # Step 2: 解压存档 (5-15%)
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                self._report_progress("解压存档", 10)
                if not extract_archive(str(archive_path), temp_dir):
                    raise ArchiveCorruptedError(slot_name, "解压失败")
                self._report_progress("存档解压完成", 15)

                # Step 3: 验证存档完整性 (15-20%)
                self._report_progress("验证存档完整性", 18)
                is_valid, error_msg = validate_archive(temp_dir)
                if not is_valid:
                    raise ArchiveCorruptedError(slot_name, error_msg)
                self._report_progress("存档验证通过", 20)

                # Step 4: 读取并验证 manifest (20-25%)
                self._report_progress("读取存档信息", 22)
                manifest_path = temp_path / "manifest.json"
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = ArchiveManifest.from_json(f.read())
                self._report_progress(
                    "存档信息读取完成",
                    25,
                    {
                        "character": manifest.character_name,
                        "time": manifest.logical_time,
                    },
                )

                # Step 5: 检查版本兼容性 (25-30%)
                is_compatible, compat_msg = check_version_compatibility(
                    manifest.version
                )
                if not is_compatible:
                    raise ArchiveVersionError(manifest.version, get_current_version())
                logger.info(f"版本兼容性: {compat_msg}")
                self._report_progress("版本检查通过", 30)

                # Step 6: 暂停系统 (30-35%)
                self._pause_system()
                self._report_progress("暂停系统组件", 35)

                try:
                    # Step 7: 自动备份当前状态 (35-45%)
                    self._report_progress("自动备份当前状态", 38)
                    auto_backup_result = self._auto_backup_current_state()
                    if auto_backup_result["success"]:
                        self._report_progress(
                            "当前状态已备份",
                            45,
                            {"backup_slot": auto_backup_result.get("backup_slot", "")},
                        )
                    else:
                        self._report_progress(
                            "备份跳过",
                            45,
                            {"reason": auto_backup_result.get("reason", "")},
                        )

                    # Step 8: 备份当前状态 (用于回滚) (45-50%)
                    self._report_progress("创建回滚点", 48)
                    backup_info = self._backup_current_state()
                    self._report_progress("回滚点创建完成", 50)

                    # Step 9: 导入 JSON 配置 (50-55%)
                    self._report_progress("恢复配置文件", 52)
                    json_importer = JsonImporter(str(temp_path), str(self.config_dir))
                    json_result = json_importer.run()
                    if not json_result.success:
                        raise ArchiveError(f"导入配置文件失败: {json_result.error}")
                    self._report_progress("配置文件恢复完成", 55)

                    # Step 10: 导入 PostgreSQL 数据（包含实体关系图谱）
                    self._report_progress("恢复数据库和知识图谱", 60)
                    pg_importer = PostgresImporter(str(temp_path))
                    pg_result = pg_importer.run()

                    # 检查结果
                    if not pg_result or not pg_result.success:
                        self._rollback_state(backup_info)
                        raise ArchiveError(
                            f"导入数据库失败: {pg_result.error if pg_result else '未知错误'}"
                        )

                    self._report_progress(
                        "数据库和知识图谱恢复完成",
                        85,
                        {
                            "pg_tables": pg_result.stats.get("tables_count", 0),
                            "pg_rows": pg_result.stats.get("total_rows", 0),
                            "entities": pg_result.stats.get("table_rows", {}).get(
                                "knowledge_entities", 0
                            ),
                            "relations": pg_result.stats.get("table_rows", {}).get(
                                "knowledge_relations", 0
                            ),
                        },
                    )

                    # Step 12: 重建内存状态 (85-95%)
                    self._report_progress("重建内存状态", 88)
                    self._reload_memory_state()
                    self._report_progress("内存状态重建完成", 95)

                    # 清理备份
                    self._cleanup_backup(backup_info)

                    self._report_progress("存档加载完成", 100)

                    self._publish_event(
                        "archive.restore.complete",
                        {
                            "slot_name": slot_name,
                            "manifest": manifest.to_dict(),
                        },
                    )

                    return ArchiveResult(
                        success=True,
                        message=f"存档 '{slot_name}' 加载成功",
                        slot_name=slot_name,
                        manifest=manifest,
                    )

                except ArchiveError:
                    # 尝试回滚
                    self._rollback_state(backup_info)
                    raise
                except Exception as e:
                    # 尝试回滚
                    self._rollback_state(backup_info)
                    raise ArchiveError(f"加载存档时发生错误: {e}")

                finally:
                    # 恢复系统
                    self._resume_system()

        except ArchiveError:
            raise
        except Exception as e:
            logger.error(f"加载存档失败: {e}")
            self._publish_event("archive.error", {"error": str(e)})
            return ArchiveResult(
                success=False,
                message=f"加载存档失败: {e}",
                error=str(e),
            )
        finally:
            with self._lock:
                self._in_progress = False
                self._current_operation = ""

    def delete_archive(self, slot_name: str) -> ArchiveResult:
        """
        删除存档

        Args:
            slot_name: 存档槽名称

        Returns:
            ArchiveResult 删除结果
        """
        try:
            archive_path = self.archive_dir / f"{slot_name}{self.ARCHIVE_EXTENSION}"

            if not archive_path.exists():
                raise ArchiveNotFoundError(slot_name)

            # 删除文件
            archive_path.unlink()

            self._publish_event("archive.deleted", {"slot_name": slot_name})

            return ArchiveResult(
                success=True,
                message=f"存档 '{slot_name}' 已删除",
                slot_name=slot_name,
            )

        except ArchiveError:
            raise
        except Exception as e:
            logger.error(f"删除存档失败: {e}")
            return ArchiveResult(
                success=False,
                message=f"删除存档失败: {e}",
                error=str(e),
            )

    def list_archives(self) -> List[ArchiveSlot]:
        """
        列出所有存档

        Returns:
            存档槽列表
        """
        slots = []

        for archive_file in self.archive_dir.glob(f"*{self.ARCHIVE_EXTENSION}"):
            slot_name = archive_file.stem

            try:
                # 解压并读取 manifest
                with tempfile.TemporaryDirectory() as temp_dir:
                    extract_archive(str(archive_file), temp_dir)
                    manifest_path = Path(temp_dir) / "manifest.json"

                    if manifest_path.exists():
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            manifest = ArchiveManifest.from_json(f.read())

                        slot = ArchiveSlot(
                            slot_name=slot_name,
                            display_name=slot_name,
                            created_at=manifest.created_at,
                            logical_time=manifest.logical_time,
                            description=manifest.description,
                            file_path=str(archive_file),
                            file_size=get_archive_size(str(archive_file)),
                            is_valid=True,
                        )
                    else:
                        slot = ArchiveSlot(
                            slot_name=slot_name,
                            display_name=slot_name,
                            created_at="",
                            logical_time="",
                            description="",
                            file_path=str(archive_file),
                            file_size=get_archive_size(str(archive_file)),
                            is_valid=False,
                            error_message="缺少 manifest.json",
                        )

            except Exception as e:
                slot = ArchiveSlot(
                    slot_name=slot_name,
                    display_name=slot_name,
                    created_at="",
                    logical_time="",
                    description="",
                    file_path=str(archive_file),
                    file_size=get_archive_size(str(archive_file)),
                    is_valid=False,
                    error_message=str(e),
                )

            slots.append(slot)

        # 按创建时间排序 (最新的在前)
        slots.sort(key=lambda s: s.created_at, reverse=True)

        return slots

    def get_archive_preview(self, slot_name: str) -> Optional[ArchiveManifest]:
        """
        获取存档预览信息

        Args:
            slot_name: 存档槽名称

        Returns:
            存档元数据，如果不存在返回 None
        """
        archive_path = self.archive_dir / f"{slot_name}{self.ARCHIVE_EXTENSION}"

        if not archive_path.exists():
            return None

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                extract_archive(str(archive_path), temp_dir)
                manifest_path = Path(temp_dir) / "manifest.json"

                if manifest_path.exists():
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        return ArchiveManifest.from_json(f.read())

        except Exception as e:
            logger.error(f"读取存档预览失败: {e}")

        return None

    def quick_save(self) -> ArchiveResult:
        """快速存档"""
        return self.create_archive("quick_save", "快速存档")

    def quick_load(self) -> ArchiveResult:
        """快速读档"""
        return self.load_archive("quick_save")

    # ==================== 内部方法 ====================

    def _pause_system(self):
        """暂停系统组件"""
        if self.heartbeat:
            self.heartbeat.stop()
            logger.debug("心跳已暂停")

        if self.state_manager:
            self.state_manager.suspend_state_updates()

    def _resume_system(self):
        """恢复系统组件"""
        if self.heartbeat:
            self.heartbeat.start()
            logger.debug("心跳已恢复")

        if self.state_manager:
            self.state_manager.resume_state_updates()

    def _reload_memory_state(self):
        """重建内存状态"""
        # 1. 先重载短期记忆（从 chat_memory.json）
        if self.short_term_memory and hasattr(self.short_term_memory, "_load_memory"):
            self.short_term_memory._load_memory()
            logger.debug("短期记忆已重载")
        # state_manager.short_term_memory 是同一个对象引用，不需要单独更新

        # 2. 再重载状态（从 state.json）
        if self.state_manager:
            state_path = self.config_dir / "state.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    import json

                    self.state_manager.current_state = json.load(f)
                logger.debug("状态已重载")

                # 3. 同步 EventBus 逻辑时间
                if self.event_bus and hasattr(self.state_manager, "current_state"):
                    logical_time_str = self.state_manager.current_state.get(
                        "对应时间", ""
                    )
                    if logical_time_str:
                        self.event_bus.set_logical_time(logical_time_str)
                        logger.debug(f"逻辑时间已同步: {logical_time_str}")

        # 发布状态重载事件
        self._publish_event("state.reload")

    def _create_manifest(
        self,
        description: str = "",
        stats: ArchiveStats = None,
    ) -> ArchiveManifest:
        """创建存档元数据"""
        # 读取当前状态获取逻辑时间
        logical_time = ""
        character_name = "依鸣"

        state_path = self.config_dir / "state.json"
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    import json

                    state = json.load(f)
                    logical_time = state.get("对应时间", "")
            except Exception:
                pass

        return ArchiveManifest(
            version=get_current_version(),
            created_at=datetime.now().isoformat(),
            character_name=character_name,
            logical_time=logical_time,
            description=description,
            stats=stats or ArchiveStats(),
        )

    def _auto_backup_current_state(self) -> dict:
        """
        自动备份当前状态到存档文件

        在加载存档前自动创建一个备份存档，以便用户可以恢复。
        会自动清理旧的自动备份，只保留最近的 N 个。

        Returns:
            备份结果
        """
        result = {
            "success": False,
            "backup_slot": "",
            "reason": "",
        }

        try:
            # 检查是否有需要备份的数据
            state_path = self.config_dir / "state.json"
            if not state_path.exists():
                result["reason"] = "无状态数据"
                return result

            # 生成备份存档名称
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_slot = f"auto_backup_{timestamp}"

            # 使用内部方法创建备份（避免锁冲突）
            backup_result = self._create_archive_internal(
                backup_slot, description=f"自动备份 (加载前)"
            )

            if backup_result.success:
                result["success"] = True
                result["backup_slot"] = backup_slot
                logger.info(f"已自动备份当前状态到: {backup_slot}")

                # 创建成功后清理旧的自动备份（只保留最近的 3 个）
                self._cleanup_auto_backups(keep_count=3)
            else:
                result["reason"] = backup_result.error or "创建备份失败"

        except Exception as e:
            logger.error(f"自动备份失败: {e}")
            result["reason"] = str(e)

        return result

    def _cleanup_auto_backups(self, keep_count: int = 3):
        """
        清理旧的自动备份，只保留最近的 N 个

        Args:
            keep_count: 保留的备份数量
        """
        try:
            # 获取所有自动备份
            auto_backups = []
            for archive_file in self.archive_dir.glob("auto_backup_*.ember"):
                slot_name = archive_file.stem
                # 提取时间戳
                try:
                    timestamp_str = slot_name.replace("auto_backup_", "")
                    # 解析时间戳用于排序
                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    auto_backups.append(
                        {
                            "slot_name": slot_name,
                            "timestamp": timestamp,
                            "path": archive_file,
                        }
                    )
                except ValueError:
                    continue

            # 按时间排序（最新的在前）
            auto_backups.sort(key=lambda x: x["timestamp"], reverse=True)

            # 删除超出保留数量的备份
            if len(auto_backups) > keep_count:
                for backup in auto_backups[keep_count:]:
                    try:
                        backup["path"].unlink()
                        logger.info(f"已清理旧自动备份: {backup['slot_name']}")
                    except Exception as e:
                        logger.warning(f"清理备份失败 {backup['slot_name']}: {e}")

                deleted_count = len(auto_backups) - keep_count
                logger.info(
                    f"已清理 {deleted_count} 个旧自动备份，保留 {keep_count} 个"
                )

        except Exception as e:
            logger.error(f"清理自动备份失败: {e}")

    def _create_archive_internal(
        self,
        slot_name: str,
        description: str = "",
    ) -> ArchiveResult:
        """
        内部创建存档方法（不获取锁，供内部调用）

        Args:
            slot_name: 存档槽名称
            description: 存档描述

        Returns:
            ArchiveResult 创建结果
        """
        try:
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # 导出 JSON 配置
                json_exporter = JsonExporter(str(temp_path), str(self.config_dir))
                json_result = json_exporter.run()
                if not json_result.success:
                    return ArchiveResult(
                        success=False, error=f"导出配置文件失败: {json_result.error}"
                    )

                # 导出 PostgreSQL 数据
                pg_exporter = PostgresExporter(str(temp_path))
                pg_result = pg_exporter.run()

                # 生成元数据
                table_rows = pg_result.stats.get("table_rows", {})
                manifest = self._create_manifest(
                    description=description,
                    stats=ArchiveStats(
                        message_count=table_rows.get("message_list", 0),
                        memory_count=table_rows.get("episodic_memory", 0),
                        entity_count=table_rows.get("knowledge_entities", 0),
                        relation_count=table_rows.get("knowledge_relations", 0),
                    ),
                )

                # 写入 manifest
                manifest_path = temp_path / "manifest.json"
                with open(manifest_path, "w", encoding="utf-8") as f:
                    f.write(manifest.to_json())

                # 压缩打包
                archive_path = self.archive_dir / f"{slot_name}{self.ARCHIVE_EXTENSION}"
                if not compress_archive(temp_dir, str(archive_path)):
                    return ArchiveResult(success=False, error="压缩存档失败")

                return ArchiveResult(
                    success=True,
                    message=f"存档 '{slot_name}' 创建成功",
                    slot_name=slot_name,
                    manifest=manifest,
                )

        except Exception as e:
            logger.error(f"内部创建存档失败: {e}")
            return ArchiveResult(success=False, error=str(e))

    def _backup_current_state(self) -> dict:
        """
        备份当前状态 (用于回滚)

        Returns:
            备份信息字典
        """
        backup_info = {
            "state": None,
            "chat_memory": None,
            "backup_dir": None,
        }

        try:
            # 创建备份目录
            backup_dir = Path(self.archive_dir) / "_temp_backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_info["backup_dir"] = str(backup_dir)

            # 备份 state.json
            state_path = self.config_dir / "state.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    backup_info["state"] = f.read()
                backup_state_path = backup_dir / "state.json.bak"
                shutil.copy2(state_path, backup_state_path)
                logger.debug("已备份 state.json")

            # 备份 chat_memory.json
            memory_path = self.config_dir / "chat_memory.json"
            if memory_path.exists():
                with open(memory_path, "r", encoding="utf-8") as f:
                    backup_info["chat_memory"] = f.read()
                backup_memory_path = backup_dir / "chat_memory.json.bak"
                shutil.copy2(memory_path, backup_memory_path)
                logger.debug("已备份 chat_memory.json")

            logger.info("当前状态备份完成")

        except Exception as e:
            logger.error(f"备份当前状态失败: {e}")

        return backup_info

    def _rollback_state(self, backup_info: dict) -> bool:
        """
        从备份回滚状态

        Args:
            backup_info: 备份信息

        Returns:
            是否成功
        """
        if not backup_info:
            return False

        try:
            logger.warning("开始回滚状态...")

            # 回滚 state.json
            if backup_info.get("state"):
                state_path = self.config_dir / "state.json"
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write(backup_info["state"])
                logger.info("已回滚 state.json")

            # 回滚 chat_memory.json
            if backup_info.get("chat_memory"):
                memory_path = self.config_dir / "chat_memory.json"
                with open(memory_path, "w", encoding="utf-8") as f:
                    f.write(backup_info["chat_memory"])
                logger.info("已回滚 chat_memory.json")

            # 尝试从数据库备份恢复
            backup_dir = backup_info.get("backup_dir")
            if backup_dir:
                self._rollback_database(backup_dir)

            logger.info("状态回滚完成")
            return True

        except Exception as e:
            logger.error(f"回滚状态失败: {e}")
            return False

    def _rollback_database(self, backup_dir: str) -> bool:
        """
        尝试从数据库备份恢复

        Args:
            backup_dir: 备份目录

        Returns:
            是否成功
        """
        try:
            # 检查是否有数据库备份表
            import psycopg2
            from config.settings import settings

            conn = psycopg2.connect(
                host=settings.PG_HOST,
                port=settings.PG_PORT,
                database=settings.PG_DB,
                user=settings.PG_USER,
                password=settings.PG_PASSWORD,
            )

            cursor = conn.cursor()

            # 尝试从备份表恢复
            for table in PostgresImporter.TABLES:
                backup_table = f"{table}_backup"
                try:
                    # 检查备份表是否存在
                    cursor.execute(
                        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                        (backup_table,),
                    )
                    if cursor.fetchone()[0]:
                        cursor.execute(f"TRUNCATE TABLE {table};")
                        cursor.execute(
                            f"INSERT INTO {table} SELECT * FROM {backup_table};"
                        )
                        logger.info(f"已从备份恢复表: {table}")
                except Exception as e:
                    logger.warning(f"恢复表 {table} 失败: {e}")

            conn.commit()
            cursor.close()
            conn.close()

            return True

        except Exception as e:
            logger.error(f"数据库回滚失败: {e}")
            return False

    def _cleanup_backup(self, backup_info: dict):
        """
        清理备份文件

        Args:
            backup_info: 备份信息
        """
        try:
            backup_dir = backup_info.get("backup_dir")
            if backup_dir and Path(backup_dir).exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
                logger.debug("已清理备份目录")

            # 清理数据库备份表
            self._cleanup_database_backup()

        except Exception as e:
            logger.warning(f"清理备份失败: {e}")

    def _cleanup_database_backup(self):
        """清理数据库备份表"""
        try:
            import psycopg2
            from config.settings import settings

            conn = psycopg2.connect(
                host=settings.PG_HOST,
                port=settings.PG_PORT,
                database=settings.PG_DB,
                user=settings.PG_USER,
                password=settings.PG_PASSWORD,
            )

            cursor = conn.cursor()

            for table in PostgresImporter.TABLES:
                backup_table = f"{table}_backup"
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {backup_table};")
                except Exception:
                    pass

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.debug(f"清理数据库备份表失败: {e}")
