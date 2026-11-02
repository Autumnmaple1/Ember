"""Opt-in four synthetic dialogue probes. No user history, database or TTS."""
import argparse
import copy
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
    parser.add_argument('--real-model', action='store_true')
    parser.add_argument('--scenario', choices=['all', 'continuous', 'next_day', 'unknown_space', 'remote_visibility'], default='all')
    args = parser.parse_args()
    if not args.real_model:
        parser.error('Real model calls require --real-model')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    os.environ.update(ENABLE_NEO4J='False', START_TIME='', TIME_ACCEL_FACTOR='1')
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='ember-presence-smoke-') as directory:
        folder = Path(directory)
        (folder / 'config').mkdir()
        shutil.copy2(ROOT / 'config/prompts.yaml', folder / 'config/prompts.yaml')
        os.chdir(folder)
        sys.path.insert(0, str(ROOT))
        try:
            from core.event_bus import EventBus
            from core.experience import Experience
            from core.time_utils import to_epoch
            from brain.core import Brain
            from brain.tag_utils import extract_thought_and_speech
            from config.settings import settings
            from memory.short_term import ShortTermMemory
            from persona.state_manager import StateManager
            from tools.processor import ToolCallProcessor

            scenarios = [
                ('continuous', '2026-11-02 15:19:20', '这段证明我有点跟不上。', '最后一步省得太多了，我也回头看了两遍。',
                    '就是最后这句，怎么突然就得到结论了？', 'remote'),
                ('next_day', '2026-11-01 23:58:00', '这段明天再看吧，晚安。', '嗯，明天再接着看。',
                    '现在下午了，我们接着昨天那段聊吧。', 'remote'),
                ('unknown_space', '2026-11-02 15:19:20', '我想把第二层循环去掉。', '可以先看里面那个计数能不能提前算好。',
                    '我刚试了，真的少了一层循环，有点开心。', 'unknown'),
                ('remote_visibility', '2026-11-02 15:19:20', '我回到宿舍了，还在用微信和你聊。', '我这页也快整理完了。',
                    '你能看看我桌上那本书，告诉我是哪一版吗？', 'remote'),
            ]
            results = []
            for label, previous_time, previous_user, previous_reply, text, mode in scenarios:
                if args.scenario != 'all' and label != args.scenario:
                    continue
                case = folder / label
                (case / 'config').mkdir(parents=True)
                os.chdir(case)
                bus = EventBus()
                bus.restore_logical_time('2026-11-02 15:20:00')
                bus.experience = Experience()
                hippocampus = SimpleNamespace(query_memory=lambda **_: {'found': False, 'memories': [], 'entities': [], 'relations': []})
                processor = ToolCallProcessor.create_with_memory_tool(hippocampus)
                memory = ShortTermMemory(base_prompt=settings.SYSTEM_PROMPT)
                memory.add_message('user', previous_user, timestamp=int(to_epoch(previous_time) * 1000))
                memory.add_message('assistant', previous_reply, timestamp=int(to_epoch(previous_time) * 1000) + 1000)
                state = StateManager(bus, hippocampus, memory, processor)
                scene = {'character_location': '测试图书馆', 'user_location': '测试宿舍' if mode == 'remote' else None,
                    'communication_mode': mode, 'remote_contact_confirmed': mode == 'remote'}
                state.current_state = {'P': 6, 'A': 4, 'D': 5, '客观情境': '测试图书馆，整理算法笔记',
                    '内心活动': '这段证明写得太跳了，想弄清楚中间一步', '近期目标': '弄清笔记里省略的一步',
                    '近期综合轨迹': '仅为合成抽查，不是业务历史', '对应时间': bus.formatted_logical_now,
                    'state_source': 'interaction', 'scene_status': 'ready', 'scene': copy.deepcopy(scene)}
                state.ready = True
                brain = Brain(bus, state, memory, hippocampus, processor)
                replies = []
                bus.subscribe('llm.finished', lambda event: replies.append(extract_thought_and_speech(event.data['text'])[1]))
                brain.process_dialogue(text)
                assert len(replies) == 1 and bus.turns.snapshot()['status'] == 'completed', label
                assert state.current_state['scene'] == scene, (label, state.current_state['scene'])
                result = {'scenario': label, 'input': text, 'reply': replies[0]}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            print(json.dumps({'result': 'completed', 'synthetic_dialogues': len(results),
                'business_database_accessed': False, 'perceived_lifelikeness': 'requires human evaluation'}, ensure_ascii=False))
        finally:
            os.chdir(original)


if __name__ == '__main__':
    main()
