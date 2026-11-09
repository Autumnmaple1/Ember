"""Structured scene facts and deterministic clock consistency checks."""
import copy
import re
import math
from datetime import datetime
from core.time_utils import get_timezone, time_period


def expire_user_presence(state, last_interaction, now):
    """After a long dialogue gap, last known presence stops being current fact.

    This uses world-clock idle time, not GPS or a per-location observation model.
    Contact details and the character's location are durable independent facts.
    """
    result = copy.deepcopy(state)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (last_interaction, now)):
        return result
    gap = now - last_interaction
    if gap < 0:
        return result  # Restore owns the clock and the archived scene.
    try:
        current_date = datetime.fromtimestamp(now, get_timezone()).date()
        previous_date = datetime.fromtimestamp(last_interaction, get_timezone()).date()
    except (ValueError, OverflowError, OSError):
        return result
    changed_day = current_date != previous_date
    if gap < 1800 and (not changed_day or gap < 300):
        return result
    scene = normalize_scene(state)
    if scene['user_location'] is None and scene['communication_mode'] == 'unknown':
        return result
    result['last_known_presence'] = {'user_location': scene['user_location'],
        'communication_mode': scene['communication_mode'],
        'last_interaction': datetime.fromtimestamp(last_interaction, get_timezone()).isoformat(),
        'basis': 'last_dialogue_before_gap'}
    scene.update(user_location=None, communication_mode='unknown')
    result['scene'] = scene
    result['客观情境'] = f"{scene['character_location'] or '角色位置待确认'}；用户当前所在地未知"
    return result

UNKNOWN_SCENE = {'character_location': None, 'user_location': None,
                 'communication_mode': 'unknown', 'remote_contact_confirmed': False}


def normalize_scene(state):
    scene = copy.deepcopy(UNKNOWN_SCENE)
    source = state.get('scene')
    if not isinstance(source, dict) and any(key in state for key in UNKNOWN_SCENE):
        source = {key: state[key] for key in UNKNOWN_SCENE if key in state}
    if isinstance(source, dict):
        scene.update({k: source[k] for k in scene if k in source})
    else:
        # Recover ONLY the character's location from the legacy scene description.
        prefix = re.split(r'[，。,\n]', state.get('客观情境', ''))[0].strip()
        if prefix and not re.search(r'\d{1,2}:\d{2}|深夜|上午|下午|晚上|清晨', prefix):
            scene['character_location'] = prefix
    for key in ('character_location', 'user_location'):
        if not isinstance(scene[key], str) or not scene[key].strip():
            scene[key] = None
    scene['remote_contact_confirmed'] = scene['remote_contact_confirmed'] is True
    if scene['communication_mode'] not in ('face_to_face', 'remote', 'unknown'):
        scene['communication_mode'] = 'unknown'
    if scene['communication_mode'] == 'face_to_face' and (
            not scene['character_location'] or scene['character_location'] != scene['user_location']):
        scene['communication_mode'] = 'unknown'
    if scene['communication_mode'] == 'remote' and not scene['remote_contact_confirmed']:
        scene['communication_mode'] = 'unknown'
    return scene


def has_time_conflict(text, epoch):
    now = datetime.fromtimestamp(epoch, get_timezone())
    for match in re.finditer(r'(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)', text):
        hour, minute = map(int, match.groups())
        if hour > 23 or minute > 59:
            return True
        # Descriptions may be generated seconds ago; allow a ten minute difference.
        distance = abs((now.hour * 60 + now.minute) - (hour * 60 + minute))
        if min(distance, 1440 - distance) > 10:
            return True
    for date in re.findall(r'\d{4}-\d{2}-\d{2}', text):
        if date != now.strftime('%Y-%m-%d'):
            return True
    expected = time_period(epoch)
    for label in ('深夜', '凌晨', '清晨', '上午', '中午', '下午', '晚上', '午后'):
        canonical = {'凌晨': '深夜', '午后': '下午'}.get(label, label)
        if label in text and canonical != expected:
            return True
    return False


def validate_scene(candidate, previous, user_text='', allow_inferred=False):
    scene = normalize_scene(candidate)
    prior = normalize_scene(previous)
    source = candidate.get('scene')
    evidence = candidate.get('scene_evidence', source.get('scene_evidence', '') if isinstance(source, dict) else '')
    if isinstance(evidence, list):
        fragments = [item for item in evidence if isinstance(item, str) and item.strip()]
        evidence = user_text if fragments and all(item in user_text for item in fragments) else ''
    explicit = bool(user_text and isinstance(evidence, str) and evidence.strip()
                    and evidence in user_text)
    if not explicit:
        scene['user_location'] = prior['user_location']
        scene['remote_contact_confirmed'] = prior['remote_contact_confirmed']
        scene['communication_mode'] = prior['communication_mode']
        if not allow_inferred:
            scene['character_location'] = prior['character_location']
    else:
        if scene['remote_contact_confirmed'] and not prior['remote_contact_confirmed'] and not re.search(
                r'微信|电话|手机号|联系方式|好友|QQ|短信', evidence, re.IGNORECASE):
            scene['remote_contact_confirmed'] = False
        for key in ('character_location', 'user_location'):
            if scene[key] != prior[key] and scene[key] and scene[key] not in evidence:
                scene[key] = prior[key]
    return normalize_scene({'scene': scene})
