"""Opt-in real model smoke check with synthetic state, no database or user history."""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true', help='Explicitly allow a small synthetic real-model check')
    args = parser.parse_args()
    if not args.real_model:
        parser.error('Real model calls require --real-model')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    os.environ.update(ENABLE_NEO4J='False', START_TIME='', TIME_ACCEL_FACTOR='1')
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='ember-model-smoke-') as directory:
        folder = Path(directory)
        (folder / 'config').mkdir()
        shutil.copy2(ROOT / 'config/prompts.yaml', folder / 'config/prompts.yaml')
        state = {'P': 5, 'A': 5, 'D': 5, '客观情境': '测试图书馆，整理笔记', '近期综合轨迹': '仅为合成功能抽查',
            '内心活动': '准备回答测试问题', '近期目标': '核对当前场景', '对应时间': '',
            'scene': {'character_location': '测试图书馆', 'user_location': None, 'communication_mode': 'unknown', 'remote_contact_confirmed': False}}
        (folder / 'config/state.json').write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.chdir(folder)
        sys.path.insert(0, str(ROOT))
        try:
            from core.event_bus import EventBus
            from core.experience import Experience
            from brain.core import Brain
            from memory.short_term import ShortTermMemory
            from persona.state_manager import StateManager
            from tools.processor import ToolCallProcessor
            from config.settings import settings
            from brain.tag_utils import extract_thought_and_speech
            bus = EventBus()
            bus.experience = Experience()
            hippocampus = SimpleNamespace(query_memory=lambda **_: {'found': False, 'memories': [], 'entities': [], 'relations': []})
            processor = ToolCallProcessor.create_with_memory_tool(hippocampus)
            memory = ShortTermMemory(base_prompt=settings.SYSTEM_PROMPT)
            manager = StateManager(bus, hippocampus, memory, processor)
            manager.ready = True
            brain = Brain(bus, manager, memory, hippocampus, processor)
            replies = []
            bus.subscribe('llm.finished', lambda event: replies.append(extract_thought_and_speech(event.data['text'])[1]))
            brain.process_dialogue('这是隔离的功能抽查。我现在在测试宿舍，用微信与你远程联系；我们已经交换过微信联系方式。请用一句话确认我们现在的交流方式，不要描述双方同处一地。')
            scene = manager.current_state['scene']
            assert scene['user_location'] == '测试宿舍' and scene['communication_mode'] == 'remote' and scene['remote_contact_confirmed'], scene
            assert replies and bus.turns.snapshot()['status'] == 'completed'
            bus.experience.activity('start', duration=1)
            brain.process_dialogue('我刚开启的是个人自习计时，还没有邀请你一起。现在只是通过微信说一声，请简短回应。')
            assert len(replies) == 2 and bus.turns.snapshot()['status'] == 'completed'
            print(json.dumps({'result': 'passed', 'dialogues': 2, 'world_time': bus.formatted_logical_now,
                'scene': manager.current_state['scene'], 'speech': replies, 'business_database_accessed': False}, ensure_ascii=False))
        finally:
            os.chdir(original)


if __name__ == '__main__':
    main()
