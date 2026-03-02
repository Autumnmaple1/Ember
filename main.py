import time
from core.event_bus import EventBus
from core.heartbeat import Heartbeat
from persona.state_manager import StateManager
from brain.core import Brain
from memory.short_term import ShortTermMemory
from config.settings import settings
import config.logging_config


def main():
    event_bus = EventBus()
    state_manager = StateManager(event_bus)
    heartbeat = Heartbeat(event_bus, interval=settings.HEARTBEAT_INTERVAL)

    brain = Brain(event_bus, state_manager)
    memory = ShortTermMemory(base_prompt=settings.SYSTEM_PROMPT)

    heartbeat.start()

    print("✨ 依鸣已在校园中醒来...")
    print("-------------------------")

    try:
        while True:
            user_input = input("user: ")
            if user_input.lower() in ["exit", "quit"]:
                print("退出程序。")
                break

            print("依鸣:", end=" ", flush=True)
            for chunk in brain.generate_response(user_input, memory):
                print(chunk, end="", flush=True)
            print()

    except KeyboardInterrupt:
        print("\n程序被用户中断。")
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    main()
