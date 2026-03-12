import edge_tts
import asyncio
import logging
import os
import uuid
from brain.tag_utils import remove_thought_content

logger = logging.getLogger("EmberTTS")

class TTSManager:
    def __init__(self, voice="zh-CN-XiaoxiaoNeural"):
        self.voice = voice
        self.output_dir = "data/audio"
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_base64(self, text: str):
        """合成语音并返回 Base64 编码"""
        # 移除 <thought> 标签内容（使用增强的容错处理）
        import base64
        clean_text = remove_thought_content(text)
        
        communicate = edge_tts.Communicate(clean_text, self.voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        
        return base64.b64encode(audio_data).decode('utf-8')

    def cleanup(self, filename):
        """清理旧的语音文件"""
        filepath = os.path.join(self.output_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
