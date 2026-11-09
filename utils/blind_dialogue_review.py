"""Render anonymous paired responses, with version keys in a separate file."""
import argparse
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    rows = json.loads(args.report.read_text(encoding='utf-8'))
    cases = {}
    for row in rows:
        if row.get('reply') is not None:
            cases.setdefault(row['case'], []).append(row)
    rng = random.Random(817)
    text = ['# 合成对话匿名对照', '', '评价接话、个性、情感动因、连续性、重复与事实冲突。无需选择更长或更礼貌的版本。',
        '', '这些是匿名评阅材料，不代表已做用户盲测。每对共用历史、模型和参数，模型仍有随机性。', '']
    key = {}
    for name, pair in cases.items():
        if len(pair) != 2:
            continue
        rng.shuffle(pair)
        text.extend(['## ' + name, ''])
        for message in pair[0]['messages'][1:]:
            text.extend([message['role'] + '：' + message['content'], ''])
        key[name] = {}
        for label, item in zip(('A', 'B'), pair):
            text.extend(['### ' + label, '', item['reply'], ''])
            key[name][label] = item['version']
    target = args.report.parent
    (target/'blind-review.md').write_text('\n'.join(text), encoding='utf-8')
    (target/'blind-key.json').write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared anonymous pairs: {len(key)}')


if __name__ == '__main__':
    main()
