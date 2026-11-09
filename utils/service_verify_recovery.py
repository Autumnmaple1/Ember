"""Destructive fault checks ONLY for the named synthetic Compose project.

Never accepts a business container/database name. Restores the control backup
into a fresh ember_test_* database and removes only that database afterwards.
"""
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'ember-service-verify-20261104'
APP, PG = PROJECT + '-ember-1', PROJECT + '-postgres-1'
BASE = 'http://127.0.0.1:8086'
COMPOSE = ['docker', 'compose', '--env-file', 'output/service-verify/service.env',
           '-f', 'compose.service.yml', '-f', 'deploy/compose.verify.yml', '-p', PROJECT]


def run(args, data=None, timeout=180):
    result = subprocess.run(args, input=data, capture_output=True, cwd=ROOT, timeout=timeout)
    if result.returncode:
        # Dump contents, cookies and credentials must never enter failure logs.
        raise RuntimeError(f'Isolated verification command failed ({result.returncode})')
    return result.stdout


def main():
    for container in (APP, PG):
        label = run(['docker', 'inspect', '--format',
                     '{{ index .Config.Labels "com.docker.compose.project" }}', container]).decode().strip()
        if label != PROJECT:
            raise RuntimeError('Refusing a container outside the synthetic verification project')
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    def request(path, data=None, expected=200, timeout=15):
        headers = {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if path != '/auth/login':
                headers['X-Ember-CSRF'] = request('/auth/session')['csrf']
        req = urllib.request.Request(BASE + path, headers=headers,
                                     data=None if data is None else json.dumps(data).encode())
        try:
            response = client.open(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read())
            assert response.status == expected, (path, response.status, expected)
            return payload

    def wait_ready():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                request('/health/ready', timeout=7)
                return
            except (AssertionError, OSError):
                time.sleep(1)
        raise AssertionError('Synthetic service did not recover')

    wait_ready()
    code = (ROOT / 'output/service-verify/private/owner-code.txt').read_text().strip()
    request('/auth/login', {'access_code': code, 'label': '恢复验证'})
    before = request('/history')
    assert before, 'Run the browser conversation check first'
    # Real database outage; always restart ONLY this project's database.
    try:
        run(COMPOSE + ['stop', 'postgres'])
        request('/health/ready', expected=503)
        request('/config', expected=503)
    finally:
        run(COMPOSE + ['start', 'postgres'])
    wait_ready()
    assert [m['message_id'] for m in request('/history')] == [m['message_id'] for m in before]

    def archive(operation, slot):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = request('/api/archive/tasks/current')['task']
            if current and current['status'] == 'running':
                time.sleep(.5)
                continue
            epoch = request('/config')['epoch']
            request('/api/archive/tasks', {'operation': operation, 'slot_name': slot,
                                         'epoch': epoch}, expected=202)
            while time.monotonic() < deadline:
                task = request('/api/archive/tasks/current')['task']
                if task and task['status'] != 'running':
                    assert task['status'] == 'completed', 'Synthetic archive operation failed'
                    return
                time.sleep(.25)
        raise AssertionError('Synthetic archive operation timed out')

    slot = 'service-restore-' + uuid.uuid4().hex[:12]
    old_epoch = request('/config')['epoch']
    archive('create', slot)
    archive('load', slot)
    after = request('/config')
    assert after['epoch'] != old_epoch
    assert request('/auth/session')['authenticated']
    assert [m['message_id'] for m in request('/history')] == [m['message_id'] for m in before]
    assert after['experience']['materials']

    # Copy the newest committed SQL backup through memory, never print its data.
    dump = run(['docker', 'exec', APP, 'python', '-c',
        "from pathlib import Path; import sys; "
        "p=max(Path('service/backups').glob('control_*.sql'),key=lambda p:p.stat().st_mtime); "
        "sys.stdout.buffer.write(p.read_bytes())"])
    sections = re.findall(rb'^COPY ember_service\.(\w+) \(([^)]+)\) FROM stdin;\n(.*?)^\\\.\r?$', dump, re.S | re.M)
    assert {table.decode() for table, _, _ in sections} == {'sessions', 'login_attempts', 'jobs', 'usage'}
    database = 'ember_test_service_restore_' + uuid.uuid4().hex
    assert re.fullmatch(r'ember_test_service_restore_[a-f0-9]{32}', database)
    created = False
    counts = {}
    try:
        run(['docker', 'exec', PG, 'createdb', '-U', 'ember_service', '-T', 'template0', database])
        created = True
        run(['docker', 'exec', '-i', PG, 'psql', '-X', '-U', 'ember_service', '-d', database,
             '-v', 'ON_ERROR_STOP=1'], dump)
        for table, columns, content in sections:
            name = table.decode()
            # Compare every restored COPY row with the committed snapshot.
            sql = f'COPY ember_service.{name} ({columns.decode()}) TO STDOUT'
            restored = run(['docker', 'exec', PG, 'psql', '-X', '-q', '-U', 'ember_service',
                            '-d', database, '-c', sql])
            rows = content.splitlines() if content else []
            assert sorted(restored.splitlines()) == sorted(rows), f'{name}: backup content mismatch'
            counts[name] = len(rows)
        assert counts['sessions'] and counts['jobs'] and counts['usage']
    finally:
        if created:
            run(['docker', 'exec', PG, 'dropdb', '-U', 'ember_service', database])
    result = {'passed': True, 'checks': ['database outage returns 503', 'database reconnection',
              'actual world save/load', 'identity/history/material survive restore',
              'control SQL restored into fresh test DB', 'all control rows match snapshot'],
              'restored_rows': counts}
    (ROOT / 'output/service-verify/recovery-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
