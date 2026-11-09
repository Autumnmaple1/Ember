"""Presentation adapters around production player rules, clock and event protocol.

JSON history, scripted replies, fixture memories and JSON snapshots are intentionally
NOT the production PostgreSQL, LLM, vector recall or .ember archive implementation.
"""
import asyncio
import copy
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from core.event_bus import EventBus
from core.experience import Experience
from core.player_api import install_player_api, presentation
from core.shared_project import INTRO
from core.shared_material import continuation_items
from core.archive_jobs import ArchiveJobs
from core.world_protocol import WorldProtocol
from core.turns import TurnCancelled
from memory.working_memory import WorkingMemory


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@dataclass
class Result:
    success: bool
    message: str
    error: str = ''

    def to_dict(self):
        return asdict(self)


class DemoArchives:
    def __init__(self, server):
        self.server, self.event_bus = server, server.event_bus
        self._in_progress = self.recovery_required = False
        self.callback = None
        self.folder = server.root / 'archives'
        self.folder.mkdir(exist_ok=True)

    def _archive_path(self, name):
        if not re.fullmatch(r'[\w\u4e00-\u9fff .-]{1,80}', name) or '..' in name or name == '.':
            raise HTTPException(400, '存档名称无效')
        return self.folder / (name + '.demo.json')

    def set_progress_callback(self, callback):
        self.callback = callback

    def payload(self, description):
        s = self.server
        return {'format': 'ember-showcase-v1', 'manifest': {'character_name': '依鸣',
            'created_at': self.event_bus.formatted_logical_now, 'logical_time': self.event_bus.formatted_logical_now,
            'description': description, 'extra': {'scene': s.state['scene']},
            'stats': {'message_count': len(s.history), 'memory_count': 1}},
            'history': copy.deepcopy(s.history), 'state': copy.deepcopy(s.state),
            'experience': s.experience.snapshot(), 'working': s.memory.working.snapshot(),
            'factor': self.event_bus.time_accel_factor, 'world_paused': self.event_bus.world_paused}

    def create_archive(self, name, description=''):
        with self.event_bus.operation_lock:
            if self.callback:
                self.callback('写入展示快照', 50)
            write_json(self._archive_path(name), self.payload(description))
        return Result(True, '展示快照已保存（不兼容生产 .ember 存档）')

    def load_archive(self, name):
        with self.event_bus.operation_lock:
            data = json.loads(self._archive_path(name).read_text(encoding='utf-8'))
            if data.get('format') != 'ember-showcase-v1':
                return Result(False, '格式不匹配', '只支持展示快照')
            # Validate first using the real schemas. Keep the previous snapshot.
            candidate = self.folder / '_validate-experience.json'
            write_json(candidate, data['experience'])
            Experience(candidate)
            write_json(self.folder / '_before-load.json', self.payload('读档前自动备份'))
            s = self.server
            s.history, s.state = data['history'], data['state']
            write_json(s.root / 'history.json', s.history)
            write_json(s.experience.path, data['experience'])
            write_json(s.memory.working.path, data['working'])
            s.experience.reload()
            s.memory.working.reload()
            self.event_bus.set_time_accel_factor(data['factor'])
            self.event_bus.restore_logical_time(data['manifest']['logical_time'])
            self.event_bus.world_paused = data['world_paused']
            self.event_bus.quiet = s.experience.snapshot()['quiet']
            s.turn = None
            s.event_bus.turns.current = None
            s.protocol.rotate()
            s.save()
            s.safe_broadcast({'type': 'world.reset'})
        return Result(True, '展示快照已恢复；活动等待确认继续')

    def quick_save(self):
        return self.create_archive('quick_save', '展示模式快速存档')

    def quick_load(self):
        return self.load_archive('quick_save')


class Showcase:
    def __init__(self, root):
        self.root = root
        self.app = FastAPI(title='Ember 中期展示模式')
        self.protocol, self.event_bus = WorldProtocol(), EventBus()
        self.experience = Experience(root / 'experience.json')
        self.event_bus.quiet = self.experience.snapshot()['quiet']
        self.memory = SimpleNamespace(working=WorkingMemory(root / 'working_memory.json'))
        self.current_ai_msg_id = self.current_ai_turn_id = None
        self.current_full_text = ''
        self.turn = None
        self.sockets = set()
        self.loop = None
        self.state = {'scene': {'character_location': '图书馆自习室', 'user_location': '图书馆自习室',
            'communication_mode': 'face_to_face', 'remote_contact_confirmed': False},
            '客观情境': '合成初见场景：正在整理一份虚构的校园观察手册。', 'scene_status': 'ready',
            '近期综合轨迹': '准备试做观察手册', 'P': 6, 'A': 4, 'D': 5}
        self.history = []
        stored = root / 'history.json'
        if stored.exists():
            self.history = json.loads(stored.read_text(encoding='utf-8'))
        else:
            self.append('user', '【合成样例】我喜欢桂花茶。要不要一起试做校园观察手册？')
            self.append('ai', '【预设台词】好呀，我们用虚构资料试做《窗边的一页》。先选一份材料，再决定如何处理不确定的信息。')
        if (root / 'world.json').exists():
            self.state = json.loads((root / 'world.json').read_text(encoding='utf-8'))['state']
        self.db_memory = SimpleNamespace(get_history=lambda limit: self.query(limit=limit))
        self.archive_manager = DemoArchives(self)
        self.archive_jobs = ArchiveJobs(self.archive_manager, self.safe_broadcast, lambda: None, str(root / 'archive-job.json'))
        install_player_api(self, history_reader=self.query, memory_reader=self.fixture_memories)

    def append(self, role, content, key=None, turn_id=None, status='completed'):
        existing = next((x for x in self.history if x['message_id'] == key), None) if key else None
        if existing:
            return existing
        row = {'id': max((x['id'] for x in self.history), default=0) + 1, 'message_id': key or str(uuid.uuid4()),
            'role': role, 'content': content, 'timestamp': int(self.event_bus.logical_now * 1000),
            'completion_status': status, 'turn_id': turn_id}
        self.history.append(row)
        self.save()
        self.memory.working.capture([row])
        return row

    def save(self):
        write_json(self.root / 'history.json', self.history)
        write_json(self.root / 'world.json', {'state': self.state})

    def query(self, q='', day='', limit=50, before_id=None, message_id=None):
        if day:
            from datetime import date
            date.fromisoformat(day)
        rows = [x for x in self.history if q in x['content']
            and (before_id is None or x['id'] < before_id)
            and (not day or self.event_bus.format_logical_time(x['timestamp'] / 1000, '%Y-%m-%d') == day)]
        return copy.deepcopy(rows[-limit:][::-1])

    @staticmethod
    def fixture_memories():
        return [{'id': 1, 'content': '【合成记忆样例】用户在展示样例中说喜欢桂花茶。',
            'insight': '仅用于展示记忆阅读界面；没有执行模型编码或向量检索。',
            'time': '合成样例 时间未知', 'metadata': {'time_basis': 'unknown'}, 'provenance': 'demo_fixture'}]

    def config_snapshot(self):
        experience = self.experience.snapshot()
        return {'display_name': '依鸣', 'demo_mode': True, 'timezone': 'Asia/Shanghai',
            'logical_time': self.event_bus.formatted_logical_now, 'time_accel_factor': self.event_bus.time_accel_factor,
            'world_paused': self.event_bus.world_paused, 'state': self.state,
            'presentation': presentation(self.state, self.event_bus.formatted_logical_now),
            'experience': experience, 'shared_project': INTRO,
            'continuation': continuation_items(experience),
            'archive_task': self.archive_jobs.snapshot(), 'turn': self.event_bus.turns.snapshot(),
            'is_thinking': self.turn is not None, **self.protocol.snapshot(),
            'capabilities': {'world_epoch': True, 'player_records': True, 'shared_projects': True,
                'archive_tasks': True, 'message_receipts': True, 'correlated_audio': False, 'cancel_turn': True, 'shared_materials': True},
            'status': {'database': {'ready': True, 'adapter': 'demo_json'},
                'long_term_memory': {'ready': True, 'adapter': 'demo_fixture', 'warning': '展示样例，未连接向量数据库'},
                'working_memory': self.memory.working.status(), 'memory_pipeline': {'pending_batches': 0, 'pending_memories': 0},
                'scene': {'ready': True, 'state': 'ready'}, 'paused': self.archive_jobs.busy,
                'configuration_warnings': ['展示模式不生成语音，不执行真实大模型和长期记忆编码。']}}

    async def emit(self, event):
        message = self.protocol.envelope(event)
        for socket in list(self.sockets):
            try:
                await socket.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                self.sockets.discard(socket)

    def safe_broadcast(self, event):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.emit(event), self.loop)

    def reply(self, text):
        if any(x in text for x in ('手册', '一起', '自习')):
            return '【预设台词】可以呀。一起试做《窗边的一页》吧；用哪份材料、保留哪些疑问，都由你决定。自习也可以在专注陪伴里单独计时。'
        if any(x in text for x in ('记得', '喜欢', '茶', '回忆')):
            return '【预设台词】展示样例写着你喜欢桂花茶。这句话来自合成资料；真实模式才会根据对话记录和记忆检索来回答。'
        if any(x in text for x in ('回家', '离开', '分开')):
            self.state['scene'].update(user_location='已离开图书馆 具体地点待确认', communication_mode='unknown')
            return '【预设台词】好，我还在图书馆。你去了哪里、之后用什么方式联系，现在还没有确认。手册会留在这里，等你决定是否继续。'
        if any(x in text for x in ('几点', '时间', '今天')):
            return f'【预设台词】后端世界时钟现在是 {self.event_bus.formatted_logical_now}，时区是上海。旧样例中的相对时间不能直接当作现在。'
        return '【预设台词】我在整理这份虚构的观察资料。你可以邀请我一起做手册、问当前时间，或者在回忆页查看保存的对话。本模式不理解任意输入。'

    async def respond(self, content, key):
        # The same lock as player actions/archives; this is a short scripted stream.
        lock = self.event_bus.operation_lock
        if not lock.acquire(blocking=False):
            self.turn = None
            await self.emit({'type': 'error', 'client_request_id': key, 'content': '当前任务尚未完成'})
            return
        try:
            row = self.append('user', content, key, key)
            turn = self.event_bus.turns.begin(row['timestamp'], key)
            self.turn = turn
            self.current_ai_msg_id, self.current_ai_turn_id = turn.message_id, key
            self.current_full_text = ''
            await self.emit({**row, 'type': 'message', 'sender': 'user', 'client_request_id': key})
            await self.emit({'type': 'message.persisted', 'message_id': key})
            await self.emit({'type': 'turn.started', **turn.snapshot()})
            answer = self.reply(content)
            self.save()
            await self.emit({'type': 'state_update', 'state': self.state})
            base = {'type': 'message', 'sender': 'ai', 'message_id': turn.message_id,
                'turn_id': key, 'timestamp': row['timestamp']}
            turn.set_phase('generating')
            await self.emit({**base, 'mode': 'start', 'content': ''})
            status = 'completed'
            try:
                for index in range(0, len(answer), 5):
                    turn.check()
                    fragment = answer[index:index + 5]
                    self.current_full_text += fragment
                    await self.emit({**base, 'mode': 'append', 'content': fragment})
                    await asyncio.sleep(0.045)
            except TurnCancelled:
                status = 'interrupted'
            self.append('ai', self.current_full_text, turn.message_id, key, status)
            await self.emit({**base, 'mode': 'replace', 'content': self.current_full_text, 'completion_status': status})
            await self.emit({'type': 'message.persisted', 'message_id': turn.message_id})
            turn.finish(status)
            turn.release()
            await self.emit({'type': 'turn.finished', **turn.snapshot()})
        finally:
            self.turn = None
            lock.release()


def create_app(root, site):
    s = Showcase(Path(root))
    app = s.app
    app.state.showcase = s

    @app.get('/config')
    async def config():
        return s.config_snapshot()

    @app.post('/config/time_accel')
    async def factor(body: dict):
        if s.turn or s.archive_jobs.busy or not s.event_bus.set_time_accel_factor(float(body.get('factor', 0))):
            raise HTTPException(409, '请等待本轮结束，并使用有效的正数倍率')
        return {'success': True, 'factor': s.event_bus.time_accel_factor}

    @app.get('/history')
    async def history(limit: int = 20, before_id: int | None = None):
        return s.query(limit=max(1, min(limit, 100)), before_id=before_id)

    @app.get('/api/archive/list')
    async def archives():
        rows = []
        for path in sorted(s.archive_manager.folder.glob('*.demo.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            rows.append({**data['manifest'], 'slot_name': path.name.removesuffix('.demo.json'),
                'file_size': path.stat().st_size, 'is_valid': True,
                'is_quick_save': path.name == 'quick_save.demo.json'})
        return {'success': True, 'archives': rows}

    @app.get('/api/archive/{slot}/preview')
    async def preview(slot: str):
        path = s.archive_manager._archive_path(slot)
        if not path.exists():
            raise HTTPException(404, '没有这个展示快照')
        return {'success': True, 'manifest': json.loads(path.read_text(encoding='utf-8'))['manifest']}

    @app.delete('/api/archive/{slot}')
    async def delete(slot: str):
        if s.archive_jobs.busy:
            raise HTTPException(409, '请等待存读档结束')
        path = s.archive_manager._archive_path(slot)
        if not path.exists():
            raise HTTPException(404, '没有这个展示快照')
        path.unlink()
        return {'success': True}

    @app.websocket('/ws/chat')
    async def chat(socket: WebSocket):
        await socket.accept()
        s.loop = asyncio.get_running_loop()
        s.sockets.add(socket)
        await socket.send_json({'type': 'world.snapshot', **s.protocol.snapshot()})
        try:
            while True:
                data = await socket.receive_json()
                if data.get('type') == 'cancel_turn':
                    snapshot = s.event_bus.turns.cancel(data.get('turn_id'))
                    if snapshot:
                        await s.emit({'type': 'turn.stopping', **snapshot})
                elif data.get('type') == 'tts_request':
                    await s.emit({'type': 'tts_error', 'message_id': data.get('message_id'),
                        'request_id': data.get('request_id'), 'content': '展示模式不生成语音，请在真实模式验证。'})
                elif data.get('content'):
                    key = data.get('client_request_id') or str(uuid.uuid4())
                    previous = next((x for x in s.history if x['message_id'] == key), None)
                    if data.get('epoch') != s.protocol.epoch or s.turn or s.archive_jobs.busy or s.event_bus.world_paused:
                        await s.emit({'type': 'error', 'client_request_id': key, 'content': '请等待当前任务完成或恢复世界后再发送'})
                    elif previous:
                        await s.emit({**previous, 'type': 'message', 'sender': 'user', 'client_request_id': key,
                            'replayed': True, 'turn_snapshot': s.event_bus.turns.snapshot()})
                    else:
                        # Reserve before scheduling, so two clients cannot accept concurrently.
                        s.turn = True
                        asyncio.create_task(s.respond(str(data['content'])[:10000], key))
        except WebSocketDisconnect:
            pass
        finally:
            s.sockets.discard(socket)

    app.mount('/', StaticFiles(directory=site, html=True), name='showcase')
    return app
