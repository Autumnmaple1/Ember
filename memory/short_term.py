import json
import threading
import os
import re


def separate_thought_and_speech(text):
    thought_match = re.search(r"<thought>([\s\S]*?)</thought>", text)
    thought = thought_match.group(1).strip() if thought_match else ""

    if "</thought>" in text:
        speech_match = re.search(r"[\s\S]*</thought>\s*([\s\S]*)", text)
        speech = speech_match.group(1).strip() if speech_match else ""
    else:
        speech = re.sub(r"<thought>[\s\S]*", "", text).strip()
        if not speech:
            speech = text.strip()

    return thought, speech


class ShortTermMemory:
    def __init__(self, max_memory_size=20, base_prompt=None, state="initial"):
        self.max_memory_size = max_memory_size
        self.memory = []
        self.base_prompt = "你是一名助手"
        if base_prompt:
            self.base_prompt = base_prompt
        self.current_state = state
        self._load_memory()

    def _load_memory(self):
        try:
            if os.path.exists("./config/chat_memory.json"):
                with open("./config/chat_memory.json", "r", encoding="utf-8") as f:
                    self.memory = json.load(f)
                    self._truncate_memory()
        except Exception as e:
            print(f"Error loading memory: {e}")

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

        threading.Thread(target=_log, daemon=True).start()

    def _async_log_clear(self, filename):
        def _log():
            with open(filename, "w", encoding="utf-8", buffering=1) as f:
                f.write("")

        threading.Thread(target=_log, daemon=True).start()

    def add_message(self, role, content):
        # content = separate_thought_and_speech(content)[1]
        self.async_log("./config/chat_history.log", f"{role}: {content}")
        self._add_back(role, content)
        self._save_memory()

    def _save_memory(self):
        def _save():
            try:
                with open("./config/chat_memory.json", "w", encoding="utf-8") as f:
                    json.dump(self.memory, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"Error saving memory: {e}")

        threading.Thread(target=_save, daemon=True).start()

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
        self._async_log_clear("./config/chat_history.log")
