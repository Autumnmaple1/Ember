"""Opt-in real-model probes with synthetic shared text, no database or TTS."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true')
    args = parser.parse_args()
    if not args.real_model:
        parser.error('Use --real-model to authorize the small external-model probe.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    os.environ.update(ENABLE_NEO4J='False', START_TIME='', TIME_ACCEL_FACTOR='1')
    qa = ROOT / 'output/continuity-20261103'
    qa.mkdir(parents=True, exist_ok=True)
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='model-', dir=qa) as directory:
        folder = Path(directory)
        (folder / 'config').mkdir()
        shutil.copy2(ROOT / 'config/prompts.yaml', folder / 'config/prompts.yaml')
        os.chdir(folder)
        sys.path.insert(0, str(ROOT))
        try:
            from brain.core import Brain
            from brain.tag_utils import extract_thought_and_speech
            from core.event_bus import EventBus
            from core.experience import Experience
            from core.time_utils import to_epoch
            from config.settings import settings
            from memory.short_term import ShortTermMemory
            from persona.state_manager import StateManager
            from tools.processor import ToolCallProcessor
            results = []
            for name, previous, text in [
                ('shared_text', '2026-11-03 15:19:00', '第二段用了什么条件？原文有没有给出归纳假设？'),
                ('return_next_day', '2026-11-02 23:58:00', '接着昨天一起看的第二段吧。我现在还没告诉你在哪。')]:
                case = folder / name
                (case / 'config').mkdir(parents=True)
                os.chdir(case)
                bus = EventBus()
                bus.restore_logical_time('2026-11-03 15:20:00')
                bus.experience = Experience()
                source = {'message_id': str(uuid.uuid4()), 'role': 'user', 'timestamp': int(to_epoch(previous)*1000),
                    'content': '第一段：给定 a > 0。第二段：把不等式两边同时除以 a。其余题目条件未提供。'}
                bus.experience.material('pin', source=source, quote=source['content'], title='合成证明材料', logical_time=previous)
                memory = ShortTermMemory(base_prompt=settings.SYSTEM_PROMPT)
                # The material itself is deliberately absent from the short window.
                memory.add_message('user', '我们下次接着看。', timestamp=source['timestamp'])
                memory.add_message('assistant', '好，下次继续。', timestamp=source['timestamp']+1000)
                hippocampus = SimpleNamespace(query_memory=lambda **_: {'found': False, 'memories': [], 'entities': [], 'relations': []})
                processor = ToolCallProcessor.create_with_memory_tool(hippocampus)
                state = StateManager(bus, hippocampus, memory, processor)
                state.current_state = {'P': 6, 'A': 4, 'D': 5, '客观情境': '测试图书馆，整理笔记',
                    '内心活动': '', '近期目标': '讨论用户实际提供的原文', '近期综合轨迹': '合成抽查',
                    '对应时间': previous, 'state_source': 'interaction', 'scene_status': 'ready',
                    'scene': {'character_location': '测试图书馆', 'user_location': '测试图书馆',
                        'communication_mode': 'face_to_face', 'remote_contact_confirmed': True}}
                state.ready = True
                brain = Brain(bus, state, memory, hippocampus, processor)
                replies = []
                bus.subscribe('llm.finished', lambda event: replies.append(extract_thought_and_speech(event.data['text'])[1]))
                brain.process_dialogue(text)
                assert bus.turns.snapshot()['status'] == 'completed' and len(replies) == 1
                if name == 'return_next_day':
                    assert state.current_state['scene']['user_location'] is None
                    assert state.current_state['scene']['communication_mode'] == 'unknown'
                record = {'scenario': name, 'reply': replies[0], 'scene': copy.deepcopy(state.current_state['scene'])}
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            (qa / 'real-model-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            os.chdir(original)


if __name__ == '__main__':
    main()
