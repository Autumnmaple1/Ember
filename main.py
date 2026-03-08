import logging

logger = logging.getLogger(__name__)
import time
import queue
import threading

from core.event_bus import EventBus, Event
from core.heartbeat import Heartbeat
from persona.state_manager import StateManager
from brain.core import Brain
from memory.short_term import ShortTermMemory
from config.settings import settings
import config.logging_config
from memory.db_memory import DBMemory

from memory.episodic_memory import EpisodicMemory
from memory.memory_process import Hippocampus


input_queue = queue.Queue()


def get_user_input():
    while True:
        user_input = input()
        if user_input.lower() in ["exit", "quit"]:
            logger.info("用户输入退出命令，程序将退出。")
            break
        input_queue.put(user_input)
        logger.info(f"用户输入: {user_input}")


def main():
    event_bus = EventBus()

    heartbeat = Heartbeat(event_bus, interval=settings.HEARTBEAT_INTERVAL)
    memory = ShortTermMemory(
        base_prompt=settings.SYSTEM_PROMPT,
        max_memory_size=settings.CONTEXT_WINDOW_SIZE,
    )

    episodic_memory = EpisodicMemory(event_bus)
    hippocampus = Hippocampus(event_bus)
    state_manager = StateManager(event_bus, hippocampus, memory)
    brain = Brain(event_bus, state_manager, memory, hippocampus)
    db_memory = DBMemory(event_bus)

    heartbeat.start()

    threading.Thread(target=get_user_input, daemon=True).start()

    def display_ai_chunk(event):
        chunk = event.data["text"]
        print(chunk, end="", flush=True)

    def on_ai_start(event):
        print("依鸣:", end=" ", flush=True)

    event_bus.subscribe("llm.chunk", display_ai_chunk)
    event_bus.subscribe("llm.finished", lambda e: print("\nuser:", end="", flush=True))
    event_bus.subscribe("llm.started", on_ai_start)

    print("\n[依鸣已经在南京大学的校园内醒来...]")
    print("----------------------------")
    print("user:", end="", flush=True)

    while True:
        try:
            user_input = input_queue.get(block=True, timeout=0.1)
            if user_input:
                event_bus.publish(Event(name="user.input", data={"text": user_input}))
        except queue.Empty:
            pass

    heartbeat.stop()


if __name__ == "__main__":
    main()
