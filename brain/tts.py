import edge_tts
import asyncio
import logging
import os
import uuid
from config.settings import settings
from brain.tag_utils import remove_thought_content

logger = logging.getLogger("EmberTTS")

class TTSManager:
    def __init__(self, voice="zh-CN-XiaoxiaoNeural"):
        self.voice = voice
        self.output_dir = "data/audio"
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_base64(self, text: str, timeout: float | None = None) -> str | None:
        """合成语音并返回 Base64 编码，带超时保护

        Args:
            text: 要合成的文本
            timeout: 超时时间（秒），None 则使用配置默认值

        Returns:
            Base64 编码的音频数据，失败返回 None
        """
        import base64
        import asyncio

        # 使用配置默认值
        actual_timeout = timeout if timeout is not None else settings.TTS_TIMEOUT_SECONDS

        try:
            # 移除 <thought> 标签内容
            clean_text = remove_thought_content(text)

            # 使用 asyncio.wait_for 包装 TTS 操作
            audio_data = await asyncio.wait_for(
                self._do_tts(clean_text),
                timeout=actual_timeout
            )

            return base64.b64encode(audio_data).decode('utf-8')
        except asyncio.TimeoutError:
            logger.error(f"TTS 合成超时 (>{actual_timeout}s)")
            return None
        except Exception as e:
            logger.error(f"TTS 合成失败: {e}")
            return None

    async def _do_tts(self, clean_text: str):
        """执行实际的 TTS 合成"""
        communicate = edge_tts.Communicate(clean_text, self.voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    def cleanup(self, filename):
        """清理旧的语音文件"""
        filepath = os.path.join(self.output_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
