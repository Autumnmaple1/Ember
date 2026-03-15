"""
Ember 服务器主模块

提供 WebSocket 实时通信、HTTP API 和 LLM/TTS 集成功能
"""
import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from brain.core import Brain
from brain.tts import TTSManager
from config.logging_config import get_logger
from config.settings import settings
from core.event_bus import Event, EventBus
from core.heartbeat import Heartbeat
from memory.db_memory import DBMemory
from memory.entity_extraction import EntityExtractionMemory
from memory.episodic_memory import EpisodicMemory
from memory.memory_process import Hippocampus
from memory.short_term import ShortTermMemory
from persona.state_manager import StateManager

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# WebSocket 消息类型常量
MSG_TYPE_PING = "ping"
MSG_TYPE_PONG = "pong"
MSG_TYPE_TTS_REQUEST = "tts_request"
MSG_TYPE_MESSAGE = "message"
MSG_TYPE_STATE_UPDATE = "state_update"
MSG_TYPE_AUDIO = "audio"
MSG_TYPE_LLM_DONE = "llm.done"

# WebSocket 心跳配置
PING_INTERVAL_SECONDS = 30
RECEIVE_TIMEOUT_SECONDS = 5.0


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """接受新连接"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"新 WebSocket 连接，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """断开连接"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket 断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: dict) -> None:
        """广播消息到所有连接"""
        if not self.active_connections:
            return

        payload = json.dumps(message, ensure_ascii=False)
        tasks = [
            self._safe_send(connection, payload)
            for connection in self.active_connections
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_send(self, websocket: WebSocket, payload: str) -> None:
        """安全发送消息（失败时断开连接）"""
        try:
            await websocket.send_text(payload)
        except Exception:
            self.disconnect(websocket)


class WebSocketHandler:
    """WebSocket 消息处理器"""

    def __init__(self, server: "EmberServer") -> None:
        self.server = server
        self.last_ping_time = 0.0

    async def handle(self, websocket: WebSocket) -> None:
        """处理 WebSocket 连接生命周期"""
        await self.server.manager.connect(websocket)
        self.last_ping_time = time.time()

        try:
            await self._message_loop(websocket)
        except WebSocketDisconnect:
            logger.info("WebSocket 客户端断开连接")
        except Exception as e:
            logger.error(f"WebSocket 处理异常: {e}")
        finally:
            self.server.manager.disconnect(websocket)

    async def _message_loop(self, websocket: WebSocket) -> None:
        """消息接收主循环"""
        while True:
            await self._send_heartbeat_if_needed(websocket)

            message = await self._receive_message(websocket)
            if message is None:
                continue

            should_continue = await self._process_message(websocket, message)
            if not should_continue:
                break

    async def _send_heartbeat_if_needed(self, websocket: WebSocket) -> None:
        """发送心跳 ping（如果需要）"""
        if time.time() - self.last_ping_time <= PING_INTERVAL_SECONDS:
            return

        try:
            await websocket.send_text(json.dumps({"type": MSG_TYPE_PING}))
            self.last_ping_time = time.time()
        except Exception:
            raise WebSocketDisconnect()

    async def _receive_message(self, websocket: WebSocket) -> dict | None:
        """接收并解析消息（带超时）"""
        try:
            data = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=RECEIVE_TIMEOUT_SECONDS
            )
            return json.loads(data)
        except asyncio.TimeoutError:
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"收到无效的 JSON 消息: {e}")
            return None

    async def _process_message(
        self,
        websocket: WebSocket,
        message: dict
    ) -> bool:
        """
        处理单条消息

        Returns:
            是否继续循环
        """
        msg_type = message.get("type")

        handlers = {
            MSG_TYPE_PONG: self._handle_pong,
            MSG_TYPE_TTS_REQUEST: self._handle_tts_request,
            MSG_TYPE_MESSAGE: self._handle_user_message,
        }

        handler = handlers.get(msg_type)
        if handler:
            return await handler(message)

        # 未知消息类型，继续循环
        return True

    async def _handle_pong(self, message: dict) -> bool:
        """处理心跳 pong"""
        return True

    async def _handle_tts_request(self, message: dict) -> bool:
        """处理 TTS 请求"""
        text = message.get("content")
        if text:
            logger.info(f"收到手动 TTS 请求: {text[:20]}...")
            asyncio.create_task(self.server.process_tts(text))
        return True

    async def _handle_user_message(self, message: dict) -> bool:
        """处理用户输入消息"""
        user_input = message.get("content")
        if not user_input:
            return True

        timestamp = int(self.server.event_bus.logical_now * 1000)

        await self.server.manager.broadcast({
            "type": MSG_TYPE_MESSAGE,
            "sender": "user",
            "content": user_input,
            "timestamp": timestamp,
            "id": timestamp,
        })

        self.server.event_bus.publish(
            Event(name="user.input", data={"text": user_input})
        )
        return True


class EmberServer:
    """Ember 服务器主类"""

    def __init__(self) -> None:
        self.app = FastAPI(title="Ember Server", version="1.0.0")
        self.event_bus = EventBus()
        self.manager = ConnectionManager()
        self.loop: asyncio.AbstractEventLoop | None = None

        # AI 消息追踪
        self.current_ai_msg_id: int | None = None
        self.current_full_text: str = ""

        # TTS 并发控制
        self._tts_semaphore = asyncio.Semaphore(settings.TTS_MAX_CONCURRENT)

        # 初始化组件
        self._init_components()

        # 设置路由和处理器
        self._setup_middleware()
        self._setup_routes()
        self._setup_event_handlers()

    def _init_components(self) -> None:
        """初始化所有子组件"""
        self.heartbeat = Heartbeat(
            self.event_bus,
            interval=settings.HEARTBEAT_INTERVAL
        )
        self.memory = ShortTermMemory(
            base_prompt=settings.SYSTEM_PROMPT,
            max_memory_size=settings.CONTEXT_WINDOW_SIZE,
        )
        self.episodic_memory = EpisodicMemory(self.event_bus)
        self.hippocampus = Hippocampus(self.event_bus)
        self.db_memory = DBMemory(self.event_bus)
        self.state_manager = StateManager(
            self.event_bus,
            self.hippocampus,
            self.memory
        )
        self.entity_memory = EntityExtractionMemory(self.event_bus)
        self.brain = Brain(
            self.event_bus,
            self.state_manager,
            self.memory,
            self.hippocampus
        )
        self.tts_manager = TTSManager(voice="zh-CN-XiaoxiaoNeural")

    def _setup_middleware(self) -> None:
        """配置中间件"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _setup_routes(self) -> None:
        """配置路由"""
        self._setup_startup_event()
        self._setup_static_files()
        self._setup_api_routes()
        self._setup_websocket()

    def _setup_startup_event(self) -> None:
        """设置启动事件"""
        @self.app.on_event("startup")
        async def startup_event():
            self.loop = asyncio.get_running_loop()
            logger.info(f"Asyncio 事件循环初始化完成: {self.loop}")

    def _setup_static_files(self) -> None:
        """挂载静态文件目录"""
        audio_dir = "data/audio"
        os.makedirs(audio_dir, exist_ok=True)
        self.app.mount("/audio", StaticFiles(directory=audio_dir), name="audio")

    def _setup_api_routes(self) -> None:
        """设置 API 路由"""
        @self.app.get("/config")
        async def get_config():
            return {
                "character_name": "Ember",
                "display_name": settings.CHARACTER_NAME,
                "state": self.state_manager.current_state,
                "logical_time": self.event_bus.formatted_logical_now,
            }

        @self.app.get("/history")
        async def get_history(limit: int = 20, before: int | None = None):
            try:
                return self.db_memory.get_history(
                    limit=limit,
                    before_timestamp=before
                )
            except Exception as e:
                logger.error(f"获取历史记录失败: {e}")
                return []

    def _setup_websocket(self) -> None:
        """设置 WebSocket 路由"""
        @self.app.websocket("/ws/chat")
        async def websocket_endpoint(websocket: WebSocket):
            handler = WebSocketHandler(self)
            await handler.handle(websocket)

    def _setup_event_handlers(self) -> None:
        """订阅事件总线事件"""
        self.event_bus.subscribe("llm.started", self._on_ai_start)
        self.event_bus.subscribe("llm.chunk", self._on_ai_chunk)
        self.event_bus.subscribe("llm.finished", self._on_ai_finished)
        self.event_bus.subscribe(
            "state.update",
            lambda e: self.safe_broadcast({
                "type": MSG_TYPE_STATE_UPDATE,
                "state": e.data.get("new_state", {})
            })
        )

    def _on_ai_start(self, event: Event) -> None:
        """处理 AI 开始生成事件"""
        self.current_ai_msg_id = int(self.event_bus.logical_now * 1000)
        self.current_full_text = ""

        self.safe_broadcast({
            "type": MSG_TYPE_MESSAGE,
            "sender": "ai",
            "content": "",
            "mode": "start",
            "timestamp": self.current_ai_msg_id,
            "id": self.current_ai_msg_id,
        })

    def _on_ai_chunk(self, event: Event) -> None:
        """处理 AI 生成片段事件"""
        if not self.current_ai_msg_id:
            return

        chunk = event.data.get("text", "")
        self.current_full_text += chunk

        self.safe_broadcast({
            "type": MSG_TYPE_MESSAGE,
            "sender": "ai",
            "content": chunk,
            "mode": "append",
            "id": self.current_ai_msg_id,
        })

    def _on_ai_finished(self, event: Event) -> None:
        """处理 AI 完成生成事件"""
        logger.info(
            f"LLM 完成输出，准备合成 TTS... (内容长度: {len(self.current_full_text)})"
        )

        if self.current_full_text and self.current_full_text.strip():
            self._schedule_tts(self.current_full_text)

        self.safe_broadcast({"type": MSG_TYPE_LLM_DONE})

    def _schedule_tts(self, text: str) -> None:
        """调度 TTS 任务"""
        if not self.loop:
            return

        def create_task():
            asyncio.create_task(self.process_tts(text))

        self.loop.call_soon_threadsafe(create_task)

    async def process_tts(self, text: str) -> None:
        """
        处理 TTS 合成

        Args:
            text: 要合成的文本
        """
        if not text or not text.strip():
            return

        async with self._tts_semaphore:
            try:
                truncated_text = self._truncate_tts_text(text)
                base64_audio = await self.tts_manager.generate_base64(truncated_text)

                if base64_audio:
                    logger.info(f"广播 Base64 TTS 音频 (长度: {len(base64_audio)})")
                    await self.manager.broadcast({
                        "type": MSG_TYPE_AUDIO,
                        "audio_base64": base64_audio
                    })
            except Exception as e:
                logger.error(f"TTS 处理错误: {e}")

    def _truncate_tts_text(self, text: str) -> str:
        """截断过长的 TTS 文本"""
        max_length = settings.TTS_MAX_TEXT_LENGTH
        if len(text) <= max_length:
            return text

        logger.warning(f"TTS 文本过长，已截断至 {max_length} 字符")
        return text[:max_length] + "..."

    def safe_broadcast(self, message: dict) -> None:
        """线程安全的广播方法"""
        if not self.loop or not self.loop.is_running():
            return

        def create_task():
            try:
                asyncio.create_task(self.manager.broadcast(message))
            except Exception as e:
                logger.error(f"创建广播任务失败: {e}")

        try:
            self.loop.call_soon_threadsafe(create_task)
        except Exception as e:
            logger.error(f"safe_broadcast 失败: {e}")

    def start(self) -> None:
        """启动服务器"""
        self.heartbeat.start()
        logger.info(">>> Ember Server starting...")
        uvicorn.run(
            self.app,
            host="0.0.0.0",
            port=8000,
            loop="asyncio"
        )


if __name__ == "__main__":
    server = EmberServer()
    server.start()
