import time
import threading
import logging
import json
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient

logger = logging.getLogger(__name__)


class Hippocampus:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.event_bus.subscribe("memory.preprocess", self._on_preprocess_request)
        self.llm_client = LLMClient()

    def _load_experience(self):
        try:
            with open("./config/chat_history.log", "r", encoding="utf-8") as f:
                content = f.read()
            with open("./config/chat_history.log", "w", encoding="utf-8") as f:
                f.write("")
            return content

        except FileNotFoundError:
            logger.warning("chat_history.log not found")
            return []

    def _on_preprocess_request(self, event: Event):
        res = self._load_experience()
        system_prompt = settings.MEMORY_ENCODING_PROMPT
        user_prompt = f"提供的日志如下：\n\n{res}"
        resp = self.llm_client.one_chat(
            settings.LARGE_LLM,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        try:
            memories = json.loads(resp)
            for mem in memories:
                logger.info(f"Preprocessed memory: {mem}")
                self.event_bus.publish(Event("memory.store", mem))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode LLM response: {e}")
