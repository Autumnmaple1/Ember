"""Local-only showcase. No production config, model request or database access."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser

HERE = Path(__file__).resolve().parent


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.version_info < (3, 11):
        raise SystemExit('需要 Python 3.11 或更高版本。')
    parser = argparse.ArgumentParser(description='Ember 中期展示模式')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--runtime', type=Path, default=HERE / 'runtime' / 'showcase')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not (HERE / 'site' / 'index.html').exists():
        raise SystemExit('缺少预构建页面。请使用提交包，或先运行 python utils/build_midterm.py。')
    missing = [name for name in ('fastapi', 'uvicorn', 'websockets', 'dotenv', 'yaml', 'psycopg2', 'pgvector', 'numpy', 'tzdata')
               if importlib.util.find_spec(name) is None]
    if missing:
        environment = HERE / 'runtime' / '.venv'
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        print('首次运行需要安装基础依赖；需要网络。展示时不调用在线模型。', flush=True)
        if not python.exists():
            subprocess.check_call([sys.executable, '-m', 'venv', str(environment)])
        requirements = HERE / 'requirements.lock.txt'
        if not requirements.exists():
            requirements = HERE / 'requirements.txt'
        subprocess.check_call([str(python), '-m', 'pip', 'install', '-r', str(requirements)])
        raise SystemExit(subprocess.call([str(python), str(__file__), *sys.argv[1:]]))
    runtime = args.runtime.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    # Set before any project import. No search for parent .env or business JSON.
    os.environ.update(EMBER_NO_DOTENV='1', START_TIME='', TIME_ACCEL_FACTOR='1', TIMEZONE='Asia/Shanghai')
    os.chdir(runtime)
    sys.path.insert(0, str(HERE.parent))
    from demo.server import create_app
    import uvicorn
    app = create_app(runtime, HERE / 'site')
    url = f'http://127.0.0.1:{args.port}'
    print(f'Ember 中期展示模式：{url}\n预设台词与合成记忆；不连接业务数据库。\n数据目录：{runtime}\n按 Ctrl+C 停止。', flush=True)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
