"""Durable, event-owned delivery budget. Model importance never bypasses it."""
import hashlib
import json
import math
from core.proactive import ProactivePolicy, awaiting_user


def ordinary_offer(history):
    latest = next((row for row in reversed(history) if row.get('role') == 'user'), {})
    identity = latest.get('message_id') or hashlib.sha256(str(latest.get('content', 'initial')).encode()).hexdigest()
    return {'id': 'conversation:' + identity, 'topic': 'conversation:' + identity,
            'kind': 'ordinary', 'summary': '沿用最近话题，有新内容才说，不重复解释旧反馈。'}


class EventProactivePolicy(ProactivePolicy):
    def __init__(self, *args, minimum_gap=120, hourly_limit=3, reminder_limit=6, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum_gap, self.hourly_limit, self.reminder_limit = minimum_gap, hourly_limit, reminder_limit
        self.data = {'version': 2, 'attempts': [], 'seen': {}, 'topics': {}}
        self.last_decision = None
        if self.path.exists() and not self.error:
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if raw.get('version', 1) == 2:
                    if (not isinstance(raw.get('attempts'), list) or not isinstance(raw.get('seen'), dict)
                            or not isinstance(raw.get('topics'), dict)):
                        raise ValueError()
                    for row in raw['attempts']:
                        if (not isinstance(row, dict) or type(row.get('at')) not in (int, float)
                                or not math.isfinite(row['at']) or row.get('kind') not in ('ordinary', 'role_event', 'shared_timer', 'reminder')):
                            raise ValueError()
                    if any(type(value) not in (int, float) or not math.isfinite(value) for value in raw['topics'].values()):
                        raise ValueError()
                    if any(not isinstance(key, str) or value not in ('reserved', 'delivered', 'silent', 'failed', 'interrupted')
                           for key, value in raw['seen'].items()):
                        raise ValueError()
                    self.data = raw
                elif raw.get('version', 1) != 1:
                    raise ValueError()
            except (ValueError, TypeError, OSError):
                self.error = '主动事件投递记录不可用，已保留记录并暂停投递'

    def check(self, history, offer=None, *, online=True, quiet=False, sleeping=False, paused=False):
        offer = offer or ordinary_offer(history)
        now = self.clock()
        reason, wait = 'ready', 0
        if self.error:
            reason = 'storage_error'
        elif paused:
            reason = 'paused'
        elif quiet:
            reason = 'quiet'
        elif sleeping:
            reason = 'sleeping'
        elif not online:
            reason = 'offline'
        elif offer.get('kind') not in ('ordinary', 'role_event', 'shared_timer', 'reminder') or not offer.get('id') or not offer.get('topic'):
            reason = 'invalid_event'
        elif offer['id'] in self.data['seen']:
            reason = 'already_attempted'
        else:
            # The legacy unanswered rule remains for ordinary chat and character news.
            unanswered = awaiting_user(history)
            reminder = offer['kind'] == 'reminder'
            minimum = min(30, self.minimum_gap) if reminder else self.minimum_gap
            attempts = [row for row in self.data['attempts'] if now - row['at'] < 3600]
            group = [row for row in attempts if (row['kind'] == 'reminder') == reminder]
            if unanswered and offer['kind'] in ('ordinary', 'role_event'):
                reason = 'awaiting_user'
            elif self.last_attempt is not None and now - self.last_attempt < minimum:
                reason, wait = 'minimum_gap', math.ceil(minimum - max(0, now - self.last_attempt))
            elif len(group) >= (self.reminder_limit if reminder else self.hourly_limit):
                reason, wait = 'hourly_budget', math.ceil(max(0, 3600 - (now - min(row['at'] for row in group))))
            elif offer['topic'] in self.data['topics'] and now - self.data['topics'][offer['topic']] < 1800:
                reason, wait = 'same_topic', math.ceil(max(0, 1800 - (now - self.data['topics'][offer['topic']])))
            elif offer['kind'] == 'ordinary' and self.last_attempt is not None and now - self.last_attempt < self.minimum_interval:
                reason, wait = 'conversation_cooldown', math.ceil(self.minimum_interval - max(0, now - self.last_attempt))
        result = {'allowed': reason == 'ready', 'reason': reason, 'retry_after_seconds': wait,
                  'event_id': offer.get('id'), 'kind': offer.get('kind')}
        self.last_decision = result
        return result

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
        self.data = data

    def reserve(self, history, offer=None, **gates):
        offer = offer or ordinary_offer(history)
        if not self.check(history, offer, **gates)['allowed']:
            return False
        now = self.clock()
        data = json.loads(json.dumps(self.data))
        data['version'], data['last_attempt_at'] = 2, now
        data['attempts'] = [row for row in data['attempts'] if now - row['at'] < 3600]
        data['attempts'].append({'id': offer['id'], 'topic': offer['topic'], 'kind': offer['kind'], 'at': now, 'status': 'reserved'})
        data['seen'][offer['id']] = 'reserved'
        data['topics'] = {key: at for key, at in data['topics'].items() if now - at < 1800}
        data['topics'][offer['topic']] = now
        self._save(data)
        self.last_attempt = now
        return True

    def finish(self, event_id, status):
        if status not in ('delivered', 'silent', 'failed', 'interrupted'):
            raise ValueError('无效主动投递结果')
        data = json.loads(json.dumps(self.data))
        if event_id not in data['seen']:
            return
        data['seen'][event_id] = status
        for row in data['attempts']:
            if row['id'] == event_id:
                row['status'] = status
        self._save(data)

    def status(self):
        return {'version': 'events_v1', 'last_decision': self.last_decision, 'error': self.error,
                'recent_attempts': self.data['attempts'][-6:], 'seen_events': len(self.data['seen']),
                'minimum_gap_real_seconds': self.minimum_gap, 'hourly_limit': self.hourly_limit,
                'reminder_hourly_limit': self.reminder_limit}
