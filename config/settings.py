import os
import dotenv
from dataclasses import dataclass
import yaml
import time
import logging
import json

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

    IMAGE_GEN_MODEL = ModelConfig(
        name=os.getenv("IMAGE_GEN_MODEL", ""),
        api_key=os.getenv("IMAGE_GEN_API_KEY", ""),
        base_url=os.getenv("IMAGE_GEN_BASE_URL", ""),
    )
    ENABLE_BACKGROUND_GENERATION = (
        os.getenv("ENABLE_BACKGROUND_GENERATION", "true").lower() == "true"
    )

    HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "10"))

    # 加载 prompts.yaml
    if os.path.exists("./config/prompts.yaml"):
        with open("./config/prompts.yaml", "r", encoding="utf-8") as f:
            PROMPTS = yaml.safe_load(f) or {}
    else:
        PROMPTS = {}

    STATE = {}
    if os.path.exists("./config/state.json"):
        with open("./config/state.json", "r", encoding="utf-8") as f:
            try:
                STATE = json.load(f)
            except json.JSONDecodeError:
                STATE = {}

    if not STATE and os.path.exists("./config/state_default.json"):
        with open("./config/state_default.json", "r", encoding="utf-8") as f:
            try:
                STATE = json.load(f)
            except json.JSONDecodeError:
                STATE = {}
        with open("./config/state.json", "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)

    logger.info(f"Loaded state: {STATE}")

    CHARACTER_NAME = os.getenv("CHARACTER_NAME", "依鸣")
    LIVE2D_MODEL_PATH = os.getenv(
        "LIVE2D_MODEL_PATH",
        "/models/yachiyo/八千代辉夜姬.model3.json",
    )
    CORE_PERSONA = PROMPTS.get("core_persona", "")
    SYSTEM_PROMPT = CORE_PERSONA + PROMPTS.get("system_prompt", "")
    STATE_UPDATE_PROMPT = PROMPTS.get("state_update_prompt", "")
    GRAPH_CONSOLIDATION_PROMPT = PROMPTS.get("graph_consolidation_prompt", "")
    TOOL_USAGE_GUIDELINES = PROMPTS.get("tool_usage_guidelines", "")
    PRE_ROUTING_PROMPT = PROMPTS.get("pre_routing_prompt", "")

    TIME_ACCEL_FACTOR = float(os.getenv("TIME_ACCEL_FACTOR", "1.0"))

    # 空闲超时时间：配置值为真实世界秒数，内部乘以加速因子转换为系统时间
    STATE_IDLE_MIN_TIMEOUT = int(os.getenv("STATE_IDLE_MIN_TIMEOUT", "30")) * int(TIME_ACCEL_FACTOR)
    STATE_IDLE_MAX_TIMEOUT = int(os.getenv("STATE_IDLE_MAX_TIMEOUT", "3600")) * int(TIME_ACCEL_FACTOR)

    IDLE_STATE_UPDATE_PROMPT = PROMPTS.get("idle_state_update_prompt", "")
    IDLE_SPEAKING_UPDATE_PROMPT = PROMPTS.get("idle_speaking_update_prompt", "")
    STATE_UPDATE_INTERVAL = int(
        os.getenv("STATE_UPDATE_INTERVAL", "1")
    )  # 每几轮对话更新一次状态
    START_TIME = os.getenv(
        "START_TIME",
        time.time(),
    )
    if START_TIME == "?":
        with open("./config/state.json", "r", encoding="utf-8") as f:
            state_data = json.load(f)
        START_TIME = state_data.get("对应时间", time.time())

    CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "20"))

    PG_HOST = os.getenv("PG_HOST", "localhost")
    PG_PORT = int(os.getenv("PG_PORT", "5432"))
    PG_USER = os.getenv("PG_USER", "postgres")
    PG_PASSWORD = os.getenv("PG_PASSWORD", "your_password")
    PG_DB = os.getenv("PG_DB", "ember_db")

    ENABLE_GRAPH_MEMORY = (
        os.getenv("ENABLE_GRAPH_MEMORY", "True").lower() == "true"
    )

    MEMORY_JUDGE_PROMPT = PROMPTS.get("memory_judge_prompt", "")
    MEMORY_ENCODING_PROMPT = PROMPTS.get("memory_encoding_prompt", "")
    MEMORY_DECENT_FACTOR = float(os.getenv("MEMORY_DECENT_FACTOR", "0.5"))
    RECALL_TOP_K = int(os.getenv("RECALL_TOP_K", "10"))

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
