"""Player-controlled records, source navigation, settings and archive jobs."""
import asyncio
import copy
import re
import json
import uuid
from datetime import date
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from memory.postgres import connection
from core.time_utils import to_epoch
from core.shared_project import ProjectConflict
from memory.journal import append_event_once


def _row(row):
    return {'id': row[0], 'timestamp': int(to_epoch(row[1]) * 1000),
        'role': 'ai' if row[2] == 'assistant' else 'user', 'content': row[3] or '',
        'message_id': row[4] or f'legacy-{row[0]}', 'completion_status': row[5], 'turn_id': row[6]}


def history_query(q='', day='', limit=50, before_id=None, message_id=None):
    """Own read transaction, bound parameters and stable source identifiers."""
    columns = 'id,timestamp,sender,text,message_id,completion_status,turn_id'
    with connection() as conn, conn.cursor() as cur:
        if message_id:
            cur.execute(f"SELECT {columns} FROM message_list WHERE COALESCE(message_id,'legacy-'||id::text)=%s", (message_id,))
            row = cur.fetchone()
            if not row:
                return []
            stamp, key = row[1], row[0]
            cur.execute(f'''(SELECT {columns} FROM message_list WHERE (timestamp,id) <= (%s,%s)
                ORDER BY timestamp DESC,id DESC LIMIT 21) UNION ALL
                (SELECT {columns} FROM message_list WHERE (timestamp,id) > (%s,%s)
                ORDER BY timestamp,id LIMIT 20) ORDER BY timestamp,id''', (stamp, key, stamp, key))
        else:
            conditions, values = ['text ILIKE %s'], ['%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%']
            if day:
                conditions.append('timestamp::date=%s')
                values.append(date.fromisoformat(day))
            if before_id:
                conditions.append('id < %s')
                values.append(before_id)
            cur.execute(f"SELECT {columns} FROM message_list WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT %s", (*values, limit))
        return [_row(x) for x in cur.fetchall()]


def source_message(key):
    for message in history_query(message_id=key):
        if message['message_id'] == key:
            return message
    raise ValueError('来源消息不存在，请先同步已保存记录')


def presentation(state, logical_time):
    scene = state.get('scene', {})
    location = scene.get('character_location') or ''
    # Only the structured location is inspected; narrative text never selects scenery.
    background = next((key for key, words in [('library', ('图书馆', '自习室')),
        ('dorm', ('宿舍',)), ('campus', ('校园', '校门', '校园室外'))]
        if any(word in location for word in words)), 'neutral')
    hour = int(logical_time[11:13])
    period = 'day' if 6 <= hour < 17 else 'dusk' if 17 <= hour < 19 else 'night'
    p, a = state.get('P', 5), state.get('A', 5)
    emotion = 'happy' if p >= 7 else 'sad' if p <= 3 else 'questioning' if a >= 7 else 'normal'
    return {'background': background, 'period': period, 'emotion': emotion,
        'actions': ['greet', 'ask', 'invite_study'], 'contact_actions': [],
        'capabilities': {'hit_areas': False, 'motions': False, 'expressions': ['normal', 'happy', 'sad', 'angry', 'questioning']}}


class PlayerAction(BaseModel):
    action: str = Field(max_length=30)
    epoch: str = Field(max_length=80)
    message_id: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    title: str = Field(default='', max_length=300)
    when: str = Field(default='', max_length=100)
    location: str = Field(default='', max_length=150)
    record_id: str = Field(default='', max_length=100)
    status: str = Field(default='', max_length=30)
    duration: int = Field(default=25, ge=1, le=240)
    shared: bool = False
    summary: str = Field(default='', max_length=2000)
    request_id: str = Field(default='', max_length=80)
    expected_version: int | None = Field(default=None, ge=0)
    choice: str = Field(default='', max_length=40)
    note: str = Field(default='', max_length=1000)


class ArchiveTaskRequest(BaseModel):
    operation: str
    slot_name: str = Field(default='', max_length=80)
    description: str = Field(default='', max_length=300)
    epoch: str


def install_player_api(server):
    app = server.app

    def readable(epoch=None):
        if server.event_bus.paused or server.archive_jobs.busy or (epoch and epoch != server.protocol.epoch):
            raise HTTPException(409, '存读档或世界切换中，请等待完成后重试')

    def check_epoch(epoch):
        if epoch != server.protocol.epoch:
            raise HTTPException(409, '世界版本已变化，请同步后重试')

    @app.get('/sync')
    async def sync(epoch: str = '', after: int = Query(0, ge=0)):
        with server.protocol.lock:
            config = server.config_snapshot()
            if config.get('status', {}).get('paused'):
                raise HTTPException(409, '存读档进行中，请等待任务完成')
            boundary = server.protocol.snapshot()
            live = {'message_id': server.current_ai_msg_id, 'turn_id': server.current_ai_turn_id,
                'content': server.current_full_text, 'timestamp': (config.get('turn') or {}).get('timestamp')}
        try:
            history = await asyncio.to_thread(server.db_memory.get_history, 100)
        except Exception as exc:
            raise HTTPException(503, '历史同步失败，请重试') from exc
        readable(boundary['epoch'])
        if server.protocol.epoch != boundary['epoch']:
            raise HTTPException(409, '读档已改变世界，请重新同步')
        return {'config': config, 'history': history, 'boundary': boundary, 'live_reply': live,
            'tail': server.protocol.tail(boundary['epoch'], boundary['event_seq'])}

    @app.get('/history/search')
    async def search(q: str = Query('', max_length=200), day: str = '', before_id: int | None = None):
        epoch = server.protocol.epoch
        readable(epoch)
        try:
            result = await asyncio.to_thread(history_query, q, day, 50, before_id)
            readable(epoch)
            if epoch != server.protocol.epoch:
                raise HTTPException(409, '世界版本已变化，请重试')
            return {'items': result, 'epoch': epoch, 'has_more': len(result) == 50}
        except ValueError as exc:
            raise HTTPException(400, '日期格式应为 YYYY-MM-DD') from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, '历史搜索暂不可用') from exc

    @app.get('/history/source/{message_id}')
    async def source(message_id: str):
        epoch = server.protocol.epoch
        readable(epoch)
        try:
            items = await asyncio.to_thread(history_query, message_id=message_id)
            readable(epoch)
            if epoch != server.protocol.epoch:
                raise HTTPException(409, '世界版本已变化，请重试')
            return {'items': items, 'epoch': epoch}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, '原始对话暂不可用') from exc

    @app.get('/experience/memories')
    async def memories():
        def read():
            with connection() as conn, conn.cursor() as cur:
                cur.execute('SELECT id,content,insight,time,metadata FROM episodic_memory ORDER BY time DESC,id DESC LIMIT 50')
                return [{'id': x[0], 'content': x[1], 'insight': x[2], 'time': str(x[3]),
                    'metadata': x[4], 'provenance': 'model_memory'} for x in cur.fetchall()]
        try:
            epoch = server.protocol.epoch
            readable(epoch)
            result = await asyncio.to_thread(read)
            readable(epoch)
            if epoch != server.protocol.epoch:
                raise HTTPException(409, '世界版本已变化，请重试')
            return {'items': result, 'epoch': epoch}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, '角色记忆读取失败，请重试') from exc

    @app.post('/experience')
    async def player_action(request: PlayerAction):
        def mutate():
            check_epoch(request.epoch)
            if server.archive_jobs.busy or server.event_bus.paused:
                raise HTTPException(409, '存读档期间请等待')
            if not server.event_bus.operation_lock.acquire(blocking=False):
                raise HTTPException(409, '当前任务正在更新状态，请稍后重试')
            try:
                check_epoch(request.epoch)
                experience = server.experience
                source = source_message(request.message_id) if request.message_id else None
                if request.action == 'favorite':
                    if not source:
                        raise ValueError('收藏需要已保存的原始消息')
                    result = experience.favorite(source, request.enabled)
                elif request.action == 'agreement':
                    if not source:
                        raise ValueError('约定需要原始对话来源')
                    result = experience.agreement(request.title, source, request.when, request.location)
                elif request.action == 'agreement_status':
                    result = experience.update_agreement(request.record_id, request.status)
                elif request.action == 'quiet':
                    result = experience.set_quiet(request.enabled)
                    server.event_bus.quiet = request.enabled
                elif request.action == 'world_pause':
                    with server.event_bus.pending_condition:
                        if server.event_bus.pending_dialogues:
                            raise ValueError('请等待已接收的对话完成后暂停世界')
                        server.event_bus.pause_world(request.enabled)
                    result = experience.snapshot()
                elif request.action.startswith('project_'):
                    if server.event_bus.world_paused:
                        raise ProjectConflict('世界已暂停，请在设置中继续后再操作手册')
                    result = experience.project(request.action.removeprefix('project_'), request_id=request.request_id,
                        record_id=request.record_id, expected_version=request.expected_version, source=source,
                        choice=request.choice, title=request.title, note=request.note,
                        logical_time=server.event_bus.formatted_logical_now)
                else:
                    if request.shared and (not source or source['role'] != 'ai' or source['completion_status'] != 'completed'):
                        raise ValueError('共同自习需要一条已完成的角色回复作为来源')
                    result = experience.activity(request.action, duration=request.duration, source=source,
                        shared=request.shared, summary=request.summary, logical_time=server.event_bus.formatted_logical_now)
                server.safe_broadcast({'type': 'experience.update', 'experience': result})
                if request.action in ('complete', 'interrupt', 'project_finish', 'project_edit', 'project_abandon') and hasattr(server, 'memory') and hasattr(server.memory, 'working'):
                    records = result['projects'] if request.action.startswith('project_') else result['activities']
                    record = next((x for x in records if x['id'] == request.record_id), None) if request.record_id else records[-1]
                    if record:
                        content = json.dumps({'action': request.action, 'record': record}, ensure_ascii=False, sort_keys=True)
                        key = str(uuid.uuid5(uuid.NAMESPACE_URL, content))
                        if not any(x['message_id'] == key for x in server.memory.working.snapshot()['sources']):
                            row = {'message_id': key, 'role': 'system_event', 'content': content,
                                'timestamp': int(server.event_bus.logical_now * 1000), 'completion_status': 'completed',
                                'source_type': 'player_activity_record'}
                            append_event_once(row)
                            server.memory.working.capture([row])
                return {'experience': result, 'world_paused': server.event_bus.world_paused, **server.protocol.snapshot()}
            except ProjectConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            finally:
                server.event_bus.operation_lock.release()
        return await asyncio.to_thread(mutate)

    @app.post('/api/archive/tasks', status_code=202)
    async def start_task(request: ArchiveTaskRequest):
        check_epoch(request.epoch)
        if request.slot_name and (not re.fullmatch(r'[\w\u4e00-\u9fff .-]{1,80}', request.slot_name)
                or '..' in request.slot_name or request.slot_name == '.'):
            raise HTTPException(400, '存档名只能包含文字、数字、空格、下划线和短横线')
        if request.operation in ('create', 'load') and not request.slot_name.strip():
            raise HTTPException(400, '请选择存档名称')
        if request.operation == 'create':
            if request.slot_name == 'quick_save' or request.slot_name.startswith('auto_backup'):
                raise HTTPException(400, '该名称为快速存档或自动备份保留，请使用其他名称')
            if server.archive_manager._archive_path(request.slot_name).exists():
                raise HTTPException(409, '同名存档已存在，请使用新名称')
        try:
            return await asyncio.to_thread(server.archive_jobs.start, request.operation, request.slot_name, request.description)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/archive/tasks/current')
    async def current_task():
        return {'task': server.archive_jobs.snapshot()}
