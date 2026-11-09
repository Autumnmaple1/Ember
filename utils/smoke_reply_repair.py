"""At most two real API calls, synthetic conversations, no business DB or files."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from contextlib import chdir

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true')
    args = parser.parse_args()
    if not args.real_model:
        raise SystemExit('Pass --real-model to authorize two synthetic model checks')
    sys.stdout.reconfigure(encoding='utf-8')
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / 'deploy/service.env' if (ROOT/'deploy/service.env').exists() else ROOT/'.env')
    for key in ('LARGE_LLM_API_KEY', 'LARGE_LLM_BASE_URL', 'LARGE_LLM_MODEL'):
        if not values.get(key): raise SystemExit('Missing model configuration')
    with tempfile.TemporaryDirectory(prefix='ember-reply-smoke-') as folder, chdir(folder):
        target = Path(folder)/'config'; target.mkdir()
        shutil.copy2(ROOT/'config/prompts.yaml', target/'prompts.yaml')
        os.environ['EMBER_NO_DOTENV'] = '1'
        sys.path.insert(0, str(ROOT))
        from brain.dialogue_context import build_dialogue_messages
        from brain.tag_utils import extract_thought_and_speech
        from brain.reasoning_filter import remove_reasoning
        from core.time_utils import to_epoch
        from config.settings import settings
        from openai import OpenAI
        client = OpenAI(api_key=values['LARGE_LLM_API_KEY'], base_url=values['LARGE_LLM_BASE_URL'], max_retries=0, timeout=25)
        cases = [
            [{'role': 'user', 'content': '你对未来有什么打算？'},
             {'role': 'assistant', 'content': '明天想洗衣服、做两道题。远一点的还没想好。'},
             {'role': 'user', 'content': '这回答好ai'}],
            [{'role': 'user', 'content': 'Ember，你明天有什么安排？'}],
        ]
        report = []
        for history in cases:
            messages = build_dialogue_messages(settings.SYSTEM_PROMPT, history,
                '角色现在在宿舍，用户位置未知，可以网页交谈。目标：整理明天自己的安排。', to_epoch('2026-11-07 21:30:00'))
            try:
                result = client.chat.completions.create(model=values['LARGE_LLM_MODEL'], messages=messages,
                    extra_body={'enable_thinking': False}, max_tokens=256, temperature=.3)
                text = extract_thought_and_speech(remove_reasoning(result.choices[0].message.content or ''))[1]
                report.append({'input': history[-1]['content'], 'reply': text})
            except Exception as exc:
                report.append({'error': type(exc).__name__})
                break
        client.close()
        output = ROOT/'output/reply-repair'; output.mkdir(parents=True, exist_ok=True)
        (output/'real-model-synthetic.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__': main()
