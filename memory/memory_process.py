import time
import threading
import logging
import json
import concurrent.futures
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
            return ""

    def _on_preprocess_request(self, event: Event):
        res = self._load_experience()
        if not res:
            logger.info("No experience log found, skipping preprocessing.")
            return

        system_prompt = settings.MEMORY_ENCODING_PROMPT
        user_prompt = f"提供的日志如下：\n\n{res}"
        resp = self.llm_client.one_chat(
            settings.LARGE_LLM,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        if resp is None:
            logger.error("LLM returned no response during memory preprocessing (resp is None)")
            return

        try:
            memories = json.loads(resp)
            for mem in memories:
                logger.info(f"Preprocessed memory: {mem}")
                self.event_bus.publish(Event("memory.store", mem))
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to decode LLM response during memory preprocessing: {e}. Raw response: {repr(resp)}")
            
            
    def road_memory(self,content):
        system_prompt=settings.CORE_PERSONA+settings.MEMORY_JUDGE_PROMPT
        logger.info(f"Loading Memory\n")
        user_prompt = f"提供的日志如下：\n\n{content}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp=self.llm_client.one_chat(
            settings.SMALL_LLM,
            messages=messages
        )
        key_words=[]
        query=""
        memories=None
        try:
            resp_json=json.loads(resp)
            key_words=resp_json.get("keywords",[])
            query=resp_json.get("query","")
            data = {"query": query, "key_words": key_words}
            memories=json.dumps(self._get_persistence_memory(data), ensure_ascii=False)
            logger.info(f"Road Memory\nQuery: {query}\nKey Words: {key_words}\n")
            
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to decode LLM response during memory loading: {e}. Raw response: {repr(resp)}")
            
        finally:
            return memories
    
    def _get_persistence_memory(self, query_data):
        future = concurrent.futures.Future()

        def on_memory_retrieved(memories):
            if not future.done():
                logger.info(f"Retrieved relevant memories: {len(memories)} items")
                future.set_result(memories)

        query_data["callback"] = on_memory_retrieved
        self.event_bus.publish(Event("memory.query", query_data))        
        try:
            return future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            logger.error("查询持久化记忆超时，跳过查询以维持对话。")
            return []
        except Exception as e:
            logger.error(f"查询持久化记忆时发生异常: {e}")
            return []
        
        
