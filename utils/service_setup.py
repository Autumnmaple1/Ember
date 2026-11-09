"""Create private hosted configuration without changing the local .env/world."""
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=ROOT / 'deploy')
    parser.add_argument('--from-env', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from service.auth import password_hash
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'service.env'
    code_file = folder / 'private/owner-code.txt'
    if target.exists() or code_file.exists():
        raise SystemExit('已有服务配置或访问码；为保护原配置，本命令不会覆盖。')
    values = dotenv_values(args.from_env) if args.from_env.exists() else {}
    code = secrets.token_urlsafe(24)
    config = {key: value for key,value in values.items() if value is not None and
        (key.startswith(('LARGE_LLM_', 'SMALL_LLM_', 'EMBEDDING_')) or key in ('CHARACTER_NAME','TIMEZONE'))}
    config.update(PG_USER='ember_service', PG_DB='ember_service', PG_PASSWORD=secrets.token_urlsafe(24),
        EMBER_AUTH_REQUIRED='true', EMBER_ACCESS_CODE_HASH=password_hash(code), EMBER_SERVICE_PORT='8080',
        EMBER_BIND_HOST='127.0.0.1', EMBER_PUBLIC_URL='http://localhost:8080', EMBER_COOKIE_SECURE='false',
        EMBER_REQUEST_TIMEOUT='20', EMBER_DAILY_CHAT_LIMIT='200', EMBER_DAILY_EMBEDDING_LIMIT='200',
        EMBER_DAILY_TTS_LIMIT='200', START_TIME='', TIME_ACCEL_FACTOR='1', TIMEZONE=config.get('TIMEZONE','Asia/Shanghai'))
    code_file.parent.mkdir(exist_ok=True)
    def quoted(value):
        if '\n' in value or '\r' in value or "'" in value:
            raise ValueError('配置值含不支持的换行或单引号，请手动配置服务文件')
        return "'" + value + "'"
    text = '\n'.join(key+'='+quoted(str(value)) for key,value in config.items())+'\n'
    code_file.write_text(code+'\n', encoding='utf-8')
    target.write_text(text, encoding='utf-8')
    if os.name != 'nt':
        code_file.chmod(0o600)
        target.chmod(0o600)
    print(f'已创建服务配置：{target}\n设备登录访问码：{code_file}\n原项目配置与数据不变。')


if __name__ == '__main__':
    main()
