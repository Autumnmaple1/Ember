"""Offers derived only from committed world/player records, never model priority."""
from core.proactive_events import ordinary_offer


def offers(state, experience, history, now):
    result = []
    for item in experience.get('agreements', []):
        due = item.get('reminder_at')
        if (item.get('status') == 'confirmed' and item.get('notification_enabled') is True
                and type(due) in (int, float) and 0 <= now - due <= 900):
            key = f"reminder:{item['id']}:{int(due * 1000)}"
            title = item['title'].replace('<', '＜').replace('>', '＞')[:300]
            result.append({'id': key, 'topic': key, 'kind': 'reminder',
                'summary': f'你开启的网页提醒到时间了：{title}', 'due': due})
    result.sort(key=lambda row: row['due'])
    for item in experience.get('activities', []):
        if (item.get('status') == 'running' and item.get('shared') is True
                and item.get('elapsed_seconds', 0) >= item.get('target_seconds', float('inf'))):
            key = f"shared:{item['id']}:timer"
            result.append({'id': key, 'topic': key, 'kind': 'shared_timer',
                'summary': '用户明确开启的共同自习计时达到设定时长；可以问是否歇一会儿，不代表用户完成了作业。'})
    for event in reversed((state.get('role_continuity') or {}).get('events', [])):
        if (event.get('kind') == 'completed' and event.get('origin') == 'online_simulation'
                and event.get('timeline_status') != 'future_snapshot' and 0 <= now - event['at'] <= 900):
            result.append({'id': event['id'], 'topic': 'notes:' + event['activity_id'], 'kind': 'role_event',
                'summary': '角色自己的虚构活动进展：' + event['description'] + '。只属于角色，不代表共同活动。'})
    result.append(ordinary_offer(history))
    return result
