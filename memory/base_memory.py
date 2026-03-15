"""
Memory 基础类 - 提供公共的数据库连接和工具方法
"""
import logging
import threading
from abc import ABC
from typing import Optional

import psycopg2
from config.settings import settings

logger = logging.getLogger(__name__)


class BasePostgresMemory(ABC):
    """PostgreSQL 内存基础类

    提供统一的数据库连接管理和基础工具方法
    """

    _executor = None  # 类级别线程池，子类可以覆盖

    def __init__(self):
        self.conn: Optional[psycopg2.extensions.connection] = None
        self._connection_lock = threading.Lock()
        self._ensure_connection()

    def _ensure_connection(self) -> bool:
        """确保数据库连接可用

        Returns:
            连接是否成功
        """
        try:
            with self._connection_lock:
                if self.conn is None or self.conn.closed:
                    self.conn = psycopg2.connect(
                        dbname=settings.PG_DB,
                        user=settings.PG_USER,
                        password=settings.PG_PASSWORD,
                        host=settings.PG_HOST,
                        port=settings.PG_PORT,
                        connect_timeout=5,
                    )
                    logger.debug(f"{self.__class__.__name__} connected to PostgreSQL.")
            return True
        except psycopg2.Error as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.conn = None
            return False

    def close(self) -> None:
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info(f"{self.__class__.__name__} connection closed")

    def _execute_query(self, query: str, params: tuple = ()) -> Optional[list]:
        """执行查询并返回结果

        Args:
            query: SQL 查询语句
            params: 查询参数

        Returns:
            查询结果列表，失败返回 None
        """
        if not self._ensure_connection():
            return None

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
        except psycopg2.Error as e:
            logger.error(f"Query execution failed: {e}")
            if self.conn:
                self.conn.rollback()
            return None

    def _execute_write(self, query: str, params: tuple = ()) -> bool:
        """执行写入操作

        Args:
            query: SQL 语句
            params: 参数

        Returns:
            是否执行成功
        """
        if not self._ensure_connection():
            return False

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                self.conn.commit()
                return True
        except psycopg2.Error as e:
            logger.error(f"Write operation failed: {e}")
            if self.conn:
                self.conn.rollback()
            return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
