"""Voice failures are simulated; these tests never contact a speech service."""
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from brain.tts import TTSGenerationError, TTSManager


def test_tts_returns_audio_and_removes_private_thoughts():
    manager = TTSManager()
    manager._do_tts = AsyncMock(return_value=b'synthetic-mp3')
    result = asyncio.run(manager.generate_base64('<thought>私下独白</thought>你好'))
    assert base64.b64decode(result) == b'synthetic-mp3'
    manager._do_tts.assert_awaited_once_with('你好')


@pytest.mark.parametrize('text', ['', '  ', '<thought>只有独白</thought>', '<think>只有推理</think>', '<think>中断推理'])
def test_empty_speech_never_calls_service(text):
    manager = TTSManager()
    manager._do_tts = AsyncMock()
    with pytest.raises(TTSGenerationError) as raised:
        asyncio.run(manager.generate_base64(text))
    assert raised.value.code == 'empty_text'
    manager._do_tts.assert_not_awaited()


@pytest.mark.parametrize('status', [403, 429, 503])
def test_http_errors_preserve_reason_without_exposing_service_tokens(status):
    manager = TTSManager()
    manager._do_tts = AsyncMock(side_effect=aiohttp.WSServerHandshakeError(
        SimpleNamespace(real_url='https://synthetic/?secret-token=private'), (),
        status=status, message='private upstream details'))
    with pytest.raises(TTSGenerationError) as raised:
        asyncio.run(manager.generate_base64('你好'))
    assert raised.value.code == f'http_{status}'
    assert 'private' not in str(raised.value)
    assert 'secret-token' not in str(raised.value)


def test_empty_audio_is_not_success():
    manager = TTSManager()
    manager._do_tts = AsyncMock(return_value=b'')
    with pytest.raises(TTSGenerationError) as raised:
        asyncio.run(manager.generate_base64('你好'))
    assert raised.value.code == 'no_audio'


def test_timeout_cancels_service_work_and_returns_visible_error():
    manager = TTSManager()
    cancelled = []

    async def blocked(_text):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    manager._do_tts = blocked
    with pytest.raises(TTSGenerationError) as raised:
        asyncio.run(manager.generate_base64('你好', timeout=0.01))
    assert raised.value.code == 'timeout'
    assert cancelled == [True]


def test_network_failure_is_classified():
    manager = TTSManager()
    manager._do_tts = AsyncMock(side_effect=aiohttp.ClientConnectionError('connection refused'))
    with pytest.raises(TTSGenerationError) as raised:
        asyncio.run(manager.generate_base64('你好'))
    assert raised.value.code == 'network'


def test_task_cancellation_is_not_reported_as_generation_failure():
    manager = TTSManager()
    manager._do_tts = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.generate_base64('你好'))


@pytest.mark.parametrize('text', [
    '<think>私有推理</think><thought>角色独白</thought>你好',
    '<REASONING>私有推理</REASONING>你好',
])
def test_tts_service_never_receives_provider_reasoning(text):
    manager = TTSManager()
    manager._do_tts = AsyncMock(return_value=b'synthetic-mp3')
    asyncio.run(manager.generate_base64(text))
    manager._do_tts.assert_awaited_once_with('你好')
