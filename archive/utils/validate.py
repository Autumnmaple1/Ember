"""
存档验证工具
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

# 存档必须包含的文件
REQUIRED_FILES = ["manifest.json", "state.json"]


def calculate_checksum(file_path: str) -> str:
    """
    计算文件的 MD5 校验和

    Args:
        file_path: 文件路径

    Returns:
        MD5 哈希值
    """
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"计算校验和失败: {e}")
        return ""


def calculate_dir_checksum(dir_path: str, exclude_files: List[str] = None) -> str:
    """
    计算目录的校验和 (基于所有文件的合并哈希)

    Args:
        dir_path: 目录路径
        exclude_files: 排除的文件列表

    Returns:
        合并的 MD5 哈希值
    """
    exclude_files = exclude_files or []
    try:
        dir_path = Path(dir_path)
        hash_md5 = hashlib.md5()

        # 按文件名排序以确保一致性
        files = sorted([f for f in dir_path.rglob("*") if f.is_file()])

        for file_path in files:
            if file_path.name in exclude_files:
                continue

            # 将文件名加入哈希
            hash_md5.update(file_path.name.encode())

            # 将文件内容加入哈希
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)

        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"计算目录校验和失败: {e}")
        return ""


def validate_archive(archive_dir: str) -> tuple[bool, str]:
    """
    验证存档目录的完整性

    Args:
        archive_dir: 解压后的存档目录

    Returns:
        (是否有效, 错误信息)
    """
    try:
        archive_path = Path(archive_dir)

        if not archive_path.exists():
            return False, "存档目录不存在"

        if not archive_path.is_dir():
            return False, "存档路径不是目录"

        # 检查必需文件
        missing_files = []
        for required_file in REQUIRED_FILES:
            if not (archive_path / required_file).exists():
                missing_files.append(required_file)

        if missing_files:
            return False, f"缺少必需文件: {', '.join(missing_files)}"

        # 验证 manifest.json 格式
        manifest_path = archive_path / "manifest.json"
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 检查必需字段
            if "version" not in manifest:
                return False, "manifest.json 缺少 version 字段"

            if "created_at" not in manifest:
                return False, "manifest.json 缺少 created_at 字段"

        except json.JSONDecodeError as e:
            return False, f"manifest.json 格式错误: {e}"

        # 验证 state.json 格式
        state_path = archive_path / "state.json"
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict) and 'role_continuity' in state:
                from persona.continuity import load_continuity
                load_continuity(state)
        except json.JSONDecodeError as e:
            return False, f"state.json 格式错误: {e}"
        except ValueError as e:
            return False, f"角色连续性记录错误: {e}"

        return True, ""

    except Exception as e:
        logger.error(f"验证存档失败: {e}")
        return False, f"验证异常: {e}"


def validate_checksum(archive_dir: str, expected_checksum: str) -> bool:
    """
    验证存档校验和

    Args:
        archive_dir: 解压后的存档目录
        expected_checksum: 预期的校验和

    Returns:
        是否匹配
    """
    actual_checksum = calculate_dir_checksum(
        archive_dir, exclude_files=["manifest.json"]
    )
    return actual_checksum == expected_checksum
