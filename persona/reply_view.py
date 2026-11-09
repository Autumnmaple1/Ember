"""Small subjective reply view, stamped by the runtime rather than the model."""
import math

VIEW_RULES = '''
【当前角色视角】可在完整状态中附加 reply_perspective 对象：
{"feeling":"当下感受与起因", "intent":"此刻想说或想做的事", "attention":"当前关注的话题或对象"}。
每项最多120字，可以为空；是角色的主观感受或候选意图，不是已完成行动，也不能证明用户内心。
依据当前输入、原有感受与活动生成，不复述长轨迹；允许玩笑、不同意见、犹豫或委屈。
同一句旧反馈不是新事件，不因再次想起就继续叠加伤害；情绪可延续、自然变化，不要求立即平静。
不要在此输出推理步骤、校验过程或拟定好的整段对白。时间与来源由程序填写。
'''

FIELDS = ('feeling', 'intent', 'attention')


def _short(value):
    return value.strip()[:120] if isinstance(value, str) else ''


def make_reply_view(candidate, now, source):
    """Old-format models remain usable; only this fresh candidate is summarized."""
    raw = candidate.get('reply_perspective')
    if isinstance(raw, dict):
        view = {key: _short(raw.get(key)) for key in FIELDS}
    else:
        view = {'feeling': _short(candidate.get('内心活动')),
                'intent': _short(candidate.get('近期目标')), 'attention': ''}
    if not any(view.values()):
        return None
    return dict(view, timestamp=int(now * 1000), source=source)


def current_reply_view(state, now):
    raw = state.get('reply_perspective')
    if not isinstance(raw, dict) or state.get('scene_status') != 'ready' or state.get('state_source') == 'fallback':
        return None
    stamp = raw.get('timestamp')
    if type(stamp) not in (int, float) or not math.isfinite(stamp):
        return None
    if not 0 <= now * 1000 - stamp <= 1800 * 1000:
        return None
    view = {key: _short(raw.get(key)) for key in FIELDS}
    if not any(view.values()) or raw.get('source') not in ('dialogue', 'idle', 'startup'):
        return None
    return dict(view, timestamp=stamp, source=raw['source'])
