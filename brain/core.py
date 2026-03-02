from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager


class Brain:
    def __init__(self, event_bus: EventBus, state_manager: StateManager):
        self.llm_client = LLMClient()
        self.event_bus = event_bus
        self.state_manager = state_manager

    def generate_response(self, user_message, memory: ShortTermMemory):
        dynamic_prompt = settings.SYSTEM_PROMPT + self.state_manager.prompt_injection

        memory.update_base_prompt(dynamic_prompt)

        memory.add_message("user", user_message)
        messages = memory.get_full_messages()

        full_content = ""

        for chunk in self.llm_client.stream_chat(
            model_config=settings.LARGE_LLM,
            messages=messages,
        ):
            full_content += chunk
            yield chunk

        if full_content:
            memory.add_message("assistant", full_content)
            self.event_bus.publish(
                Event(name="user_interaction", data=memory.get_memory())
            )
