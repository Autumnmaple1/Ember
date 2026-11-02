"""
Pytest 配置文件和共享 fixtures
"""
import pytest
import sys
import os
import tempfile
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_test_db = None
_admin_args = None
_suite_dir = None


def pytest_configure(config):
    """Never allow the legacy archive tests to connect to business databases."""
    global _test_db, _admin_args, _suite_dir
    from dotenv import dotenv_values
    import psycopg2
    from psycopg2 import sql
    values = dotenv_values(ROOT / '.env')
    _admin_args = dict(host=os.getenv('PG_HOST') or values.get('PG_HOST', '127.0.0.1'),
        port=os.getenv('PG_PORT') or values.get('PG_PORT', '5433'),
        user=os.getenv('PG_USER') or values.get('PG_USER', 'postgres'),
        password=os.getenv('PG_PASSWORD') or values.get('PG_PASSWORD', ''),
        dbname='postgres', connect_timeout=5)
    candidate_db = 'ember_test_' + uuid.uuid4().hex
    conn = psycopg2.connect(**_admin_args)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(candidate_db)))
            _test_db = candidate_db
    finally:
        conn.close()
    for key, arg in (('PG_HOST','host'),('PG_PORT','port'),('PG_USER','user'),('PG_PASSWORD','password')):
        os.environ[key] = str(_admin_args[arg])
    os.environ.update(PG_DB=_test_db, ENABLE_NEO4J='False', START_TIME='', TIME_ACCEL_FACTOR='1.0',
                      TIMEZONE='Asia/Shanghai', STATE_UPDATE_INTERVAL='1')
    for prefix in ('LARGE_LLM', 'SMALL_LLM', 'EMBEDDING'):
        os.environ[prefix + '_API_KEY'] = 'test-key'
        os.environ[prefix + '_BASE_URL'] = 'http://127.0.0.1:9/v1'
        os.environ[prefix + '_MODEL'] = 'test-model'
    _suite_dir = tempfile.TemporaryDirectory(prefix='ember-tests-')
    _copy_test_workspace(Path(_suite_dir.name))
    os.chdir(_suite_dir.name)
    from memory.postgres import connection, initialize_history, initialize_episodic
    with connection() as db:
        initialize_history(db)
        initialize_episodic(db)


def _copy_test_workspace(target):
    for relative in ('server.py', '.env.example', 'config/prompts.yaml', 'config/state_default.json',
                     'memory/db_memory.py', 'memory/episodic_memory.py', 'memory/neo4j_memory.py'):
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, dest)


def pytest_unconfigure(config):
    if _test_db and _test_db.startswith('ember_test_'):
        import psycopg2
        from psycopg2 import sql
        conn = psycopg2.connect(**_admin_args)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(_test_db)))
        finally:
            conn.close()
    os.chdir(ROOT)
    if _suite_dir:
        _suite_dir.cleanup()


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    _copy_test_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环用于异步测试"""
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_state_file(tmp_path):
    """创建临时状态文件"""
    state_file = tmp_path / "test_state.json"
    default_state = {
        "P": 5,
        "A": 5,
        "D": 5,
        "客观情境": "测试场景",
        "近期综合轨迹": "测试轨迹",
        "内心活动": "测试中",
        "近期目标": "通过测试",
        "对应时间": "2026-03-13 12:00:00"
    }
    import json
    state_file.write_text(json.dumps(default_state, ensure_ascii=False), encoding='utf-8')
    return str(state_file)


@pytest.fixture
def mock_env(monkeypatch):
    """设置测试环境变量"""
    monkeypatch.setenv("LARGE_LLM_API_KEY", "test-key")
    monkeypatch.setenv("LARGE_LLM_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("LARGE_LLM_MODEL", "test-model")
    monkeypatch.setenv("SMALL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SMALL_LLM_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("SMALL_LLM_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "test")
    monkeypatch.setenv("PG_PASSWORD", "test")
    monkeypatch.setenv("PG_DB", "test_db")
    monkeypatch.setenv("ENABLE_NEO4J", "False")
