import os
import dotenv
from dataclasses import dataclass
import yaml
import time

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

    with open("./config/prompts.yaml", "r", encoding="utf-8") as f:
        PROMPTS = yaml.safe_load(f)

    SYSTEM_PROMPT = PROMPTS.get("core_persona", "")
    STATE = PROMPTS.get("state", "")
    STATE_UPDATE_PROMPT = PROMPTS.get("state_update_prompt", "")

    STATE_IDLE_MAX_TIMEOUT = int(os.getenv("STATE_IDLE_MAX_TIMEOUT", "3600"))
    STATE_IDLE_MIN_TIMEOUT = int(os.getenv("STATE_IDLE_MIN_TIMEOUT", "30"))

    IDLE_STATE_UPDATE_PROMPT = PROMPTS.get("idle_state_update_prompt", "")
    IDLE_SPEAKING_UPDATE_PROMPT = PROMPTS.get("idle_speaking_update_prompt", "")

    TIME_ACCEL_FACTOR = float(os.getenv("TIME_ACCEL_FACTOR", "1.0"))
    START_TIME = os.getenv(
        "START_TIME",
        time.time(),
    )

    CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "20"))


settings = Settings()
