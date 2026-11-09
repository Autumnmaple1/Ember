import edge_tts
import asyncio
import base64
import logging
import os
import aiohttp
from brain.tag_utils import remove_thought_content
from core.service_policy import reserve, finish

logger = logging.getLogger("EmberTTS")


class TTSGenerationError(RuntimeError):
    """Safe, actionable error for clients; never includes service URLs or tokens."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class TTSManager:
    def __init__(self, voice="zh-CN-XiaoxiaoNeural"):
        self.voice = voice
        self.output_dir = "data/audio"
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_base64(self, text: str, timeout: float = 30.0):
        """Return nonempty audio or raise a classified error; cancellation propagates."""
        clean_text = remove_thought_content(text)
        if not clean_text or not clean_text.strip():
            raise TTSGenerationError('empty_text', '这条消息没有可朗读的对白')

        ticket = None
        try:
            try:
                ticket = reserve('tts', self.voice, 'speech')
            except RuntimeError as exc:
                raise TTSGenerationError('quota_exceeded', str(exc)) from exc
            audio_data = await asyncio.wait_for(
                self._do_tts(clean_text),
                timeout=timeout
            )

            if not audio_data:
                raise TTSGenerationError('no_audio', '语音服务未返回音频，请重试')
            finish(ticket)
            ticket = None
            return base64.b64encode(audio_data).decode('utf-8')
        except TTSGenerationError:
            raise
        except asyncio.TimeoutError as exc:
            raise TTSGenerationError('timeout', '语音生成超时，请稍后重试') from exc
        except aiohttp.ClientResponseError as exc:
            if exc.status == 403:
                message = '语音服务拒绝请求（403），请更新 edge-tts 并检查网络后重试'
            elif exc.status == 429:
                message = '语音服务请求过于频繁，请稍后重试'
            else:
                message = f'语音服务暂时不可用（HTTP {exc.status}），请稍后重试'
            raise TTSGenerationError(f'http_{exc.status}', message) from exc
        except aiohttp.ClientError as exc:
            raise TTSGenerationError('network', '无法连接语音服务，请检查网络后重试') from exc
        except edge_tts.exceptions.NoAudioReceived as exc:
            raise TTSGenerationError('no_audio', '语音服务未返回音频，请重试') from exc
        except Exception as e:
            logger.error('TTS 合成失败 (%s)', type(e).__name__)
            raise TTSGenerationError('generation_failed', '语音生成失败，请稍后重试') from e
        finally:
            finish(ticket, status='failed')

    async def _do_tts(self, clean_text: str):
        """执行实际的 TTS 合成"""
        communicate = edge_tts.Communicate(clean_text, self.voice)
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        return bytes(audio_data)

    def cleanup(self, filename):
        """清理旧的语音文件"""
        filepath = os.path.join(self.output_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
