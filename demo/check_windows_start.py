"""Windows bootstrap regression checks; no model/database or business files."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(environment):
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(ROOT / 'demo/start.ps1'), '--check-python'], env=environment,
        capture_output=True, encoding='utf-8', errors='replace', timeout=30)


def main():
    if os.name != 'nt':
        raise SystemExit('These checks require Windows.')
    environment = dict(os.environ)
    environment.pop('EMBER_PYTHON', None)
    result = run(environment)
    assert result.returncode == 0 and 'Using Python' in result.stdout, result.stdout + result.stderr
    print('PASS automatic discovery without EMBER_PYTHON')
    environment['EMBER_PYTHON'] = str(ROOT / 'output/missing-python.exe')
    result = run(environment)
    assert result.returncode == 1 and 'EMBER_PYTHON is invalid' in result.stdout, result.stdout + result.stderr
    print('PASS invalid explicit override has an actionable failure')
    qa = ROOT / 'output/midterm/qa'
    qa.mkdir(parents=True, exist_ok=True)
    # This generated temporary directory is confined to the named QA workspace.
    with tempfile.TemporaryDirectory(prefix='bootstrap-', dir=qa) as folder:
        folder = Path(folder)
        virtualenv = folder / '\u4e2d\u6587 space' / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(virtualenv)], check=True)
        environment['EMBER_PYTHON'] = str(virtualenv / 'Scripts/python.exe')
        result = run(environment)
        assert result.returncode == 0 and 'Using Python' in result.stdout, result.stdout + result.stderr
        print('PASS interpreter path containing Chinese and spaces')
        environment.pop('EMBER_PYTHON')
        fakebin = folder / 'fake-bin'
        fakebin.mkdir()
        marker = folder / 'bad-launcher-was-used.txt'
        (fakebin / 'py.cmd').write_text(f'@echo off\r\necho broken>"{marker}"\r\nexit /b 91\r\n', encoding='utf-8')
        environment.update(PATH=str(fakebin) + os.pathsep + environment['PATH'],
            CONDA_PREFIX=str(virtualenv), PY_PYTHON='3.13t')
        result = run(environment)
        assert result.returncode == 0 and not marker.exists(), result.stdout + result.stderr
        print('PASS broken py default is bypassed using a validated Conda interpreter')


if __name__ == '__main__':
    main()
