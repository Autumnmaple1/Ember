import json
import threading


class ShortTermMemory:
    def __init__(self, max_memory_size=20, base_prompt=None, state="initial"):
        self.max_memory_size = max_memory_size
        self.memory = []
        self.base_prompt = "你是一名助手"
        if base_prompt:
            self.base_prompt = base_prompt
        self.current_state = state

    def _add_front(self, role, content):
        self.memory.insert(0, {"role": role, "content": content})
        self._truncate_memory()

    def _add_back(self, role, content):
        self.memory.append({"role": role, "content": content})
        self._truncate_memory()

    def _truncate_memory(self):
        if len(self.memory) > self.max_memory_size:
            self.memory = self.memory[-self.max_memory_size :]

    def async_log(self, filename, content):
        def _log():
            with open(filename, "a", encoding="utf-8", buffering=1) as f:
                f.write(content + "\n")

        threading.Thread(target=_log).start()

    def _async_log_clear(self, filename):
        def _log():
            with open(filename, "w", encoding="utf-8", buffering=1) as f:
                f.write("")

        threading.Thread(target=_log).start()

    def add_message(self, role, content):
        self.async_log("chat_history.log", f"{{{role}: {content}}}")
        self._add_back(role, content)

    def update_base_prompt(self, new_base_prompt):
        self.base_prompt = new_base_prompt

    def get_full_messages(self):
        system_content = self.base_prompt
        return [
            {"role": "system", "content": system_content},
        ] + self.memory

    def get_memory(self):
        return {"history": self.memory}

    def clear_memory(self):
        self.memory = []
        self._async_log_clear("chat_history.log")
