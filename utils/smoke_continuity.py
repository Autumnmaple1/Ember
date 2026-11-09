"""Opt-in bounded real state+reply flow, wholly inside a synthetic workspace."""
import argparse
from contextlib import chdir
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
    parser.add_argument('--turns', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    if not args.real_model:
        parser.error('Pass --real-model to enable the bounded synthetic flow')
    from dotenv import dotenv_values
    values = dotenv_values(ROOT/'deploy/service.env' if (ROOT/'deploy/service.env').exists() else ROOT/'.env')
    for prefix in ('LARGE_LLM', 'SMALL_LLM'):
        for suffix in ('API_KEY', 'BASE_URL', 'MODEL'):
            key = prefix + '_' + suffix
            if not values.get(key):
                parser.error('Missing model configuration')
            os.environ[key] = values[key]
    os.environ.update(EMBER_NO_DOTENV='1', DIALOGUE_POLICY='expressive_v1', ROLE_CONTINUITY_ENABLED='true', PROACTIVE_POLICY='events_v1')
    output = ROOT/'output/dialogue-expression'
    output.mkdir(parents=True, exist_ok=True)
    report, calls = [], []
    with tempfile.TemporaryDirectory(prefix='ember-continuity-', dir=output) as folder, chdir(folder):
        target = Path('config')
        target.mkdir()
        shutil.copy2(ROOT/'config/prompts.yaml', target/'prompts.yaml')
        sys.path.insert(0, str(ROOT))
        from brain.core import Brain
        from brain.tag_utils import extract_thought_and_speech
        from config.settings import settings
        from core.event_bus import EventBus
        from core.time_utils import to_epoch
        from memory.short_term import ShortTermMemory
        from persona.state_manager import StateManager
        from persona.continuity import load_continuity
        from tools.processor import ToolCallProcessor
        bus = EventBus()
        now = to_epoch('2026-11-08 15:00:00')
        bus.restore_logical_time(now)
        memory = ShortTermMemory(base_prompt=settings.SYSTEM_PROMPT)
        hippocampus = SimpleNamespace(query_memory=lambda **_: {'found': False, 'memories': [], 'entities': [], 'relations': []})
        tools = ToolCallProcessor.create_with_memory_tool(hippocampus)
        manager = StateManager(bus, hippocampus, memory, tools)
        manager.current_state = {'P': 6, 'A': 5, 'D': 5, '客观情境': '宿舍，整理自己的笔记',
            '近期目标': '整理一页自己的笔记', '内心活动': '想把这页理清楚', '近期综合轨迹': '合成测试，没有真实共同经历',
            '对应时间': bus.formatted_logical_now, 'state_source': 'interaction', 'scene_status': 'ready',
            'scene': {'character_location': '宿舍', 'user_location': None, 'communication_mode': 'unknown', 'remote_contact_confirmed': False}}
        manager.ready = True
        brain = Brain(bus, manager, memory, hippocampus, tools)

        def counted(method, kind):
            def invoke(*arguments, **keywords):
                if len(calls) >= 12:
                    raise RuntimeError('Synthetic call budget exceeded')
                calls.append({'kind': kind, 'call_type': keywords.get('call_type', '')})
                return method(*arguments, **keywords)
            return invoke

        for client in (manager.llm_client, brain.llm_client):
            client.one_chat = counted(client.one_chat, 'state_or_tool')
            client.stream_chat = counted(client.stream_chat, 'reply')
        speech = []
        bus.subscribe('llm.finished', lambda event: speech.append(extract_thought_and_speech(event.data['text'])[1]))
        inputs = ['今天你自己想做点什么？', '哈哈，听着有点像AI在写计划。其实我想随便聊聊。',
                  '你不必总顺着我。你自己觉得呢？']
        for index, text in enumerate(inputs[:args.turns]):
            at = now + (index * 30 if index < 2 else 660)
            bus.restore_logical_time(at)
            if index == 2:
                manager._advance_role(at)
            before = len(speech)
            brain.process_dialogue(text, {'message_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-continuity:' + str(index))),
                'timestamp': int(at * 1000)})
            turn = bus.turns.snapshot()
            if not turn['released'] or turn['status'] != 'completed':
                raise SystemExit('Synthetic turn failed; inspect ignored local diagnostic logs')
            assert manager.current_state['scene']['user_location'] is None
            assert manager.current_state['scene']['communication_mode'] == 'unknown'
            continuity = load_continuity(manager.current_state)
            record = {'input': text, 'reply': speech[-1] if len(speech) > before else '',
                'pads': {key: manager.current_state[key] for key in ('P', 'A', 'D')},
                'perspective': copy.deepcopy(manager.current_state.get('reply_perspective')),
                'emotion': continuity['emotion'], 'activity': continuity['activity'], 'event_count': len(continuity['events'])}
            report.append(record)
            (output/f'real-continuity-flow-{args.turns}turns.json').write_text(json.dumps({'turns': report, 'calls': calls}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'turn': index + 1, 'reply': record['reply'], 'event_count': record['event_count']}, ensure_ascii=False), flush=True)
    print(json.dumps({'completed_turns': len(report), 'model_requests': len(calls), 'business_database_accessed': False,
        'tts_requests': 0, 'user_location_invented': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
