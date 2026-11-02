import os
import dotenv
from dataclasses import dataclass
import yaml
import time
import logging
import json
import math

logger = logging.getLogger(__name__)

dotenv.load_dotenv()


@dataclass
class ModelConfig:
    name: str
    api_key: str
    base_url: str


class Settings:
    SMALL_LLM = ModelConfig(
        name=os.getenv("SMALL_LLM_MODEL", ""),
        api_key=os.getenv("SMALL_LLM_API_KEY", ""),
        base_url=os.getenv("SMALL_LLM_BASE_URL", ""),
    )

    LARGE_LLM = ModelConfig(
        name=os.getenv("LARGE_LLM_MODEL", ""),
        api_key=os.getenv("LARGE_LLM_API_KEY", ""),
        base_url=os.getenv("LARGE_LLM_BASE_URL", ""),
    )

    EMBEDDING_MODEL = ModelConfig(
        name=os.getenv("EMBEDDING_MODEL", ""),
        api_key=os.getenv("EMBEDDING_API_KEY", ""),
        base_url=os.getenv("EMBEDDING_BASE_URL", ""),
    )

    HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "10"))

    # 加载 prompts.yaml
    if os.path.exists("./config/prompts.yaml"):
        with open("./config/prompts.yaml", "r", encoding="utf-8") as f:
            PROMPTS = yaml.safe_load(f) or {}
    else:
        PROMPTS = {}

    STATE = {}
    STATE_IS_DEFAULT = False
    if os.path.exists("./config/state.json"):
        with open("./config/state.json", "r", encoding="utf-8") as f:
            try:
                STATE = json.load(f)
            except json.JSONDecodeError:
                STATE = {}

    if not STATE and os.path.exists("./config/state_default.json"):
        STATE_IS_DEFAULT = True
        with open("./config/state_default.json", "r", encoding="utf-8") as f:
            try:
                STATE = json.load(f)
            except json.JSONDecodeError:
                STATE = {}

    logger.info(f"Loaded state: {STATE}")

    CHARACTER_NAME = os.getenv("CHARACTER_NAME", "依鸣")
    CORE_PERSONA = PROMPTS.get("core_persona", "")
    SYSTEM_PROMPT = CORE_PERSONA + PROMPTS.get("system_prompt", "")
    STATE_UPDATE_PROMPT = PROMPTS.get("state_update_prompt", "")
    GRAPH_CONSOLIDATION_PROMPT = PROMPTS.get("graph_consolidation_prompt", "")
    TOOL_USAGE_GUIDELINES = PROMPTS.get("tool_usage_guidelines", "")

    TIME_ACCEL_FACTOR = float(os.getenv("TIME_ACCEL_FACTOR", "1.0"))
    if not math.isfinite(TIME_ACCEL_FACTOR) or TIME_ACCEL_FACTOR <= 0:
        raise ValueError('TIME_ACCEL_FACTOR 必须是有限的正数')
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
    CONFIG_WARNINGS = []

    # 空闲超时使用真实秒数，动态加速不改变等待间隔。
    STATE_IDLE_MIN_TIMEOUT = float(os.getenv("STATE_IDLE_MIN_TIMEOUT", "30"))
    STATE_IDLE_MAX_TIMEOUT = float(os.getenv("STATE_IDLE_MAX_TIMEOUT", "3600"))

    IDLE_STATE_UPDATE_PROMPT = PROMPTS.get("idle_state_update_prompt", "")
    IDLE_SPEAKING_UPDATE_PROMPT = PROMPTS.get("idle_speaking_update_prompt", "")
    STATE_UPDATE_INTERVAL = int(
        os.getenv("STATE_UPDATE_INTERVAL", "1")
    )  # 每几轮对话更新一次状态
    START_TIME = os.getenv("START_TIME", "").strip()
    if START_TIME.startswith("#"):
        CONFIG_WARNINGS.append("START_TIME 只有注释，已按当前现实时间启动")
        START_TIME = ""
    if START_TIME == "?":
        START_TIME = STATE.get("对应时间", "")

    CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "20"))

    PG_HOST = os.getenv("PG_HOST", "127.0.0.1")
    PG_PORT = int(os.getenv("PG_PORT", "5433"))
    PG_USER = os.getenv("PG_USER", "postgres")
    PG_PASSWORD = os.getenv("PG_PASSWORD", "your_password")
    PG_DB = os.getenv("PG_DB", "ember_db")

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "your_password")
    NEO4J_DB = os.getenv("NEO4J_DB", "neo4j")
    ENABLE_NEO4J = os.getenv("ENABLE_NEO4J", "True").lower() == "true"

    MEMORY_JUDGE_PROMPT = PROMPTS.get("memory_judge_prompt", "")
    MEMORY_ENCODING_PROMPT = PROMPTS.get("memory_encoding_prompt", "")
    MEMORY_DECENT_FACTOR = float(os.getenv("MEMORY_DECENT_FACTOR", "0.5"))
    RECALL_TOP_K = int(os.getenv("RECALL_TOP_K", "10"))
    MEMORY_ENCODE_MESSAGES = max(2, int(os.getenv('MEMORY_ENCODE_MESSAGES', '12')))
    MEMORY_ENCODE_INTERVAL = max(10, float(os.getenv('MEMORY_ENCODE_INTERVAL', '120')))
    MEMORY_RETRY_INTERVAL = max(5, float(os.getenv('MEMORY_RETRY_INTERVAL', '60')))

    # LLM generation temperature; overridable via env for benchmark/determinism
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))

    # Tool system configuration
    ENABLE_TOOLS = os.getenv("ENABLE_TOOLS", "True").lower() == "true"
    TOOL_MAX_CALLS_PER_TURN = int(os.getenv("TOOL_MAX_CALLS_PER_TURN", "3"))
    TOOL_EXECUTOR_PERMISSION = os.getenv("TOOL_EXECUTOR_PERMISSION", "READWRITE")
    TOOL_DEFAULT_TIMEOUT = float(os.getenv("TOOL_DEFAULT_TIMEOUT", "30.0"))
    TOOL_FILE_SANDBOX_DIR = os.getenv("TOOL_FILE_SANDBOX_DIR", "./data/files")
    TOOL_NOTES_DIR = os.getenv("TOOL_NOTES_DIR", "./data/notes")

    # Tool-specific API keys (optional)
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
    SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")


settings = Settings()
