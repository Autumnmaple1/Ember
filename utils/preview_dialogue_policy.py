"""Preview exact stock adaptations without importing runtime settings or models."""
import argparse
import difflib
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import yaml
    from config.dialogue_policy import compile_persona, adapt_idle_prompt
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompts', type=Path, default=ROOT/'config/prompts.yaml')
    parser.add_argument('--output', type=Path, default=ROOT/'output/dialogue-expression')
    args = parser.parse_args()
    original = args.prompts.read_bytes()
    prompts = yaml.safe_load(original) or {}
    old, _ = compile_persona(prompts.get('core_persona', ''), prompts.get('system_prompt', ''), 'legacy')
    new, count = compile_persona(prompts.get('core_persona', ''), prompts.get('system_prompt', ''),
        'expressive_v1', prompts.get('dialogue_style'))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'effective-prompt.txt').write_text(new, encoding='utf-8')
    (args.output/'expression-preview.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True),
        new.splitlines(True), fromfile='original-YAML', tofile='expressive_v1-effective')), encoding='utf-8')
    for key in ('idle_state_update_prompt', 'idle_speaking_update_prompt'):
        raw = prompts.get(key, '')
        adapted = adapt_idle_prompt(raw, 'expressive_v1')
        (args.output/(key + '.diff')).write_text(''.join(difflib.unified_diff(raw.splitlines(True),
            adapted.splitlines(True), fromfile='original-YAML', tofile='effective')), encoding='utf-8')
    assert args.prompts.read_bytes() == original
    print(f'Stock adaptations: {count}; source unchanged: {hashlib.sha256(original).hexdigest()}')
    print(f'Preview directory: {args.output}')


if __name__ == '__main__':
    main()
