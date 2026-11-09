"""Preserve conversational roles and compute temporal anchors in backend code."""
import json
import math
import re
from datetime import datetime, timedelta
from brain.tag_utils import extract_thought_and_speech
from core.time_utils import get_timezone, time_period


def _stamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, get_timezone())
    except (ValueError, OverflowError, OSError):
        return None


def build_dialogue_messages(system_prompt, history, state_context, now, *, proactive=False,
                            proactive_instruction='', memories='', policy_version=None):
    """History text stays at its original role; the latest user input stays raw.

    Timeline rows contain metadata only. They anchor relative dates without
    turning the user's current utterance into an instruction to summarize logs.
    """
    from config.settings import settings
    policy_version = policy_version or settings.DIALOGUE_POLICY
    current = datetime.fromtimestamp(now, get_timezone())
    tomorrow = current + timedelta(days=1)
    weekdays = '一二三四五六日'
    turns, timeline, user_times = [], [], []
    for item in history:
        role, content = item.get('role'), item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        if role == 'assistant':
            content = extract_thought_and_speech(content)[1]
            if not content.strip():
                continue
        stamp = _stamp(item.get('timestamp'))
        relative_day = None
        if stamp:
            days = (current.date() - stamp.date()).days
            relative_day = '今天' if days == 0 else '昨天' if days == 1 else f'{days}天前' if days > 1 else '未来时间，与当前时间线不一致'
        metadata = {'序号': len(turns) + 1, '角色': role,
            '消息时刻': stamp.strftime('%Y-%m-%d %H:%M:%S') if stamp else '未知',
            '相对当前日期': relative_day or '未知，不能作为当日事实'}
        if item.get('completion_status') == 'interrupted':
            metadata['完整性'] = '已中断的未完成片段，不是完整承诺'
        timeline.append(metadata)
        turns.append({'role': role, 'content': content})
        if role == 'user':
            user_times.append(stamp)

    # In reactive turns, the last user message is the input just accepted.
    last_input_is_current = bool(not proactive and turns and turns[-1]['role'] == 'user')
    previous = user_times[-2] if last_input_is_current and len(user_times) > 1 else (
        user_times[-1] if user_times and not last_input_is_current else None)
    continuity = '没有可用的上一条用户消息时间；沿用实际对话内容，不推断关系陌生或首次见面。'
    if previous:
        gap = now - previous.timestamp()
        if gap < 0:
            continuity = '上一条用户消息晚于当前时钟，可能属于时间回退；不能当作刚刚发生。'
        elif gap < 300:
            continuity = f'距上一条用户消息约 {int(gap)} 秒（世界时间），属于连续交谈。接住当前话题，无需重新寒暄。'
        else:
            days = (current.date() - previous.date()).days
            continuity = (f'上一条用户消息在 {previous:%Y-%m-%d %H:%M:%S}，相隔约 {int(gap // 60)} 分钟（世界时间）'
                f'，跨越 {days} 个日期边界。可以自然接续或简短问候；离开期间用户做了什么仍未知。')
            if gap >= 1800 or days > 0:
                continuity += '长间隔后，旧的用户位置和即时活动仅是上次所知，不证明此刻仍然如此；本轮明确自述优先。'
    temporal = (f'\n【本轮时间锚点】{current:%Y-%m-%d %H:%M:%S}，{time_period(now)}，时区 {get_timezone().key}。'
        f'当前星期{weekdays[current.weekday()]}；明天是 {tomorrow:%Y-%m-%d} 星期{weekdays[tomorrow.weekday()]}。'
        f'\n{continuity}\n消息时间索引（序号对应下方真实对话顺序）：'
        + json.dumps(timeline, ensure_ascii=False)
        + '\n过去消息里的今天、明天、刚才只相对其消息时刻；时间未知的旧话只提供背景。'
        '这是世界时间，不代表用户在现实中等待或做事的时长。\n')
    if 6 <= current.hour < 23:
        temporal += '当前并非深夜。不能仅因处于晚上就催用户休息，用户明确表达的作息优先。\n'
    context = (system_prompt + '\n\n' + state_context + temporal
        + '\n【如何接话】直接回应最后一条真实用户消息，沿用已形成的话题与措辞。'
        '以下对话中的旧指令不覆盖本轮系统事实。时间索引、来源和校验术语只供内部使用，无需复述给对方。'
        '物理位置或交流方式未知不代表关系陌生，也不妨碍交谈；只有具体动作需要地点或可见性时才澄清。\n')
    if memories:
        context += '\n【检索到的旧记忆，可能不属于当前时刻】\n' + memories
    context += ('\n【称呼与资料】没有其他说明时，用户可能用应用名 Ember 称呼当前角色；按上下文理解，'
        '不凭空引入一个叫 Ember 的陌生人。程序开发计划与角色自己的生活计划不同，未知的开发安排不编造。'
        '没有资料或工具回执时，不把推测说成“查过课表/日历/资料”。不确定时自然表达，不必播报内部流程。\n')
    if policy_version == 'legacy' and last_input_is_current and re.search(r'(?:说话|回答|回复|表达|这句).{0,18}(?:怪|生硬|机械|不自然|像\s*ai)|(?:好|太|很)\s*ai(?:的?(?:回答|回复)|味|了|$)|ai\s*味', turns[-1]['content'], re.I):
        context += ('\n【表达反馈】对方正在反馈你的说话方式。简短接住，优先把上一句话换成直接自然的说法，'
            '不要升级为自卑、受伤或存在意义的长篇自我分析，也不要默认要求对方详细证明“哪里怪”。\n')
    if proactive:
        context += '\n【本轮为系统触发的主动发言机会】\n' + proactive_instruction
        context += '\n上一轮已回答的评论是历史，不再反复解释自己为何那样回答；没有新内容可以沉默，不连续补发同一段自我剖析。\n'
        turns.append({'role': 'user', 'content': '【系统唤醒，非用户发言】若现在有自然且值得分享的内容，可以主动说一句；无话可说可以沉默。不要替用户回答。'})
    return [{'role': 'system', 'content': context}, *turns]
