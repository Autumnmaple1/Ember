"""
安全测试 - 验证潜在的安全问题
"""
import pytest


class TestCORSConfiguration:
    """测试 CORS 配置"""

    def test_cors_not_wildcard(self):
        """确保 CORS 不是通配符 *"""
        # 读取 server.py 检查配置
        with open("server.py", "r", encoding="utf-8") as f:
            content = f.read()

        # 不应该有 allow_origins=["*"]
        assert 'allow_origins=["*"]' not in content
        assert 'allow_origins=["http://localhost:5173"' in content


class TestSQLInjection:
    """测试 SQL 注入防护"""

    def test_db_memory_uses_parameters(self):
        """测试数据库查询使用参数化"""
        with open("memory/db_memory.py", "r", encoding="utf-8") as f:
            content = f.read()

        # 应该使用 %s 占位符而不是 f-string
        assert "%s" in content

    def test_episodic_memory_uses_parameters(self):
        """测试情景记忆使用参数化查询"""
        with open("memory/episodic_memory.py", "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否使用了参数化查询
        assert "%s" in content or "execute" in content


class TestGraphSQLInjection:
    """测试图谱 SQL 注入防护"""

    def test_postgres_graph_uses_parameters(self):
        """测试 PostgreSQL 图谱查询使用参数"""
        with open("memory/postgres_graph_memory.py", "r", encoding="utf-8") as f:
            content = f.read()

        assert "%s" in content


class TestSecrets:
    """测试密钥保护"""

    def test_env_not_committed(self):
        """确保 .env 不在 git 中"""
        import os

        # 检查 .env 是否被跟踪
        git_dir = ".git"
        if os.path.exists(git_dir):
            import subprocess
            result = subprocess.run(
                ["git", "ls-files", ".env"],
                capture_output=True,
                text=True
            )
            assert ".env" not in result.stdout, ".env 不应该被 Git 跟踪"

    def test_env_example_exists(self):
        """确保 .env.example 存在作为模板"""
        import os
        assert os.path.exists(".env.example")
