"""Integration checks: temp files, local TestClient, no database or model."""
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    qa = ROOT / 'output' / 'midterm' / 'qa'
    qa.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='check-', dir=qa))
        stack.callback(os.chdir, ROOT)
        os.chdir(folder)
        os.environ.update(EMBER_NO_DOTENV='1', START_TIME='', TIME_ACCEL_FACTOR='1', TIMEZONE='Asia/Shanghai')
        from fastapi.testclient import TestClient
        from demo.server import create_app
        app = create_app(Path(folder), ROOT / 'demo' / 'site')
        with TestClient(app) as client:
            config = client.get('/config').json()
            assert config['demo_mode'] and len(client.get('/history').json()) == 2
            epoch = config['epoch']
            def action(action, **values):
                response = client.post('/experience', json={'action': action, 'epoch': epoch, **values})
                assert response.status_code == 200, response.text
                return response.json()['experience']
            source = client.get('/history').json()[0]['message_id']
            print('Checking six project paths...')
            for route in ('window', 'archive'):
                for choice in ('verify', 'broaden', 'leave_open'):
                    request = {'message_id': source, 'request_id': str(uuid.uuid4())}
                    project = action('project_start', **request)['projects'][-1]
                    duplicate = action('project_start', **request)['projects'][-1]
                    assert duplicate['id'] == project['id']
                    for decision in (route, choice):
                        project = action('project_choose', record_id=project['id'], expected_version=project['version'],
                            choice=decision, request_id=str(uuid.uuid4()))['projects'][-1]
                    project = action('project_finish', record_id=project['id'], expected_version=project['version'],
                        title='合成成果', note='只在独立临时目录保存', request_id=str(uuid.uuid4()))['projects'][-1]
                    assert project['status'] == 'completed'
            action('favorite', message_id=source)
            action('agreement', message_id=source, title='合成待办 日期未知')
            action('start', duration=1)
            action('pause')
            action('interrupt', summary='合成自习回顾')
            print('Checking websocket...')
            with client.websocket_connect('/ws/chat') as ws:
                assert ws.receive_json()['type'] == 'world.snapshot'
                key = str(uuid.uuid4())
                ws.send_json({'content': '现在几点了？', 'client_request_id': key, 'epoch': epoch})
                events = []
                while not events or events[-1]['type'] != 'turn.finished':
                    events.append(ws.receive_json())
                    print('event:', events[-1]['type'])
                assert sum(x['type'] == 'message.persisted' for x in events) == 2
                assert any('预设台词' in x.get('content', '') for x in events)
            before = len(client.get('/history').json())
            print('Checking archive...')
            response = client.post('/api/archive/tasks', json={'operation': 'create', 'slot_name': 'synthetic', 'epoch': epoch})
            assert response.status_code == 202
            for _ in range(200):
                task = client.get('/api/archive/tasks/current').json()['task']
                if task['status'] != 'running':
                    break
                time.sleep(0.01)
            assert task['status'] == 'completed', task
            app.state.showcase.append('user', '保存后的合成变化')
            response = client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'synthetic', 'epoch': epoch})
            assert response.status_code == 202
            for _ in range(200):
                task = client.get('/api/archive/tasks/current').json()['task']
                if task['status'] != 'running':
                    break
                time.sleep(0.01)
            assert task['status'] == 'completed', task
            assert len(client.get('/history').json()) == before
            assert client.get('/config').json()['epoch'] != epoch
            assert client.post('/experience', json={'action': 'quiet', 'epoch': epoch}).status_code == 409
            assert client.get('/history/search?day=invalid').status_code == 400
            assert client.get('/experience/memories').json()['items'][0]['provenance'] == 'demo_fixture'
            assert client.get('/api/archive/missing/preview').status_code == 404
        # A fresh process adapter can recover the records without production state.
        app2 = create_app(Path(folder), ROOT / 'demo' / 'site')
        assert len(app2.state.showcase.history) == before
        assert len(app2.state.showcase.experience.snapshot()['projects']) == 6
    print('PASS: 6 project paths, idempotence, player records, streaming receipts, archive restore, epoch isolation, errors and restart')


if __name__ == '__main__':
    main()
