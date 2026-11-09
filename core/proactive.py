"""Delivery permissions are separate from fictional scene facts."""
import json
import math
import time
from pathlib import Path

WEB_PROACTIVE_RULES = """
【网页主动陪伴】本次主动消息发送到 Ember 网页，由用户的主动发言/静默设置授权。
网页联系不要求已知用户位置、面对面或交换微信/电话；旧模板中未加联系方式的抑制不适用于这个网页渠道。
仍需有值得说的具体内容，遵守睡眠、专注和不打扰要求。不知道用户在做什么，不替用户回应。
交流方式未知只限制身体动作与共同空间描写，不禁止网页内交谈；不能声称已发送微信、拨打电话或推送到手机。
"""


def web_companion(bus):
    return getattr(bus, 'proactive_channel', None) == 'web'


def delivery_rules(bus):
    return WEB_PROACTIVE_RULES if web_companion(bus) else ''


def awaiting_user(history):
    history = [row for row in history if isinstance(row, dict)]
    last_user = next((i for i in range(len(history)-1, -1, -1) if history[i].get('role') == 'user'), -1)
    tail = [row for row in history[last_user+1:] if row.get('role') == 'assistant'
            and isinstance(row.get('content'), str) and row['content'].strip()]
    return any(row.get('turn_source') == 'proactive' for row in tail) or len(tail) >= (2 if last_user >= 0 else 1)


class ProactivePolicy:
    """Real-time attempt cooldown; no repeated monologues while waiting for a user."""
    def __init__(self, minimum_interval=600, path='data/state/proactive.json', clock=time.time):
        self.minimum_interval, self.path, self.clock = minimum_interval, Path(path), clock
        self.last_attempt = None
        self.error = None
        self._record = {}
        if self.path.exists():
            try:
                self._record = json.loads(self.path.read_text(encoding='utf-8'))
                value = self._record['last_attempt_at']
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('Invalid proactive clock')
                self.last_attempt = value
            except (OSError, ValueError, KeyError, TypeError):
                self.error = '主动发言计时记录不可用，已保留记录并暂停主动发言'

    def check(self, history):
        if self.error:
            return {'allowed': False, 'reason': 'storage_error', 'retry_after_seconds': 0}
        if awaiting_user(history):
            return {'allowed': False, 'reason': 'awaiting_user', 'retry_after_seconds': 0}
        if self.last_attempt is not None:
            elapsed = max(0, self.clock() - self.last_attempt)
            remaining = max(0, self.minimum_interval - elapsed)
            if remaining:
                return {'allowed': False, 'reason': 'cooldown', 'retry_after_seconds': math.ceil(remaining)}
        return {'allowed': True, 'reason': 'ready', 'retry_after_seconds': 0}

    def reserve(self, history):
        if not self.check(history)['allowed']:
            return False
        stamp = self.clock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        record = dict(self._record, last_attempt_at=stamp)
        record.setdefault('version', 1)
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.replace(self.path)
        self.last_attempt = stamp
        return True
