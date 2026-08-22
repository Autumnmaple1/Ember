"""
PostgreSQL 数据库导入器

负责导入 PostgreSQL 数据库中的表数据
"""

import logging
import time
from pathlib import Path
from typing import List, Optional

from archive.importers.base import BaseImporter, ImportResult
from config.settings import settings

logger = logging.getLogger(__name__)


class PostgresImporter(BaseImporter):
    """PostgreSQL 数据库导入器"""

    name = "postgres"
    description = "PostgreSQL 数据库导入器"

    # 需要导入的表
    TABLES = [
        "episodic_memory",
        "message_list",
        "state_list",
        "knowledge_entities",
        "knowledge_relations",
    ]

    # 需要重建向量索引的表
    VECTOR_TABLES = {
        "episodic_memory": [
            ("embedding", "ivfflat", "vector_cosine_ops"),
            ("insight_embedding", "ivfflat", "vector_cosine_ops"),
        ]
    }

    # 连接重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # 秒

    def __init__(
        self,
        source_dir: str,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None,
    ):
        """
        初始化 PostgreSQL 导入器

        Args:
            source_dir: 源目录 (解压后的存档目录)
            host: 数据库主机
            port: 数据库端口
            database: 数据库名称
            user: 用户名
            password: 密码
        """
        super().__init__(source_dir)
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

    def import_data(self) -> ImportResult:
        """导入 PostgreSQL 数据"""
        try:
            conn = self._get_connection()
            imported_tables = []
            failed_tables = []
            stats = {
                "tables_count": 0,
                "total_rows": 0,
                "indexes_rebuilt": 0,
                "table_rows": {},
            }

            try:
                # 先清空所有表（无论存档中是否有数据文件）
                self._clear_all_tables(conn)

                for table in self.TABLES:
                    sql_file = Path(self.source_dir) / f"{table}.sql"
                    if sql_file.exists():
                        result = self._import_table(conn, table, sql_file)
                        if result["success"]:
                            imported_tables.append(table)
                            stats["tables_count"] += 1
                            stats["total_rows"] += result.get("rows", 0)
                            stats["table_rows"][table] = result.get("rows", 0)
                        else:
                            failed_tables.append(
                                {"table": table, "error": result.get("error")}
                            )

                # 重建向量索引
                index_result = self._rebuild_vector_indexes(conn)
                stats["indexes_rebuilt"] = index_result.get("count", 0)

                # 同步所有表的序列
                self._sync_all_sequences(conn)

                conn.close()

                if failed_tables:
                    return ImportResult(
                        success=True,
                        message=f"已导入 {len(imported_tables)} 个表，{len(failed_tables)} 个失败",
                        data={"imported": imported_tables, "failed": failed_tables},
                        stats=stats,
                    )

                return ImportResult(
                    success=True,
                    message=f"已导入 {stats['tables_count']} 个表",
                    data=imported_tables,
                    stats=stats,
                )

            except Exception as e:
                conn.close()
                raise e

        except ImportError:
            self.logger.error("psycopg2 库未安装")
            return ImportResult(success=False, error="psycopg2 库未安装")
        except Exception as e:
            self.logger.error(f"导入 PostgreSQL 数据失败: {e}")
            return ImportResult(success=False, error=str(e))

    # 批量导入配置
    BATCH_INSERT_SIZE = 500  # 每批插入的行数

    def _clear_all_tables(self, conn):
        """
        清空所有表数据（在导入前调用）

        Args:
            conn: 数据库连接
        """
        try:
            cursor = conn.cursor()
            for table in self.TABLES:
                # 检查表是否存在
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                    (table,),
                )
                if cursor.fetchone()[0]:
                    self.logger.info(f"清空表: {table}")
                    cursor.execute(f"DELETE FROM {table};")
                    cursor.execute(
                        f"ALTER SEQUENCE IF EXISTS {table}_id_seq RESTART WITH 1;"
                    )
            conn.commit()
            cursor.close()
            self.logger.info("已清空所有表")
        except Exception as e:
            self.logger.error(f"清空表失败: {e}")
            conn.rollback()

    def _import_table(self, conn, table_name: str, sql_file: Path) -> dict:
        """
        导入单个表 (使用批量插入优化)

        Args:
            conn: 数据库连接
            table_name: 表名
            sql_file: SQL 文件路径

        Returns:
            导入结果
        """
        try:
            cursor = conn.cursor()

            # 检查表是否存在
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table_name,),
            )
            if not cursor.fetchone()[0]:
                self.logger.warning(f"表 {table_name} 不存在，跳过导入")
                return {"success": False, "error": f"表 {table_name} 不存在"}

            # 读取 SQL 文件
            with open(sql_file, "r", encoding="utf-8") as f:
                sql_content = f.read()

            # 存档内只允许执行当前白名单表的 INSERT。直接交给 PostgreSQL
            # 解析，可以正确恢复 JSONB、TEXT[]、时间戳和 vector 等类型。
            import re

            row_count = 0
            insert_pattern = re.compile(
                r'^INSERT\s+INTO\s+'
                r'(?:(?:"?public"?)\.)?'
                r'"?([A-Za-z_][A-Za-z0-9_]*)"?\s',
                re.IGNORECASE,
            )
            for statement in self._parse_sql_statements(sql_content):
                match = insert_pattern.match(statement)
                if not match:
                    continue
                if match.group(1) != table_name:
                    raise ValueError(
                        f"存档表名不匹配: 期望 {table_name}, 实际 {match.group(1)}"
                    )
                cursor.execute(statement)
                row_count += 1

            # 即使没有数据也要提交事务
            conn.commit()

            cursor.close()
            self.logger.info(f"已导入表: {table_name}, 共 {row_count} 行")
            return {"success": True, "rows": row_count}

        except Exception as e:
            self.logger.error(f"导入表 {table_name} 失败: {e}")
            conn.rollback()
            return {"success": False, "error": str(e)}

    def _try_copy_import(self, conn, cursor, table_name: str, sql_content: str) -> bool:
        """
        尝试使用 COPY 命令导入 (更快)

        Returns:
            是否成功使用 COPY
        """
        try:
            import io

            # 解析 INSERT 语句提取数据
            values_list = self._extract_values_from_sql(sql_content)
            if not values_list:
                return False

            # 获取列名 (从第一条 INSERT 语句)
            columns = self._extract_columns_from_sql(sql_content)
            if not columns:
                return False

            # 构建 CSV 数据
            csv_buffer = io.StringIO()
            for values in values_list:
                # 将 SQL 值转换为 CSV 格式
                csv_row = self._sql_values_to_csv_row(values, columns)
                csv_buffer.write(csv_row + "\n")

            csv_buffer.seek(0)

            # 使用 COPY 命令
            columns_str = ", ".join(columns)
            cursor.copy_expert(
                f"COPY {table_name} ({columns_str}) FROM STDIN WITH (FORMAT CSV, NULL 'NULL')",
                csv_buffer,
            )

            conn.commit()
            return True

        except Exception as e:
            self.logger.debug(f"COPY 导入失败，回退到批量INSERT: {e}")
            conn.rollback()
            return False

    def _extract_columns_from_sql(self, sql_content: str) -> list:
        """从 INSERT 语句中提取列名"""
        import re

        match = re.search(r"INSERT INTO \w+ \(([^)]+)\)", sql_content, re.IGNORECASE)
        if match:
            return [col.strip() for col in match.group(1).split(",")]
        return []

    def _extract_values_from_sql(self, sql_content: str) -> list:
        """从 INSERT 语句中提取值列表"""
        import re

        pattern = r"INSERT INTO \w+ \([^)]+\) VALUES \(([^)]+)\);"
        matches = re.findall(pattern, sql_content, re.IGNORECASE)
        return matches

    def _sql_values_to_csv_row(self, values_str: str, columns: list) -> str:
        """将 SQL VALUES 转换为 CSV 行"""
        # 简单处理：移除引号内的逗号问题
        parts = []
        in_quote = False
        current = []
        quote_char = None

        for char in values_str:
            if char in ("'", '"') and not in_quote:
                in_quote = True
                quote_char = char
            elif char == quote_char and in_quote:
                in_quote = False
                quote_char = None
            elif char == "," and not in_quote:
                parts.append("".join(current).strip())
                current = []
                continue
            current.append(char)

        if current:
            parts.append("".join(current).strip())

        # 转换为 CSV 格式
        csv_parts = []
        for part in parts:
            part = part.strip()
            if part.upper() == "NULL":
                csv_parts.append("NULL")
            elif part.startswith("'") and part.endswith("'"):
                # 移除 SQL 引号，CSV 会自动处理
                csv_parts.append(part[1:-1].replace("''", "'"))
            else:
                csv_parts.append(part)

        return ",".join(csv_parts)

    def _batch_insert(self, cursor, table_name: str, sql_content: str) -> int:
        """
        批量 INSERT 导入 (比逐条快很多)

        Returns:
            导入的行数
        """
        import re

        # 提取所有 INSERT 语句
        pattern = r"INSERT INTO (\w+) \(([^)]+)\) VALUES \(([^)]+)\);"
        matches = re.findall(pattern, sql_content, re.IGNORECASE)

        if not matches:
            # 没有数据需要导入，返回 0
            self.logger.debug(f"表 {table_name} 没有 INSERT 语句，跳过导入")
            return 0

        columns = [col.strip() for col in matches[0][1].split(",")]
        columns_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))

        # 批量收集值
        all_values = []
        for match in matches:
            values_str = match[2]
            values = self._parse_values(values_str)
            all_values.append(values)

        # 批量插入
        inserted = 0
        for i in range(0, len(all_values), self.BATCH_INSERT_SIZE):
            batch = all_values[i : i + self.BATCH_INSERT_SIZE]
            for values in batch:
                cursor.execute(
                    f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1

        return inserted

    def _parse_values(self, values_str: str) -> list:
        """解析 SQL VALUES 字符串为 Python 值列表"""
        values = []
        current = []
        in_quote = False
        quote_char = None

        for char in values_str:
            if char in ("'", '"') and not in_quote:
                in_quote = True
                quote_char = char
            elif char == quote_char and in_quote:
                in_quote = False
                quote_char = None
            elif char == "," and not in_quote:
                val = "".join(current).strip()
                values.append(self._convert_value(val))
                current = []
                continue
            current.append(char)

        if current:
            val = "".join(current).strip()
            values.append(self._convert_value(val))

        return values

    def _convert_value(self, val: str):
        """将 SQL 值字符串转换为 Python 值"""
        if val.upper() == "NULL":
            return None
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1].replace("''", "'")
        try:
            if "." in val:
                return float(val)
            return int(val)
        except ValueError:
            return val

    def _parse_sql_statements(self, sql_content: str) -> List[str]:
        """
        解析 SQL 文件为独立语句

        Args:
            sql_content: SQL 文件内容

        Returns:
            SQL 语句列表
        """
        statements = []
        current_stmt = []

        for line in sql_content.split("\n"):
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith("--"):
                continue

            current_stmt.append(line)

            # 检查语句是否结束
            if line.endswith(";"):
                stmt = " ".join(current_stmt)
                statements.append(stmt)
                current_stmt = []

        # 处理最后一个未结束的语句
        if current_stmt:
            statements.append(" ".join(current_stmt))

        return statements

    def _rebuild_vector_indexes(self, conn) -> dict:
        """
        重建向量索引

        Args:
            conn: 数据库连接

        Returns:
            重建结果
        """
        rebuilt_count = 0

        try:
            cursor = conn.cursor()

            for table_name, indexes in self.VECTOR_TABLES.items():
                for column, index_type, ops in indexes:
                    index_name = f"idx_{table_name}_{column}"

                    try:
                        # 删除旧索引
                        cursor.execute(f"DROP INDEX IF EXISTS {index_name};")

                        # 创建新索引
                        # 使用 ivfflat 索引，lists 参数根据数据量调整
                        cursor.execute(
                            f"CREATE INDEX {index_name} ON {table_name} "
                            f"USING {index_type} ({column} {ops}) WITH (lists = 100);"
                        )

                        rebuilt_count += 1
                        self.logger.info(f"已重建索引: {index_name}")

                    except Exception as e:
                        self.logger.warning(f"重建索引 {index_name} 失败: {e}")

            conn.commit()
            cursor.close()

        except Exception as e:
            self.logger.error(f"重建向量索引失败: {e}")
            conn.rollback()

        return {"count": rebuilt_count}

    def _sync_all_sequences(self, conn):
        """
        同步所有表的序列到 max_id + 1

        Args:
            conn: 数据库连接
        """
        try:
            cursor = conn.cursor()

            for table in self.TABLES:
                # 获取表中最大的 id
                cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                max_id = cursor.fetchone()[0]

                # 设置序列的下一个值为 max_id + 1
                next_val = max_id + 1
                cursor.execute(
                    f"ALTER SEQUENCE IF EXISTS {table}_id_seq RESTART WITH {next_val};"
                )
                self.logger.debug(f"已同步序列 {table}_id_seq 到 {next_val}")

            conn.commit()
            cursor.close()
            self.logger.info("已同步所有表的序列")

        except Exception as e:
            self.logger.error(f"同步序列失败: {e}")
            conn.rollback()

    def backup_table(self, conn, table_name: str) -> bool:
        """
        备份单个表数据

        Args:
            conn: 数据库连接
            table_name: 表名

        Returns:
            是否成功
        """
        try:
            cursor = conn.cursor()
            backup_table = f"{table_name}_backup"

            # 删除旧备份
            cursor.execute(f"DROP TABLE IF EXISTS {backup_table};")

            # 创建备份表
            cursor.execute(
                f"CREATE TABLE {backup_table} AS SELECT * FROM {table_name};"
            )

            conn.commit()
            cursor.close()

            self.logger.info(f"已备份表: {table_name} -> {backup_table}")
            return True

        except Exception as e:
            self.logger.error(f"备份表 {table_name} 失败: {e}")
            conn.rollback()
            return False

    def restore_from_backup(self, conn, table_name: str) -> bool:
        """
        从备份恢复表数据

        Args:
            conn: 数据库连接
            table_name: 表名

        Returns:
            是否成功
        """
        try:
            cursor = conn.cursor()
            backup_table = f"{table_name}_backup"

            # 检查备份表是否存在
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (backup_table,),
            )
            if not cursor.fetchone()[0]:
                self.logger.warning(f"备份表 {backup_table} 不存在")
                return False

            # 清空原表并恢复数据
            cursor.execute(f"TRUNCATE TABLE {table_name};")
            cursor.execute(f"INSERT INTO {table_name} SELECT * FROM {backup_table};")

            conn.commit()
            cursor.close()

            self.logger.info(f"已从备份恢复表: {table_name}")
            return True

        except Exception as e:
            self.logger.error(f"恢复表 {table_name} 失败: {e}")
            conn.rollback()
            return False

    def clear_all_tables(self) -> bool:
        """清空所有表"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            for table in self.TABLES:
                cursor.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY;")

            conn.commit()
            cursor.close()
            conn.close()

            self.logger.info("已清空所有表")
            return True

        except Exception as e:
            self.logger.error(f"清空表失败: {e}")
            return False
