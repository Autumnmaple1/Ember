import json
import asyncio
import time
import logging
import os
import uuid
import re
from collections import OrderedDict
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from core.event_bus import EventBus, Event
from core.heartbeat import Heartbeat
from persona.state_manager import StateManager
from brain.core import Brain
from brain.tts import TTSManager, TTSGenerationError
from brain.tag_utils import extract_thought_and_speech
from memory.short_term import ShortTermMemory
from config.settings import settings
from memory.episodic_memory import EpisodicMemory
from memory.memory_process import Hippocampus
from memory.db_memory import DBMemory
from memory.entity_extraction import EntityExtractionMemory
from config.logging_config import get_logger
from tools.processor import ToolCallProcessor
from archive import ArchiveManager
from core.experience import Experience
from core.world_protocol import WorldProtocol, protocol_serialized
from core.archive_jobs import ArchiveJobs
from core.player_api import install_player_api, presentation
from core.shared_project import INTRO as PROJECT_INTRO

# Configure logging
logger = get_logger(__name__)


class ConnectionManager:
    """Async connection manager"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Pure async broadcast"""
        if getattr(self, 'protocol', None):
            message = self.protocol.envelope(message)
            if message is None:
                return
        if not self.active_connections:
            return

        payload = json.dumps(message, ensure_ascii=False)
        tasks = []
        for connection in self.active_connections:
            tasks.append(self._safe_send(connection, payload))
        await asyncio.gather(*tasks)

    async def _safe_send(self, websocket: WebSocket, payload: str):
        try:
            await websocket.send_text(payload)
        except Exception:
            self.disconnect(websocket)


class EmberServer:
    def __init__(self):
        self.app = FastAPI()
        self.event_bus = EventBus()
        self.manager = ConnectionManager()
        self.protocol = WorldProtocol()
        self.manager.protocol = self.protocol
        self.experience = Experience()
        self.event_bus.experience = self.experience
        self.event_bus.subscribe('system.tick', lambda _: self.experience.checkpoint_if_due() if not self.event_bus.paused else None)
        self.event_bus.quiet = self.experience.data['quiet']
        self.loop = None
        self.current_ai_msg_id = None  # Track ongoing AI message ID
        self.current_ai_turn_id = None
        self.current_full_text = ""  # Track full text for TTS
        self._tts_semaphore = asyncio.Semaphore(3)  # 限制并发 TTS 数量为 3
        self._accepted_inputs = OrderedDict()

        # Initialize components
        self.heartbeat = Heartbeat(self.event_bus, interval=settings.HEARTBEAT_INTERVAL)
        self.memory = ShortTermMemory(
            base_prompt=settings.SYSTEM_PROMPT,
            max_memory_size=settings.CONTEXT_WINDOW_SIZE,
        )
        if not self.memory.working.existed:
            legacy_pending = list(Path('data/failed_memories').glob('*.json'))
            if legacy_pending:
                self.memory.working.adopt_pending(legacy_pending)
        allowed_ids = {m['memory_id'] for j in self.memory.working.snapshot()['jobs'] for m in j['memories']}
        self.episodic_memory = EpisodicMemory(self.event_bus, allowed_pending_ids=allowed_ids)
        self.hippocampus = Hippocampus(self.event_bus, self.memory.working, self.memory)
        self.db_memory = DBMemory(self.event_bus)

        # 创建并复用 ToolCallProcessor
        self.tool_processor = ToolCallProcessor.create_with_memory_tool(
            self.hippocampus
        )

        self.state_manager = StateManager(
            self.event_bus, self.hippocampus, self.memory, self.tool_processor
        )
        self.entity_memory = EntityExtractionMemory(self.event_bus)
        self.brain = Brain(
            self.event_bus,
            self.state_manager,
            self.memory,
            self.hippocampus,
            self.tool_processor,
        )
        self.tts_manager = TTSManager(voice="zh-CN-XiaoxiaoNeural")

        # Initialize archive manager
        self.archive_manager = ArchiveManager(
            event_bus=self.event_bus,
            hippocampus=self.hippocampus,
            heartbeat=self.heartbeat,
            state_manager=self.state_manager,
            short_term_memory=self.memory,
            episodic_memory=self.episodic_memory,
            db_memory=self.db_memory,
        )

        self._setup_middleware()
        self.archive_jobs = ArchiveJobs(self.archive_manager, self.safe_broadcast, self._restore_epoch)
        if self.archive_manager.recovery_required:
            self.event_bus.paused = True
        self._setup_routes()
        self._setup_event_handlers()
        install_player_api(self)

    def _restore_epoch(self):
        self.protocol.rotate()
        try:
            self.experience.reload()
            self.event_bus.quiet = self.experience.data['quiet']
        except Exception:
            self.archive_manager.recovery_required = True
            self.event_bus.paused = True
            raise
        self._accepted_inputs.clear()
        self.event_bus.turns.current = None
        self.current_ai_msg_id = None
        self.safe_broadcast({'type': 'world.reset'})

    def _setup_middleware(self):
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _setup_routes(self):
        @self.app.on_event("startup")
        async def startup_event():
            self.loop = asyncio.get_running_loop()
            logger.info(f">>> [DEBUG] Asyncio loop initialized: {self.loop}")
            if not self.archive_manager.recovery_required:
                await asyncio.to_thread(self.state_manager.prepare_startup)
                self.heartbeat.start()

        @self.app.on_event('shutdown')
        async def shutdown_event():
            self.event_bus.paused = True
            self.experience.checkpoint()
            await asyncio.to_thread(self.heartbeat.stop)
            await asyncio.to_thread(self.db_memory.flush)
            self.db_memory.stop()
            await asyncio.to_thread(self.episodic_memory.stop)

        # Mount audio directory
        audio_dir = "data/audio"
        os.makedirs(audio_dir, exist_ok=True)
        self.app.mount("/audio", StaticFiles(directory=audio_dir), name="audio")

        def config_snapshot():
            return {
                "character_name": "Ember",
                "display_name": settings.CHARACTER_NAME,
                "state": self.state_manager.current_state,
                "logical_time": self.event_bus.formatted_logical_now,
                "is_thinking": self.state_manager.is_thinking or self.brain._is_processing or self.event_bus.pending_dialogues > 0,
                "time_accel_factor": self.event_bus.time_accel_factor,
                "timezone": settings.TIMEZONE,
                "capabilities": {"message_receipts": True, "correlated_audio": True, "cancel_turn": True,
                    'world_epoch': hasattr(self, 'protocol'), 'player_records': hasattr(self, 'experience'),
                    'archive_tasks': hasattr(self, 'archive_jobs'), 'shared_projects': True},
                'shared_project': PROJECT_INTRO,
                **(self.protocol.snapshot() if hasattr(self, 'protocol') else {}),
                'experience': self.experience.snapshot() if hasattr(self, 'experience') else None,
                'archive_task': self.archive_jobs.snapshot() if hasattr(self, 'archive_jobs') else None,
                'world_paused': self.event_bus.world_paused,
                'presentation': presentation(self.state_manager.current_state, self.event_bus.formatted_logical_now),
                "turn": self.event_bus.turns.snapshot(),
                "status": {
                    "database": {"ready": self.db_memory.ready, "pending_writes": len(list(self.db_memory.spool_dir.glob('*.json')))},
                    "long_term_memory": {"ready": self.episodic_memory.ready, 'warning': getattr(self.episodic_memory, 'last_warning', None)},
                    'working_memory': self.memory.working.status() if hasattr(self.memory, 'working') else None,
                    'memory_pipeline': self.hippocampus.status() if hasattr(self, 'hippocampus') else None,
                    "scene": {"ready": self.state_manager.ready, "state": self.state_manager.current_state.get('scene_status'), "error": self.state_manager.last_error},
                    "paused": self.event_bus.paused or (self.archive_jobs.busy if hasattr(self, 'archive_jobs') else False),
                    'recovery_required': getattr(getattr(self, 'archive_manager', None), 'recovery_required', False),
                    "configuration_warnings": settings.CONFIG_WARNINGS,
                },
            }

        self.config_snapshot = config_snapshot

        @self.app.get('/config')
        async def get_config():
            return config_snapshot()

        class TimeAccelRequest(BaseModel):
            factor: float

        @self.app.post("/config/time_accel")
        async def set_time_accel(request: TimeAccelRequest):
            """动态设置时间加速因子"""
            if self.event_bus.paused or self.event_bus.pending_dialogues or (hasattr(self, 'archive_jobs') and self.archive_jobs.busy):
                raise HTTPException(409, '请等待当前任务结束后调整世界时间')
            if request.factor <= 0:
                raise HTTPException(status_code=400, detail="时间加速因子必须大于0")

            success = self.event_bus.set_time_accel_factor(request.factor)
            if success:
                return {
                    "success": True,
                    "time_accel_factor": self.event_bus.time_accel_factor,
                    "logical_time": self.event_bus.formatted_logical_now,
                }
            else:
                raise HTTPException(status_code=500, detail="设置时间加速因子失败")

        @self.app.get("/history")
        async def get_history(limit: int = Query(20, ge=1, le=100), before: int = None, before_id: int = None):
            epoch = self.protocol.epoch if hasattr(self, 'protocol') else None
            if self.event_bus.paused or getattr(self.event_bus, 'archive_active', False):
                raise HTTPException(409, '存读档期间请等待完成后同步历史')
            try:
                result = await asyncio.to_thread(self.db_memory.get_history, limit, before, before_id)
                if self.event_bus.paused or getattr(self.event_bus, 'archive_active', False) or (epoch and epoch != self.protocol.epoch):
                    raise HTTPException(409, '世界恢复中，请完成后重新同步历史')
                return result
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to fetch history: {e}")
                raise HTTPException(status_code=503, detail="聊天记录数据库不可用，请重试或检查后端日志") from e

        # ==================== 存档 API ====================

        class ArchiveCreateRequest(BaseModel):
            slot_name: str
            description: Optional[str] = ""

        class ArchiveLoadRequest(BaseModel):
            slot_name: str

        @self.app.get("/api/archive/list")
        async def list_archives():
            """获取存档列表"""
            try:
                slots = self.archive_manager.list_archives()
                return {
                    "success": True,
                    "archives": [slot.to_dict() for slot in slots],
                }
            except Exception as e:
                logger.error(f"获取存档列表失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/archive/create")
        async def create_archive(request: ArchiveCreateRequest):
            """创建存档"""
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.archive_manager.create_archive,
                    request.slot_name,
                    request.description or "",
                )
                return result.to_dict()
            except Exception as e:
                logger.error(f"创建存档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/archive/load")
        async def load_archive(request: ArchiveLoadRequest):
            """加载存档"""
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.archive_manager.load_archive,
                    request.slot_name,
                )
                return result.to_dict()
            except Exception as e:
                logger.error(f"加载存档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/archive/{slot_name}")
        async def delete_archive(slot_name: str):
            """删除存档"""
            try:
                result = self.archive_manager.delete_archive(slot_name)
                return result.to_dict()
            except Exception as e:
                logger.error(f"删除存档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/archive/{slot_name}/preview")
        async def preview_archive(slot_name: str):
            """预览存档信息"""
            try:
                manifest = self.archive_manager.get_archive_preview(slot_name)
                if manifest:
                    return {"success": True, "manifest": manifest.to_dict()}
                else:
                    raise HTTPException(status_code=404, detail="存档不存在")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"预览存档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/archive/quick-save")
        async def quick_save():
            """快速存档"""
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.archive_manager.quick_save,
                )
                return result.to_dict()
            except Exception as e:
                logger.error(f"快速存档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/archive/quick-load")
        async def quick_load():
            """快速读档"""
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.archive_manager.quick_load,
                )
                return result.to_dict()
            except Exception as e:
                logger.error(f"快速读档失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.websocket("/ws/chat")
        async def websocket_endpoint(websocket: WebSocket):
            await self.manager.connect(websocket)
            async def send_json(payload):
                if hasattr(self, 'protocol'):
                    payload = self.protocol.envelope(payload)
                if payload is not None:
                    await websocket.send_json(payload)
            if websocket.query_params.get('protocol') == '2' and hasattr(self, 'protocol'):
                await send_json({'type': 'world.snapshot', **self.protocol.snapshot()})
            last_ping = time.time()
            ping_interval = 30  # 30秒发送一次心跳

            try:
                while True:
                    # 检查是否需要发送心跳
                    if time.time() - last_ping > ping_interval:
                        try:
                            await websocket.send_text(json.dumps({"type": "ping"}))
                            last_ping = time.time()
                        except Exception:
                            break  # 发送失败，连接已断开

                    # 使用超时接收，避免阻塞
                    try:
                        data = await asyncio.wait_for(
                            websocket.receive_text(), timeout=5.0
                        )
                    except asyncio.TimeoutError:
                        continue  # 超时继续循环，检查心跳

                    message = json.loads(data)
                    msg_type = message.get("type")
                    if message.get('epoch') and hasattr(self, 'protocol') and message['epoch'] != self.protocol.epoch:
                        await send_json({'type': 'world.reset', **self.protocol.snapshot()})
                        continue

                    # 处理心跳 pong
                    if msg_type == "pong":
                        continue
                    if msg_type == 'cancel_turn':
                        snapshot = self.event_bus.turns.cancel(message.get('turn_id'))
                        if snapshot is None:
                            await send_json({'type': 'error', 'action': 'cancel_turn',
                                'content': '本轮已变化，请同步状态后重试', 'turn_id': message.get('turn_id')})
                        elif snapshot['status'] in ('completed', 'interrupted', 'failed'):
                            await send_json({'type': 'turn.finished' if snapshot['released'] else 'turn.stopping', **snapshot})
                        else:
                            await self.manager.broadcast({'type': 'turn.stopping', **snapshot})
                        continue

                    # 严格拦截 TTS 请求，防止进入消息广播逻辑
                    if msg_type == "tts_request":
                        text = message.get("content")
                        if text:
                            logger.info(f"收到手动 TTS 请求: {text[:20]}...")
                            asyncio.create_task(self._process_tts(text, target=websocket,
                                message_id=message.get('message_id'), request_id=message.get('request_id'),
                                epoch=self.protocol.epoch if hasattr(self, 'protocol') else None))
                        continue  # 必须 continue，跳过下方的 user_input 处理

                    user_input = message.get("content")
                    if user_input:
                        request_id = message.get('client_request_id')
                        if request_id:
                            try:
                                if str(uuid.UUID(request_id)) != request_id:
                                    raise ValueError('Expected canonical UUID')
                            except (ValueError, TypeError, AttributeError):
                                await send_json({'type': 'error', 'content': '消息标识无效，请重新编辑后发送', 'client_request_id': request_id})
                                continue
                            previous = self._accepted_inputs.get(request_id)
                            if previous is None and message.get('retry'):
                                try:
                                    previous = await asyncio.to_thread(self.db_memory.get_message, request_id)
                                    previous = self._accepted_inputs.get(request_id) or previous
                                except RuntimeError:
                                    await send_json({'type': 'error', 'content': '暂时无法核对原消息，请恢复数据库后重试', 'client_request_id': request_id})
                                    continue
                            if previous:
                                if previous['content'] != user_input:
                                    await send_json({'type': 'error', 'content': '同一消息标识不能用于不同内容', 'client_request_id': request_id})
                                else:
                                    await send_json({**previous, 'type': 'message', 'sender': 'user',
                                        'client_request_id': request_id, 'replayed': True, 'turn_snapshot': self.event_bus.turns.snapshot()})
                                continue
                        if self.event_bus.paused or self.event_bus.world_paused or not self.state_manager.ready or (hasattr(self, 'archive_jobs') and self.archive_jobs.busy):
                            await send_json({'type': 'error', 'content': '系统正在准备场景或存读档，请稍后重试', 'client_request_id': request_id})
                            continue
                        if message.get('format') == 'audio':
                            await send_json({'type': 'error', 'content': '语音识别尚未启用', 'client_request_id': request_id})
                            continue
                        ts = int(self.event_bus.logical_now * 1000)
                        message_id = request_id or str(uuid.uuid4())
                        receipt = {'content': user_input, 'timestamp': ts, 'id': ts, 'message_id': message_id, 'turn_id': message_id}
                        with self.event_bus.pending_condition:
                            current_turn = self.event_bus.turns.snapshot()
                            turn_released = current_turn is None or current_turn.get('released', False)
                            accepted = not self.event_bus.paused and not self.event_bus.world_paused and not getattr(self.event_bus, 'archive_active', False) and not self.event_bus.pending_dialogues and not self.brain._is_processing and turn_released
                            if accepted:
                                self._accepted_inputs[message_id] = receipt
                                if len(self._accepted_inputs) > 1024:
                                    self._accepted_inputs.popitem(last=False)
                                self.event_bus.publish(Event(name='user.input', data={
                                    'text': user_input, 'message_id': message_id, 'timestamp': ts, 'turn_id': message_id}))
                        if not accepted:
                            await send_json({'type': 'error', 'content': '当前任务尚未完成，请稍后重试', 'client_request_id': request_id})
                            continue
                        await self.manager.broadcast(
                            {
                                "type": "message",
                                "sender": "user",
                                "content": user_input,
                                "timestamp": ts,
                                "id": ts,
                                "message_id": message_id,
                                "client_request_id": request_id,
                                "turn_id": message_id,
                            }
                        )
            except WebSocketDisconnect:
                self.manager.disconnect(websocket)
            except Exception as e:
                logger.error(f"WS loop exception: {e}")
                self.manager.disconnect(websocket)
            finally:
                # 确保连接被移除
                self.manager.disconnect(websocket)

    def _setup_event_handlers(self):
        if hasattr(self, 'protocol'):
            self.event_bus.subscribe('archive.restore.complete', lambda _: self._restore_epoch())
        self.event_bus.subscribe('turn.started', lambda e: self.safe_broadcast({'type': 'turn.started', **e.data}))
        self.event_bus.subscribe('turn.finished', self._on_turn_finished_internal)
        self.event_bus.subscribe('llm.interrupted', self._on_ai_interrupted_internal)
        self.event_bus.subscribe('message.persisted', self._on_message_persisted_internal)
        self.event_bus.subscribe("llm.started", self._on_ai_start_internal)
        self.event_bus.subscribe("llm.chunk", self._on_ai_chunk_internal)
        self.event_bus.subscribe("llm.finished", self._on_ai_finished_internal)
        self.event_bus.subscribe(
            "state.update",
            lambda e: self.safe_broadcast(
                {"type": "state_update", "state": e.data.get("new_state", {})}
            ),
        )

    def _on_message_persisted_internal(self, event):
        previous = self._accepted_inputs.get(event.data['message_id'])
        if previous is not None:
            previous['persisted'] = True
        self.safe_broadcast({'type': 'message.persisted', **event.data})

    @protocol_serialized
    def _on_ai_start_internal(self, event):
        self.current_ai_msg_id = event.data['message_id']
        self.current_ai_turn_id = event.data.get('turn_id')
        self.current_full_text = ""
        self.safe_broadcast(
            {
                "type": "message",
                "sender": "ai",
                "content": "",
                "mode": "start",
                "timestamp": event.data['timestamp'],
                "id": self.current_ai_msg_id,
                "message_id": self.current_ai_msg_id,
                "turn_id": self.current_ai_turn_id,
            }
        )

    @protocol_serialized
    def _on_ai_chunk_internal(self, event):
        if event.data.get('turn_id') and event.data['turn_id'] != self.current_ai_turn_id:
            return
        if self.current_ai_msg_id:
            chunk = event.data.get("text", "")
            if event.data.get('reset'):
                self.current_full_text = chunk
            else:
                self.current_full_text += chunk
            self.safe_broadcast(
                {
                    "type": "message",
                    "sender": "ai",
                    "content": chunk,
                    "mode": "start" if event.data.get('reset') else "append",
                    "id": self.current_ai_msg_id,
                    "message_id": self.current_ai_msg_id,
                    "turn_id": self.current_ai_turn_id,
                }
            )

    @protocol_serialized
    def _on_ai_finished_internal(self, event):
        self.current_full_text = event.data['text']
        self.safe_broadcast({'type': 'message', 'sender': 'ai', 'mode': 'replace',
            'content': self.current_full_text, 'id': event.data['message_id'],
            'message_id': event.data['message_id'], 'timestamp': event.data['timestamp'],
            'turn_id': event.data.get('turn_id'), 'completion_status': 'completed',
            'live2d_emotion': presentation(self.state_manager.current_state, self.event_bus.formatted_logical_now)['emotion']})
        logger.info(
            f"LLM 完成输出，准备合成 TTS... (内容长度: {len(self.current_full_text)})"
        )
        if self.current_full_text and self.current_full_text.strip():
            # 只有开启了某种自动逻辑或当前处于 AI 回复流中才自动合成
            if self.loop:
                final_text = self.current_full_text
                epoch = self.protocol.epoch if hasattr(self, 'protocol') else None
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self._process_tts(final_text, message_id=event.data['message_id'], turn_id=event.data.get('turn_id'), epoch=epoch)
                    )
                )

        if not event.data.get('turn_id'):
            self.safe_broadcast({"type": "llm.done", "message_id": event.data['message_id']})

    def _on_ai_interrupted_internal(self, event):
        self.safe_broadcast({'type': 'message', 'sender': 'ai', 'mode': 'replace',
            'content': event.data['text'], 'id': event.data['message_id'],
            'message_id': event.data['message_id'], 'turn_id': event.data['turn_id'],
            'timestamp': event.data['timestamp'], 'completion_status': 'interrupted'})

    def _on_turn_finished_internal(self, event):
        self.safe_broadcast({'type': 'turn.finished', **event.data})
        self.safe_broadcast({'type': 'llm.done', **event.data})

    async def _process_tts(self, text, *, target=None, message_id=None, request_id=None, turn_id=None, epoch=None):
        """处理 TTS，限制并发数量"""
        epoch = epoch or (self.protocol.epoch if hasattr(self, 'protocol') else None)
        async def report_error(code, content):
            payload = {'type': 'tts_error', 'code': code, 'content': content,
                'request_id': request_id, 'message_id': message_id, 'turn_id': turn_id}
            if hasattr(self, 'protocol'):
                payload = self.protocol.envelope(payload, epoch)
                if payload is None:
                    return
            if target is not None:
                await self.manager._safe_send(target, json.dumps(payload, ensure_ascii=False))
            else:
                await self.manager.broadcast(payload)

        _, text = extract_thought_and_speech(text or '')
        text = re.sub(r'</?response>', '', text, flags=re.IGNORECASE).strip()
        if not text or not text.strip():
            if target is not None:
                await report_error('empty_text', '这条消息没有可朗读的对白')
            return

        # 使用信号量限制并发
        async with self._tts_semaphore:
            try:
                # 限制文本长度，防止超长文本导致性能问题
                max_tts_length = 500
                if len(text) > max_tts_length:
                    text = text[:max_tts_length] + "..."
                    logger.warning(f"TTS 文本过长，已截断至 {max_tts_length} 字符")

                base64_audio = await self.tts_manager.generate_base64(text)
                if not base64_audio:
                    raise TTSGenerationError('no_audio', '语音服务未返回音频，请重试')
                logger.info(f"广播 Base64 TTS 音频 (长度: {len(base64_audio)})")
                payload = {"type": "audio", "audio_base64": base64_audio, 'message_id': message_id, 'request_id': request_id, 'turn_id': turn_id}
                if hasattr(self, 'protocol'):
                    payload = self.protocol.envelope(payload, epoch)
                    if payload is None:
                        return
                if target is not None:
                    await self.manager._safe_send(target, json.dumps(payload, ensure_ascii=False))
                else:
                    await self.manager.broadcast(payload)
            except TTSGenerationError as e:
                logger.warning('TTS 生成失败 [%s]: %s', e.code, e)
                await report_error(e.code, str(e))
            except Exception as e:
                logger.error('TTS 处理失败 (%s)', type(e).__name__)
                await report_error('generation_failed', '语音生成失败，请重试')

    def safe_broadcast(self, message: dict):
        """线程安全的广播方法"""
        if hasattr(self, 'protocol'):
            message = self.protocol.envelope(message)
            if message is None:
                return
        if self.loop and self.loop.is_running():
            try:
                # 使用 call_soon_threadsafe 安排协程创建
                def create_task():
                    try:
                        asyncio.create_task(self.manager.broadcast(message))
                    except Exception as e:
                        logger.error(f"创建广播任务失败: {e}")

                self.loop.call_soon_threadsafe(create_task)
            except Exception as e:
                logger.error(f"safe_broadcast 失败: {e}")

    def start(self):
        import uvicorn

        logger.info(">>> Ember Server starting...")
        uvicorn.run(self.app, host="0.0.0.0", port=8000, loop="asyncio")


if __name__ == "__main__":
    server = EmberServer()
    server.start()
