"""Finite fictional life and event-based emotion; no user actions are simulated."""
import copy
import hashlib
import json
import math
import uuid
from datetime import datetime
from core.time_utils import get_timezone

AXES = ('P', 'A', 'D')
RULES = '''
【情绪与自主活动】role_continuity 是程序提交的记录，不得由模型覆写。
同一事件的情绪影响只记一次；空闲时情绪会按世界时间自然缓和，不凭重复阅读旧话继续加重。
可输出 role_activity_request：{"action":"start|pause|resume|cancel", "kind":"notes"}。
notes 表示角色整理自己的笔记，只有程序接受并计时推进后才形成开始、进展、结束记录。
没有请求或条件不满足就不执行。不得声称用户参加、完成真实作业或获得未提供的知识。
主观猜想可放 role_beliefs 字符串列表（最多3条），不是用户事实；旧猜想不等于新证据。
睡眠状态发生变化时可输出 action_pulse.is_sleeping（JSON布尔值）；不确定则保留原状态。
'''


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _pads(state):
    return {key: float(state.get(key, 5)) for key in AXES}


def load_continuity(state):
    raw = state.get('role_continuity')
    if raw is None:
        return {'version': 1, 'sequence': 0, 'events': [], 'activity': None,
                'emotion': None, 'processed_inputs': [], 'beliefs': [], 'sleeping': False}
    data = copy.deepcopy(raw)
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('角色连续性记录版本无效')
    if type(data.get('sequence')) is not int or data['sequence'] < 0:
        raise ValueError('角色事件序号无效')
    for key in ('events', 'processed_inputs', 'beliefs'):
        if not isinstance(data.get(key), list):
            raise ValueError('角色连续性列表无效')
    if (any(not isinstance(key, str) for key in data['processed_inputs'])
            or not isinstance(data.get('processed_life_events', []), list)
            or any(not isinstance(key, str) for key in data.get('processed_life_events', []))):
        raise ValueError('角色事件来源无效')
    for event in data['events']:
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not finite(event.get('at')):
            raise ValueError('角色事件无效')
        if (event.get('kind') not in ('started', 'progress', 'completed', 'paused', 'resumed', 'cancelled')
                or event.get('origin') not in ('online_simulation', 'offline_simulation')
                or event.get('participants') != ['character'] or not isinstance(event.get('description'), str)):
            raise ValueError('角色事件归属无效')
        if not isinstance(event.get('activity_id'), str) or event.get('source_type') != 'role_simulation_record':
            raise ValueError('角色事件来源无效')
    if type(data.get('starts_today', 0)) is not int or not 0 <= data.get('starts_today', 0) <= 2:
        raise ValueError('角色活动计数无效')
    if type(data.get('sleeping', False)) is not bool:
        raise ValueError('角色睡眠标记无效')
    activity = data.get('activity')
    if activity is not None:
        if (not isinstance(activity, dict) or activity.get('kind') != 'notes'
                or activity.get('status') not in ('running', 'paused', 'completed', 'cancelled')
                or not isinstance(activity.get('id'), str) or not isinstance(activity.get('location'), str)
                or not all(finite(activity.get(key)) for key in ('elapsed', 'duration', 'anchor'))
                or activity['elapsed'] < 0 or activity['duration'] <= 0):
            raise ValueError('角色活动记录无效')
    emotion = data.get('emotion')
    if emotion is not None:
        if (not isinstance(emotion, dict) or not finite(emotion.get('at'))
                or not isinstance(emotion.get('feeling'), str)
                or not all(isinstance(emotion.get(key), dict) and all(
                    finite(emotion[key].get(axis)) and 0 <= emotion[key][axis] <= 10 for axis in AXES)
                    for key in ('baseline', 'peak'))):
            raise ValueError('情绪事件记录无效')
    return data


def _event(data, activity, kind, now, origin='online_simulation'):
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"role:{activity['id']}:{kind}"))
    if kind not in ('paused', 'resumed') and any(row['id'] == key for row in data['events']):
        return
    descriptions = {'started': '开始整理自己的笔记', 'progress': '自己的笔记整理进行到中段',
                    'completed': '结束了这一轮自己的笔记整理', 'paused': '暂停整理自己的笔记',
                    'resumed': '继续整理自己的笔记', 'cancelled': '提前结束自己的笔记整理'}
    # Pause/resume can occur more than once; the activity revision owns their IDs.
    if kind in ('paused', 'resumed'):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"role:{activity['id']}:{kind}:{data['sequence']}"))
    data['events'].append({'id': key, 'kind': kind, 'at': now, 'activity_id': activity['id'],
        'location': activity['location'], 'description': descriptions[kind],
        'source_type': 'role_simulation_record', 'origin': origin, 'participants': ['character'], 'memory_pending': True})
    retained = {row['id'] for row in data['events'][-64:]}
    data['events'] = [row for row in data['events'] if row.get('memory_pending') or row['id'] in retained]


def advance(previous, now, *, half_life=3600, sleeping=False, offline=False):
    state = copy.deepcopy(previous)
    data = load_continuity(previous)
    activity = data['activity']
    future_activity = bool(activity and now < activity['anchor'])
    future_emotion = bool(data['emotion'] and now < data['emotion']['at'])
    if future_activity or future_emotion:
        # Real-time restart can precede an accelerated world's saved clock.
        # Preserve the old branch, but never claim its future progress happened now.
        data['time_discontinuity'] = {'at': now, 'reason': 'saved_world_ahead_of_clock'}
        for event in data['events']:
            if event['at'] > now:
                event['timeline_status'] = 'future_snapshot'
                event['memory_pending'] = False
        if future_activity:
            data['suspended_activity'] = copy.deepcopy(activity)
            data['sequence'] += 1
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f"role-rebase:{activity['id']}:{now}:{data['sequence']}"))
            activity.update(id=identity, status='paused', elapsed=0, anchor=now)
        if future_emotion:
            data['suspended_emotion'] = data['emotion']
            state.update(data['emotion']['baseline'])
            data['emotion'] = None
    if activity and activity['status'] == 'running' and now >= activity['anchor']:
        elapsed = min(activity['duration'], activity['elapsed'] + now - activity['anchor'])
        activity.update(elapsed=elapsed, anchor=now)
        location = state.get('scene', {}).get('character_location')
        origin = 'offline_simulation' if offline else 'online_simulation'
        if location != activity['location']:
            activity['status'] = 'cancelled'
            _event(data, activity, 'cancelled', now, origin)
        elif sleeping:
            data['sequence'] += 1
            activity['status'] = 'paused'
            _event(data, activity, 'paused', now, origin)
        elif elapsed >= activity['duration']:
            activity['status'] = 'completed'
            _event(data, activity, 'completed', now, origin)
        elif elapsed >= activity['duration'] / 2:
            _event(data, activity, 'progress', now, origin)
    emotion = data['emotion']
    if emotion and now >= emotion['at']:
        remaining = 2 ** (-(now - emotion['at']) / half_life)
        for key in AXES:
            target = 2 if sleeping and key == 'A' else emotion['baseline'][key]
            state[key] = round(target + (emotion['peak'][key] - target) * remaining, 4)
        emotion['remaining'] = round(remaining, 4)
    state['role_continuity'] = data
    return state


def commit(previous, candidate, now, *, input_id='', offline=False, sleeping=False,
           half_life=3600, duration=900, update_pad=True):
    """Commit a model proposal after scene validation; authoritative fields are ours."""
    state = advance(previous, now, half_life=half_life, sleeping=sleeping, offline=offline)
    data = state['role_continuity']
    # Only new runtime events or a new accepted user ID can anchor a fresh affect.
    new_events = [row for row in data['events'] if row['id'] not in data.get('processed_life_events', [])
        and row.get('timeline_status') != 'future_snapshot' and row['at'] <= now]
    basis = input_id if input_id and input_id not in data['processed_inputs'] else (
        new_events[-1]['id'] if new_events else '')
    if sleeping != data.get('sleeping', False) and not basis:
        basis = f"rest:{now}:{sleeping}"
    data['sleeping'] = sleeping
    view = copy.deepcopy(candidate.get('reply_perspective'))
    if basis:
        baseline = (data['emotion']['baseline'] if data['emotion'] else _pads(previous))
        feeling = view.get('feeling', '') if isinstance(view, dict) else ''
        data['emotion'] = {'basis_id': basis, 'at': now, 'baseline': baseline,
            'peak': _pads(candidate) if update_pad else _pads(state), 'feeling': feeling[:120], 'remaining': 1.0}
        if input_id:
            data['processed_inputs'] = (data['processed_inputs'] + [input_id])[-128:]
        data['processed_life_events'] = (data.get('processed_life_events', []) + [row['id'] for row in new_events])[-128:]
        if isinstance(candidate.get('role_beliefs'), list):
            data['beliefs'] = [{'text': text[:160], 'basis_id': basis, 'at': now,
                'source_type': 'role_interpretation'} for text in candidate['role_beliefs'][:3] if isinstance(text, str)]
    elif data['emotion']:
        if isinstance(view, dict):
            view['feeling'] = data['emotion']['feeling'] if data['emotion'].get('remaining', 1) >= .15 else ''
    # Retain validated model state, then enforce deterministic emotion/life fields.
    result = copy.deepcopy(candidate)
    for key in AXES:
        result[key] = data['emotion']['peak'][key] if basis else state[key]
    if isinstance(view, dict):
        result['reply_perspective'] = view
    request = candidate.get('role_activity_request')
    activity = data['activity']
    if activity and activity['status'] == 'running' and candidate.get('scene', {}).get('character_location') != activity['location']:
        activity['status'] = 'cancelled'
        _event(data, activity, 'cancelled', now, 'offline_simulation' if offline else 'online_simulation')
    if not offline and not sleeping and isinstance(request, dict) and request.get('kind') == 'notes':
        action, activity = request.get('action'), data['activity']
        if action == 'start' and (not activity or activity['status'] in ('completed', 'cancelled')):
            location = candidate.get('scene', {}).get('character_location')
            day = datetime.fromtimestamp(now, get_timezone()).date().isoformat()
            if location and (data.get('start_day') != day or data.get('starts_today', 0) < 2):
                data['sequence'] += 1
                identity = hashlib.sha256(json.dumps([now, data['sequence'], location]).encode()).hexdigest()[:32]
                activity = {'id': identity, 'kind': 'notes', 'status': 'running', 'elapsed': 0,
                    'duration': duration, 'anchor': now, 'location': location}
                data['activity'] = activity
                data['starts_today'] = data.get('starts_today', 0) + 1 if data.get('start_day') == day else 1
                data['start_day'] = day
                _event(data, activity, 'started', now)
        elif activity and (action, activity['status']) in (('pause', 'running'), ('resume', 'paused'), ('cancel', 'running'), ('cancel', 'paused')):
            data['sequence'] += 1
            if action == 'resume' and candidate.get('scene', {}).get('character_location') != activity['location']:
                action = 'cancel'
            activity['status'] = {'pause': 'paused', 'resume': 'running', 'cancel': 'cancelled'}[action]
            activity['anchor'] = now
            _event(data, activity, {'pause': 'paused', 'resume': 'resumed', 'cancel': 'cancelled'}[action], now)
    result.pop('role_activity_request', None)
    result.pop('role_beliefs', None)
    result['role_continuity'] = data
    active = data['activity']
    if active and active['status'] == 'running':
        result['客观情境'] = f"{active['location']}；整理自己的笔记"
    return result


def context(state, now=None):
    data = load_continuity(state)
    emotion = copy.deepcopy(data['emotion'])
    if emotion and emotion.get('remaining', 1) < .15:
        emotion['feeling'] = ''
    return ('\n【角色持续状态：虚构世界记录与主观视角】\n' + json.dumps({
        'activity': data['activity'], 'emotion': emotion, 'beliefs': data['beliefs'],
        'events': [row for row in data['events'] if row.get('timeline_status') != 'future_snapshot'
            and (now is None or row['at'] <= now)][-8:], 'time_discontinuity': data.get('time_discontinuity')}, ensure_ascii=False)
        + '\n活动只属于角色，不代表用户参与、现实作业完成或共同约定。离线来源为规则模拟。'
        '活动 elapsed/duration 只表示这一轮的计时阶段，不能推算已完成半页、整页或具体笔记内容。'
        'beliefs 和情绪只代表角色的理解，不是用户事实；打算与已经发生的事件分开。'
        '\n个人往事的可用来源：已提供档案、带来源的记忆和上述事件。其他具体往事尚未设定；'
        '兴趣起源不明时可以谈喜欢的理由或泛指体验，不用补一个首次观察、上次调试等具体故事。')
