"""Synthetic fixed-context comparison; default prepares prompts without API IO."""
import argparse
from contextlib import chdir
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cases = json.loads((ROOT/'utils/dialogue_expression_cases.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true')
    parser.add_argument('--full-review', action='store_true', help='Explicitly allow all 12 paired cases (24 requests)')
    parser.add_argument('--resume', action='store_true', help='Resume only identical prepared contexts after a network failure')
    parser.add_argument('--tag', default='', help='Keep a separate named comparison report')
    parser.add_argument('--case', action='append', choices=[case['id'] for case in cases])
    args = parser.parse_args()
    if args.tag and (not args.tag.replace('-', '').replace('_', '').isalnum() or len(args.tag) > 40):
        parser.error('Report tag must contain only letters, digits, dash and underscore')
    selected = args.case or (['teasing', 'disagreement'] if args.real_model and not args.full_review else [case['id'] for case in cases])
    if len(set(selected)) != len(selected) or args.real_model and len(selected) > (12 if args.full_review else 2):
        parser.error('Use --full-review for more than two real paired cases; maximum 12')
    from dotenv import dotenv_values
    values = dotenv_values(ROOT/'deploy/service.env' if (ROOT/'deploy/service.env').exists() else ROOT/'.env') if args.real_model else {}
    if args.real_model and not all(values.get(key) for key in ('LARGE_LLM_API_KEY', 'LARGE_LLM_BASE_URL', 'LARGE_LLM_MODEL')):
        parser.error('Missing model configuration')
    output = ROOT/'output/dialogue-expression'
    output.mkdir(parents=True, exist_ok=True)
    report, client = [], None
    report_name = 'full-real-comparison.json' if args.full_review else 'real-comparison.json'
    if args.tag:
        report_name = report_name.replace('.json', '-' + args.tag + '.json')
    if args.resume and args.real_model and (output/report_name).exists():
        report = json.loads((output/report_name).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='ember-expression-', dir=output) as folder, chdir(folder):
        target = Path(folder)/'config'
        target.mkdir()
        shutil.copy2(ROOT/'config/prompts.yaml', target/'prompts.yaml')
        os.environ['EMBER_NO_DOTENV'] = '1'
        sys.path.insert(0, str(ROOT))
        from brain.dialogue_context import build_dialogue_messages
        from brain.tag_utils import extract_thought_and_speech
        from brain.reasoning_filter import remove_reasoning
        from config.dialogue_policy import compile_persona
        from config.settings import settings
        from core.event_bus import EventBus
        from core.time_utils import to_epoch
        from persona.state_manager import StateManager
        from persona.reply_view import make_reply_view
        if args.real_model:
            from openai import OpenAI
            client = OpenAI(api_key=values['LARGE_LLM_API_KEY'], base_url=values['LARGE_LLM_BASE_URL'], max_retries=0, timeout=25)
        try:
            for index, case in enumerate(case for case in cases if case['id'] in selected):
                bus = EventBus()
                bus.restore_logical_time(case['time'])
                now = to_epoch(case['time'])
                history = case['history']
                manager = StateManager(bus, SimpleNamespace(), SimpleNamespace(get_memory=lambda: {'history': history}), SimpleNamespace())
                manager.current_state = {'P': 5, 'A': 5, 'D': 5, '对应时间': case['time'], '客观情境': '宿舍里，整理笔记',
                    '近期目标': case['intent'], '内心活动': case['feeling'], '近期综合轨迹': '仅为合成评估',
                    'scene_status': 'ready', 'state_source': 'interaction', 'scene': {
                        'character_location': '宿舍', 'user_location': '宿舍' if case.get('mode') == 'face_to_face' else None,
                        'communication_mode': case.get('mode', 'unknown'), 'remote_contact_confirmed': False}}
                manager.current_state['reply_perspective'] = make_reply_view({'reply_perspective': case}, now, 'dialogue')
                # Alternate request order; same model, parameters and history in each pair.
                versions = ('legacy', 'expressive_v1') if index % 2 == 0 else ('expressive_v1', 'legacy')
                for version in versions:
                    settings.DIALOGUE_POLICY = version
                    settings.ROLE_CONTINUITY_ENABLED = version == 'expressive_v1'
                    system, _ = compile_persona(settings.RAW_CORE_PERSONA, settings.PROMPTS.get('system_prompt', ''), version)
                    messages = build_dialogue_messages(system, history, manager.prompt_injection, now, policy_version=version)
                    record = {'case': case['id'], 'version': version, 'messages': messages}
                    previous = next((row for row in report if row['case'] == case['id'] and row['version'] == version and 'reply' in row), None)
                    if previous:
                        if previous['messages'] != messages:
                            raise SystemExit('Saved context changed; do not resume this report')
                        continue
                    if client:
                        try:
                            result = client.chat.completions.create(model=values['LARGE_LLM_MODEL'], messages=messages,
                                temperature=.5, max_tokens=450, extra_body={'enable_thinking': False})
                        except Exception as exc:
                            record['error'] = type(exc).__name__
                            report.append(record)
                            (output/'failed-comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                            raise SystemExit(f'Model comparison stopped: {type(exc).__name__}; no automatic retries') from None
                        record['reply'] = extract_thought_and_speech(remove_reasoning(result.choices[0].message.content or ''))[1]
                        print(json.dumps({'case': case['id'], 'version': version, 'generated': True}, ensure_ascii=False), flush=True)
                    report.append(record)
                    (output/(report_name if client else 'prepared-cases.json')).write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            if client:
                client.close()
    print(json.dumps({'cases': len(selected), 'real_requests': len(report) if args.real_model else 0,
        'business_database_accessed': False, 'quality': 'requires human review; synthetic perspectives supplied'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
