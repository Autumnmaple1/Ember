"""Hosted entry point: initialize an isolated workspace before importing settings."""
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if int(os.getenv('WEB_CONCURRENCY', '1')) != 1:
        raise SystemExit('Ember 单世界服务只支持一个 worker')
    os.environ.update(EMBER_AUTH_REQUIRED='true', EMBER_NO_DOTENV='1')
    runtime = Path(os.getenv('EMBER_RUNTIME_DIR', str(ROOT / 'data/service-runtime'))).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'config').mkdir(exist_ok=True)
    for name in ('prompts.yaml', 'state_default.json'):
        target = runtime / 'config' / name
        if not target.exists():
            shutil.copy2(ROOT / 'config' / name, target)
    os.chdir(runtime)
    sys.path.insert(0, str(ROOT))
    from service.auth import AuthConfig
    from service.store import ServiceStore
    from service.runtime import ServiceRuntime, WorldLock
    from core.service_policy import install
    auth = AuthConfig.environment()
    lock = WorldLock(runtime / 'service/world.lock')
    try:
        limits = {kind: max(0, int(os.getenv(f'EMBER_DAILY_{kind.upper()}_LIMIT', '200'))) for kind in ('chat','embedding','tts')}
        store = ServiceStore(limits=limits)
        store.initialize()
        install(store)
        from server import EmberServer
        server = EmberServer(service_runtime=ServiceRuntime(store, auth))
        server.start()
    finally:
        install(None)
        lock.close()


if __name__ == '__main__':
    main()
