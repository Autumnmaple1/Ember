"""Prepare a separate synthetic-only Compose environment, no business secrets."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'output/service-verify'
target = folder / 'service.env'
if not target.exists():
    subprocess.run([sys.executable, str(ROOT / 'utils/service_setup.py'), '--directory', str(folder),
        '--from-env', str(folder / 'absent.env')], check=True)
values = {'EMBER_SERVICE_IMAGE': 'ember-service:dev', 'EMBER_SERVICE_ENV': './output/service-verify/service.env',
    'EMBER_SERVICE_PORT': '8086', 'EMBER_PUBLIC_URL': 'http://127.0.0.1:8086',
    'HEARTBEAT_INTERVAL': '3600', 'EMBER_BACKUP_INTERVAL': '60', 'EMBER_DAILY_TTS_LIMIT': '0'}
for prefix, model in [('LARGE_LLM','fixture-large'), ('SMALL_LLM','fixture-small'), ('EMBEDDING','fixture-embedding')]:
    values.update({prefix+'_API_KEY': 'synthetic-test-key', prefix+'_BASE_URL': 'http://fixture:9000/v1', prefix+'_MODEL': model})
lines = [line for line in target.read_text().splitlines() if line.split('=',1)[0] not in values]
target.write_text('\n'.join(lines + [key+'='+value for key,value in values.items()])+'\n')
print('Synthetic Compose configuration ready; no model service will be contacted.')
