"""
压缩和解压工具
"""

import zipfile
import os
import logging
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def compress_archive(
    source_dir: str,
    output_path: str,
    files: Optional[List[str]] = None,
) -> bool:
    """
    将目录或指定文件压缩为 .ember 存档文件

    Args:
        source_dir: 源目录路径
        output_path: 输出文件路径 (.ember)
        files: 要压缩的文件列表，如果为 None 则压缩整个目录

    Returns:
        是否成功
    """
    temporary = None
    try:
        source_path = Path(source_dir)
        output_path = Path(output_path)

        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix='.ember.tmp', delete=False) as pending:
            temporary = Path(pending.name)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
            if files:
                # 压缩指定文件
                for file_name in files:
                    file_path = source_path / file_name
                    if file_path.exists():
                        zf.write(file_path, file_name)
                        logger.debug(f"已添加文件: {file_name}")
                    else:
                        logger.warning(f"文件不存在，跳过: {file_name}")
            else:
                # 压缩整个目录
                for file_path in source_path.rglob("*"):
                    if file_path.is_file():
                        arc_name = file_path.relative_to(source_path)
                        zf.write(file_path, arc_name)
                        logger.debug(f"已添加文件: {arc_name}")

        temporary.replace(output_path)
        logger.info(f"存档压缩完成: {output_path}")
        return True

    except Exception as e:
        logger.error(f"压缩存档失败: {e}")
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_archive(
    archive_path: str,
    output_dir: str,
) -> bool:
    """
    解压 .ember 存档文件

    Args:
        archive_path: 存档文件路径
        output_dir: 输出目录路径

    Returns:
        是否成功
    """
    try:
        archive_path = Path(archive_path)
        output_path = Path(output_dir)

        if not archive_path.exists():
            logger.error(f"存档文件不存在: {archive_path}")
            return False

        # 清空并创建输出目录
        if output_path.exists():
            for item in output_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    import shutil

                    shutil.rmtree(item)
        else:
            output_path.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(output_path)

        logger.info(f"存档解压完成: {output_path}")
        return True

    except zipfile.BadZipFile as e:
        logger.error(f"存档文件损坏: {e}")
        return False
    except Exception as e:
        logger.error(f"解压存档失败: {e}")
        return False


def get_archive_size(archive_path: str) -> int:
    """获取存档文件大小"""
    try:
        return os.path.getsize(archive_path)
    except Exception:
        return 0


def list_archive_contents(archive_path: str) -> List[str]:
    """列出存档文件内容"""
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return zf.namelist()
    except Exception:
        return []
