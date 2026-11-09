"""Heartbeat -> state decision -> proactive reply -> persistence/WS/TTS, isolated."""
import asyncio
import copy
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from brain.core import Brain
from core.event_bus import Event
from core.heartbeat import Heartbeat
from tests.test_persistence_scene import make_state_manager
from tests.test_turn_cancellation import make_brain
from tests.test_restore_experience import make_server


def test_actual_heartbeat_reaches_proactive_history_websocket_and_speech():
    server = make_server()
    state = server.state_manager
    state.prepare_startup = Mock()
    state.current_state['scene'].update(character_location='图书馆', user_location=None,
        communication_mode='unknown', remote_contact_confirmed=False)
    state.last_dialogue_logical_time = server.event_bus.logical_now
    candidate = copy.deepcopy(state.current_state)
    candidate['action_pulse'] = {'should_speak': True, 'memory_encode': False, 'is_sleeping': False}
    state._ask_llm_with_tools = Mock(return_value=json.dumps(candidate, ensure_ascii=False))
    state.llm_client._extract_json = json.loads
    state.last_interaction_real_time = time.monotonic() - 100
    tools = Mock()
    tools.build_system_prompt_with_tools.side_effect = lambda text: text
    tools.extract_tool_calls.return_value = []
    tools.has_tool_calls.return_value = False
    tools.remove_tool_calls.side_effect = lambda text: text
    llm = Mock()
    speech = '我整理了刚才的话题，要继续吗？'
    llm.stream_chat.return_value = iter([f'<thought>合成主动意图</thought><response>{speech}</response>'])
    with patch('brain.core.LLMClient', return_value=llm):
        server.brain = Brain(server.event_bus, state, server.memory, Mock(), tools)
    server.tts_manager = SimpleNamespace(generate_base64=AsyncMock(return_value='synthetic-audio'))
    server._tts_semaphore = asyncio.Semaphore(3)
    server.episodic_memory.stop = Mock()
    heartbeat = Heartbeat(server.event_bus, .02)
    finished = threading.Event()
    server.event_bus.subscribe('turn.finished', lambda _: (heartbeat._stop_event.set(), finished.set()))
    frames = []
    received = threading.Event()
    with TestClient(server.app) as client:
        with client.websocket_connect('/ws/chat') as socket:
            def read():
                try:
                    while True:
                        frames.append(socket.receive_json())
                        if all(any(frame.get('type') == kind for frame in frames)
                               for kind in ('turn.finished', 'audio')):
                            received.set()
                            return
                except Exception:
                    received.set()
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            server.heartbeat = heartbeat
            heartbeat.start()
            assert finished.wait(5), 'Heartbeat did not finish a proactive turn'
            assert received.wait(5), 'Proactive frames did not reach the websocket'
            heartbeat.stop()
            turn = server.event_bus.turns.snapshot()
            assert turn['source'] == 'proactive' and turn['status'] == 'completed' and turn['released']
            assert any(frame.get('mode') == 'replace' and speech in frame.get('content', '') for frame in frames)
            assert any(frame.get('type') == 'audio' and frame['turn_id'] == turn['turn_id'] for frame in frames)
            assert server.db_memory.flush()
            saved = next(row for row in server.db_memory.get_history() if row['message_id'] == turn['message_id'])
            assert saved['content'] == speech
            assert not any(item['role'] == 'user' for item in server.memory.get_memory()['history'])
            assert llm.stream_chat.call_args.kwargs['call_type'] == 'proactive'
            assert '主动' in llm.stream_chat.call_args.kwargs['messages'][0]['content']
            assert '网页主动陪伴' in llm.stream_chat.call_args.kwargs['messages'][0]['content']
            server.tts_manager.generate_base64.assert_awaited_once_with(speech)


@pytest.mark.parametrize('mode,contact,sleeping,pulse,expected', [
    ('face_to_face', False, False, True, True),
    ('remote', True, False, True, True),
    ('remote', False, False, True, False),
    ('unknown', True, False, True, False),
    ('face_to_face', False, True, True, False),
    ('face_to_face', False, False, False, False),
    ('face_to_face', False, False, 'false', False),
])
def test_proactive_scene_and_sleep_gates(mode, contact, sleeping, pulse, expected):
    state = make_state_manager()
    state.current_state['scene'].update(communication_mode=mode, remote_contact_confirmed=contact)
    state.last_dialogue_logical_time = state.event_bus.logical_now
    events = []
    state.event_bus.subscribe('idle_speak', events.append)
    with patch.object(state, '_generate_state', return_value={'should_speak': pulse, 'is_sleeping': sleeping}):
        state._update_state_due_to_idle(state.event_bus.logical_now)
    assert bool(events) is expected


@pytest.mark.parametrize('flag', ['quiet', 'world_paused', 'paused', 'archive_active'])
def test_proactive_obeys_silence_pause_and_archive(flag):
    brain, bus, _memory, _state = make_brain()
    setattr(bus, flag, True)
    done = threading.Event()
    real_thread = threading.Thread
    def worker(*args, **kwargs):
        target = kwargs['target']
        def run():
            try: target()
            finally: done.set()
        kwargs['target'] = run
        return real_thread(*args, **kwargs)
    with patch('brain.core.threading.Thread', side_effect=worker):
        brain._on_idle_speak(Event('idle_speak', {}))
        if flag == 'paused': assert done.wait(2)
    brain.llm_client.stream_chat.assert_not_called()
    assert bus.turns.current is None


def test_proactive_preparation_failure_releases_turn_and_next_dialogue_works():
    brain, bus, _memory, _state = make_brain()
    brain.tool_processor.build_system_prompt_with_tools.side_effect = [RuntimeError('synthetic failure'), 'base']
    # Execute the worker synchronously so an escaped preparation error fails here.
    with patch('brain.core.threading.Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])):
        brain._on_idle_speak(Event('idle_speak', {}))
    assert bus.turns.snapshot()['status'] == 'failed'
    assert bus.turns.snapshot()['released']
    assert not brain._is_processing
    brain.llm_client.stream_chat.return_value = iter(['<response>合成正常回复</response>'])
    brain.process_dialogue('继续测试')
    assert bus.turns.snapshot()['status'] == 'completed'
    assert bus.turns.snapshot()['released']


def test_pending_user_input_has_priority_over_idle_state_call():
    state = make_state_manager()
    state.ready = True
    state.event_bus.pending_dialogues = 1
    state.last_interaction_real_time = time.monotonic() - 100
    with patch.object(state, '_update_state_due_to_idle') as evolve:
        state._on_tick(Event('system.tick', {}))
    evolve.assert_not_called()


@pytest.mark.parametrize('pulse', [{'should_speak': 'false'}, ['invalid']])
def test_non_boolean_or_malformed_pulse_does_not_commit_model_state(pulse):
    state = make_state_manager()
    candidate = copy.deepcopy(state.current_state)
    candidate['客观情境'] = '宿舍内，活动待确认'
    candidate['action_pulse'] = pulse
    with patch.object(state, '_ask_llm_with_tools', return_value=json.dumps(candidate)), \
         patch.object(state.llm_client, '_extract_json', side_effect=json.loads):
        assert state._generate_state('synthetic', state.event_bus.logical_now) == {}
    assert state.current_state['state_source'] == 'fallback'


def test_heartbeat_restart_clears_pause_state():
    from core.event_bus import EventBus
    heartbeat = Heartbeat(EventBus(), .02)
    try:
        heartbeat.start()
        heartbeat.pause()
        heartbeat.stop()
        heartbeat.start()
        assert not heartbeat.is_paused
        assert heartbeat._thread.is_alive()
    finally:
        heartbeat.stop()


@pytest.mark.parametrize('quiet,sleeping,expected', [(False, False, True), (True, False, False), (False, True, False)])
def test_web_proactive_uses_permission_not_unknown_position_or_fictional_contact(quiet, sleeping, expected):
    state = make_state_manager()
    state.event_bus.proactive_channel = 'web'
    state.event_bus.quiet = quiet
    state.current_state['scene'].update(user_location=None, communication_mode='unknown', remote_contact_confirmed=False)
    events = []
    state.event_bus.subscribe('idle_speak', events.append)
    with patch.object(state, '_generate_state', return_value={'should_speak': True, 'is_sleeping': sleeping}):
        state._update_state_due_to_idle(state.event_bus.logical_now)
    assert bool(events) is expected
