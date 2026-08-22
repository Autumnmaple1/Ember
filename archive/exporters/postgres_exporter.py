"""
PostgreSQL 数据库导出器

负责导出 PostgreSQL 数据库中的表数据
"""

import subprocess
import os
import logging
import time
import json
from typing import List, Optional
from pathlib import Path

from archive.exporters.base import BaseExporter, ExportResult
from config.settings import settings

logger = logging.getLogger(__name__)


class PostgresExporter(BaseExporter):
    """PostgreSQL 数据库导出器"""

    name = "postgres"
    description = "PostgreSQL 数据库导出器"

    # 需要导出的表
    TABLES = [
        "episodic_memory",
        "message_list",
        "state_list",
        "knowledge_entities",
        "knowledge_relations",
    ]

    # 分批导出配置
    BATCH_SIZE = 1000  # 每批处理的行数
    MAX_TIMEOUT = 300  # 最大超时时间 (秒)

    # 连接重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0

    def __init__(
        self,
        output_dir: str,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None,
    ):
        """
        初始化 PostgreSQL 导出器

        Args:
            output_dir: 输出目录
            host: 数据库主机
            port: 数据库端口
            database: 数据库名称
            user: 用户名
            password: 密码
        """
        super().__init__(output_dir)
        self.host = host or settings.PG_HOST
        self.port = port or settings.PG_PORT
        self.database = database or settings.PG_DB
        self.user = user or settings.PG_USER
        self.password = password or settings.PG_PASSWORD

    def _get_connection(self, retries: int = None):
        """
        获取数据库连接，支持重试

        Args:
            retries: 重试次数

        Returns:
            数据库连接
        """
        import psycopg2

        retries = retries or self.MAX_RETRIES
        last_error = None

        for attempt in range(retries):
            try:
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    connect_timeout=10,
                )
                self.logger.debug(f"数据库连接成功 (尝试 {attempt + 1}/{retries})")
                return conn
            except Exception as e:
                last_error = e
                self.logger.warning(
                    f"数据库连接失败 (尝试 {attempt + 1}/{retries}): {e}"
                )
                if attempt < retries - 1:
                    time.sleep(self.RETRY_DELAY)

        raise last_error

    def export(self) -> ExportResult:
        """导出 PostgreSQL 数据"""
        try:
            exported_tables = []
            failed_tables = []
            stats = {"tables_count": 0, "total_rows": 0, "table_rows": {}}

            for table in self.TABLES:
                result = self._export_table(table)
                if result["success"]:
                    exported_tables.append(table)
                    stats["tables_count"] += 1
                    stats["total_rows"] += result.get("rows", 0)
                    stats["table_rows"][table] = result.get("rows", 0)
                else:
                    failed_tables.append({"table": table, "error": result.get("error")})

            if failed_tables:
                self.logger.warning(f"部分表导出失败: {failed_tables}")

            return ExportResult(
                success=len(exported_tables) > 0,
                data={"exported": exported_tables, "failed": failed_tables},
                stats=stats,
            )

        except Exception as e:
            self.logger.error(f"导出 PostgreSQL 数据失败: {e}")
            return ExportResult(success=False, error=str(e))

    def _export_table(self, table_name: str) -> dict:
        """
        导出单个表

        Args:
            table_name: 表名

        Returns:
            导出结果
        """
        try:
            output_file = Path(self.output_dir) / f"{table_name}.sql"

            # 首先尝试使用 pg_dump
            result = self._export_table_pg_dump(table_name, output_file)
            if result["success"]:
                return result

            # pg_dump 失败，使用 Python 方式
            self.logger.info(f"pg_dump 不可用，使用 Python 方式导出 {table_name}")
            return self._export_table_python(table_name, output_file)

        except Exception as e:
            self.logger.error(f"导出表 {table_name} 失败: {e}")
            return {"success": False, "error": str(e)}

    def _export_table_pg_dump(self, table_name: str, output_file: Path) -> dict:
        """
        使用 pg_dump 导出表

        Args:
            table_name: 表名
            output_file: 输出文件路径

        Returns:
            导出结果
        """
        try:
            # 设置环境变量
            env = os.environ.copy()
            env["PGPASSWORD"] = self.password

            # 使用 pg_dump 导出表数据
            cmd = [
                "pg_dump",
                "-h",
                self.host,
                "-p",
                str(self.port),
                "-U",
                self.user,
                "-d",
                self.database,
                "-t",
                table_name,
                "--data-only",
                "--column-inserts",
                "-f",
                str(output_file),
            ]

            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.MAX_TIMEOUT,
            )

            if result.returncode != 0:
                self.logger.debug(f"pg_dump 失败: {result.stderr[:200]}")
                return {"success": False, "error": result.stderr[:200]}

            # 统计行数
            row_count = self._count_file_rows(output_file)

            self.logger.info(f"已导出表 (pg_dump): {table_name}, 共 {row_count} 行")
            return {"success": True, "rows": row_count}

        except subprocess.TimeoutExpired:
            self.logger.warning(f"pg_dump 导出 {table_name} 超时")
            return {"success": False, "error": "导出超时"}
        except FileNotFoundError:
            return {"success": False, "error": "pg_dump 不可用"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _export_table_python(self, table_name: str, output_file: Path) -> dict:
        """
        使用 Python 方式导出表数据 (支持分批处理)

        Args:
            table_name: 表名
            output_file: 输出文件路径

        Returns:
            导出结果
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查表是否存在
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table_name,),
            )
            if not cursor.fetchone()[0]:
                self.logger.warning(f"表 {table_name} 不存在，跳过导出")
                cursor.close()
                conn.close()
                return {"success": False, "error": f"表 {table_name} 不存在"}

            # 获取总行数
            cursor.execute(f"SELECT count(*) FROM {table_name}")
            total_rows = cursor.fetchone()[0]

            if total_rows == 0:
                # 空表，创建空文件
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(f"-- Table: {table_name}\n")
                    f.write("-- Rows: 0\n")
                cursor.close()
                conn.close()
                return {"success": True, "rows": 0}

            # 获取列信息
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 0")
            columns = [desc[0] for desc in cursor.description]

            # 分批导出。由 psycopg2 负责生成 SQL 字面量，确保 JSONB、数组、
            # 时间戳和字符串都能被 PostgreSQL 无损读回。
            from psycopg2.extras import Json

            placeholders = ", ".join(["%s"] * len(columns))
            columns_str = ", ".join(columns)
            insert_template = (
                f"INSERT INTO {table_name} ({columns_str}) "
                f"VALUES ({placeholders});"
            )

            def adapt_value(value):
                if isinstance(value, dict):
                    return Json(
                        value,
                        dumps=lambda obj: json.dumps(obj, ensure_ascii=False),
                    )
                return value

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"-- Table: {table_name}\n")
                f.write(f"-- Rows: {total_rows}\n")
                f.write(f"-- Columns: {', '.join(columns)}\n\n")

                offset = 0
                exported_rows = 0

                while offset < total_rows:
                    # 分批查询
                    cursor.execute(
                        f"SELECT * FROM {table_name} ORDER BY id OFFSET %s LIMIT %s",
                        (offset, self.BATCH_SIZE),
                    )
                    rows = cursor.fetchall()

                    for row in rows:
                        adapted_row = tuple(adapt_value(value) for value in row)
                        statement = cursor.mogrify(
                            insert_template, adapted_row
                        ).decode(conn.encoding or "utf-8")
                        f.write(statement + "\n")
                        exported_rows += 1

                    offset += self.BATCH_SIZE
                    self.logger.debug(
                        f"导出 {table_name}: {exported_rows}/{total_rows} 行"
                    )

            cursor.close()
            conn.close()

            self.logger.info(f"已导出表 (Python): {table_name}, 共 {exported_rows} 行")
            return {"success": True, "rows": exported_rows}

        except Exception as e:
            self.logger.error(f"Python 方式导出表 {table_name} 失败: {e}")
            return {"success": False, "error": str(e)}

    def _format_row_values(self, row: tuple) -> List[str]:
        """
        格式化行数据为 SQL 值

        Args:
            row: 数据行

        Returns:
            格式化后的值列表
        """
        import json

        values = []
        for val in row:
            if val is None:
                values.append("NULL")
            elif isinstance(val, bool):
                values.append("TRUE" if val else "FALSE")
            elif isinstance(val, (int, float)):
                # 处理特殊浮点数
                if isinstance(val, float):
                    if val != val:  # NaN
                        values.append("'NaN'")
                    elif val == float("inf"):
                        values.append("'Infinity'")
                    elif val == float("-inf"):
                        values.append("'-Infinity'")
                    else:
                        values.append(str(val))
                else:
                    values.append(str(val))
            elif isinstance(val, (dict, list)):
                # JSON 类型使用 json.dumps 序列化（确保双引号格式）
                json_str = json.dumps(val, ensure_ascii=False)
                escaped = self._escape_string(json_str)
                values.append(f"'{escaped}'")
            elif isinstance(val, str):
                # 完善的字符串转义
                escaped = self._escape_string(val)
                values.append(f"'{escaped}'")
            elif isinstance(val, bytes):
                # 二进制数据使用 hex 格式
                values.append(f"'\\x{val.hex()}'")
            elif hasattr(val, "__str__"):
                # 其他类型转为字符串
                escaped = self._escape_string(str(val))
                values.append(f"'{escaped}'")
            else:
                values.append("NULL")

        return values

    def _escape_string(self, s: str) -> str:
        """
        转义 SQL 字符串中的特殊字符

        Args:
            s: 原始字符串

        Returns:
            转义后的字符串
        """
        if not s:
            return ""

        # 替换特殊字符
        result = s
        result = result.replace("\\", "\\\\")  # 反斜杠
        result = result.replace("'", "''")  # 单引号 (SQL标准)
        result = result.replace("\0", "\\0")  # NULL字符
        result = result.replace("\n", "\\n")  # 换行符
        result = result.replace("\r", "\\r")  # 回车符
        result = result.replace("\t", "\\t")  # 制表符
        result = result.replace("\x1a", "\\Z")  # Ctrl+Z

        return result

    def _count_file_rows(self, file_path: Path) -> int:
        """
        统计 SQL 文件中的 INSERT 语句数量

        Args:
            file_path: 文件路径

        Returns:
            行数
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                count = 0
                for line in f:
                    if line.strip().upper().startswith("INSERT"):
                        count += 1
                return count
        except Exception:
            return 0
