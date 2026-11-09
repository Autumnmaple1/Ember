"""Reproducible source and showcase packaging; never copies business runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'midterm'


def build_site():
    environment = dict(os.environ, VITE_API_BASE_URL='/')
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not npm:
        raise SystemExit('Build requires Node.js/npm. The submission package includes a prebuilt site.')
    site = ROOT / 'demo' / 'site'
    if site.is_symlink() or site.resolve().parent != (ROOT / 'demo').resolve():
        raise SystemExit('Unsafe generated site path')
    if site.exists():
        shutil.rmtree(site)  # Only this verified, generated frontend directory.
    subprocess.run([npm, 'run', 'build', '--', '--outDir', str(site), '--emptyOutDir'],
        cwd=ROOT / 'frontend', env=environment, check=True)


def package():
    OUT.mkdir(parents=True, exist_ok=True)
    raw = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    paths = sorted(set(raw.decode('utf-8').split('\0')) - {''})
    # An explicit second boundary protects against accidentally tracked runtime.
    forbidden_dirs = {'.git', '.agents', '.codex', '.aws', 'node_modules', '__pycache__',
        '.pytest_cache', 'data', 'archives', 'output', 'test-results', 'dist', 'coverage'}
    allowed_config = {'prompts.yaml', 'state_default.json', '__init__.py', 'settings.py'}
    entries = {}
    for name in paths:
        path = ROOT / name
        parts = Path(name).parts
        if not path.is_file() or any(x in forbidden_dirs for x in parts):
            continue
        if parts[0] == 'config' and path.name not in allowed_config:
            continue
        if path.name.startswith('.env') and path.name != '.env.example':
            continue
        if path.suffix.lower() in {'.log', '.dump', '.ember', '.backup', '.pem', '.pfx', '.pyc'}:
            continue
        entries['source/' + name.replace('\\', '/')] = path
    for path in (ROOT / 'demo' / 'site').rglob('*'):
        if path.is_file():
            entries['source/demo/site/' + path.relative_to(ROOT / 'demo' / 'site').as_posix()] = path
    for name in ('Ember中期策划案.docx', 'Ember中期策划案.pdf', '验收报告.md'):
        path = OUT / name
        if not path.exists():
            raise SystemExit(f'Missing final artifact: {path}')
        entries[name] = path
    for origin, dest in [('01-reviewer-readme.md', '先读我.md'), ('02-demo-script.md', '演示讲稿.md'),
                         ('proposal.md', '策划案内容源.md'), ('03-real-mode.md', '真实模式运行说明.md'),
                         ('04-assets-and-dependencies.md', '素材与依赖说明.md')]:
        entries[dest] = ROOT / 'docs' / 'midterm' / origin
    for path in (ROOT / 'docs' / 'midterm' / 'assets').glob('*.png'):
        entries['assets/' + path.name] = path
    launcher = '@echo off\r\ncd /d "%~dp0source\\demo"\r\ncall start.cmd %*\r\n'
    manifest = {'baseline_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'delivery': 'Ember midterm 2026-11-02 with local submission changes',
        'files': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in entries.items()}}
    manifest['files']['开始展示.cmd'] = hashlib.sha256(launcher.encode()).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    target = OUT / 'Ember中期提交包.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in entries.items():
            archive.write(path, 'Ember-midterm/' + name)
        archive.writestr('Ember-midterm/开始展示.cmd', launcher)
        archive.write(OUT / 'manifest.json', 'Ember-midterm/manifest.json')
    print(json.dumps({'zip': str(target), 'entries': len(entries) + 2, 'bytes': target.stat().st_size}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-only', action='store_true')
    parser.add_argument('--package-only', action='store_true')
    args = parser.parse_args()
    if not args.package_only:
        build_site()
    if not args.site_only:
        package()
