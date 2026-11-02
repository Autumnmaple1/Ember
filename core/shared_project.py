"""A small, deterministic shared activity. Cards describe a fictional exercise.

Choices never move people, establish contact, or claim real-world observation.
The LLM may discuss a page; only these rules advance it.
"""
import copy
import uuid


class ProjectConflict(ValueError):
    pass


INTRO = {
    'title': '窗边的一页',
    'subtitle': '和依鸣试做一本校园观察手册',
    'description': '两份材料，一次取舍。决定留下什么证据，也决定把什么问题留给下一次。',
    'boundary': '这是用虚构材料进行的资料推演，不表示你们已经外出、同处一地或观察到真实鸟类。',
}

ROUTES = {
    'window': {
        'label': '从窗边速写开始', 'hint': '保留当时的细节；轮廓被枝叶遮挡，需要决定是否进一步核对。',
        'card': {'id': 'window-sketch', 'title': '材料 A · 枝间的轮廓',
            'text': '一张模拟速写里，树枝上有个小小的轮廓。旁注写着“尾部似乎在摆动”，但画面没有足够细节。',
            'source': '篇章提供的虚构速写', 'uncertainty': '轮廓的身份与尾部动作均未确认。'},
        'event': '准备落笔时，你们发现：“似乎”不能直接写成“看见了”。要花时间核对，还是留下别的细节？',
        'verified': {'id': 'window-check', 'title': '补充材料 · 连续两幅画面',
            'text': '补充画面里的轮廓一直停在原处，晃动来自前方的细枝。可确认的是枝叶遮挡和画面变化，仍不能判断物种。',
            'source': '篇章提供的模拟连续画面', 'uncertainty': '物种保持未知。'},
        'broader': {'id': 'window-detail', 'title': '补充材料 · 画面边缘',
            'text': '另一幅模拟速写留下了窗框、枝条与光斑的位置。可以把这一页写成“看见细节”，保留最初的疑问。',
            'source': '篇章提供的虚构环境速写', 'uncertainty': '没有新增身份或动作证据。'},
    },
    'archive': {
        'label': '从旧资料开始', 'hint': '更容易整理线索；两张卡的日期可能并不一致。',
        'card': {'id': 'archive-notes', 'title': '材料 B · 两张旧卡片',
            'text': '一张模拟卡片写着“树影里有鸣声”，另一张画着窗外的轮廓。扫描边缘裁掉了日期，无法确认它们是不是同一天。',
            'source': '篇章提供的虚构资料卡', 'uncertainty': '两条线索是否相关尚未确认。'},
        'event': '把两张卡放在一起后，画面与鸣声仿佛能拼成故事。但日期被裁掉了，要不要先找回它？',
        'verified': {'id': 'archive-check', 'title': '补充材料 · 完整页眉',
            'text': '完整的模拟页眉显示，两张卡相隔三天。它们可以并列展示，但不能作为同一次观察的联合证据。',
            'source': '篇章提供的虚构完整扫描', 'uncertainty': '两次材料之间没有已确认的因果关系。'},
        'broader': {'id': 'archive-detail', 'title': '补充材料 · 边注与构图',
            'text': '资料边上还留着不同的构图草稿。可以做成一页资料拼贴，并明确将两张卡分开标注。',
            'source': '篇章提供的虚构构图草稿', 'uncertainty': '日期和线索关联仍待核对。'},
    },
}

RESOLUTIONS = {
    'verify': {'label': '补充核对', 'cost': 2, 'hint': '用完剩下的调查步数，获得一份能修正原判断的材料。',
        'edition': '核对版', 'outcome': '这一页记录了怎样修正第一眼的判断，也保留了尚未解开的疑问。'},
    'broaden': {'label': '换一个观察角度', 'cost': 1, 'hint': '多留下一份环境或构图细节，原问题仍保持未知。',
        'edition': '细节版', 'outcome': '这一页从寻找答案转向收集细节；材料更丰富，结论仍然克制。'},
    'leave_open': {'label': '把疑问留在这一页', 'cost': 0, 'hint': '保留现有材料，不为缺少证据的问题补写答案。',
        'edition': '待续版', 'outcome': '这一页留下了清楚的问题，方便以后获得新材料时继续。'},
}


def project_view(project):
    if project['status'] == 'abandoned':
        return {'heading': '保留未完成的片段', 'event': '', 'choices': [], 'edition': '未完成',
            'outcome': '这次试作已经结束，现有材料仍在。可以另起一页，不需要补完它。'}
    phase = project['phase']
    route = ROUTES.get(project.get('route'))
    options = ROUTES if phase == 'route' else RESOLUTIONS if phase == 'investigate' else {}
    return {
        'heading': {'route': '先从哪份材料入手？', 'investigate': '这一次，怎样取舍？',
            'compose': '把经历整理成一页', 'finished': '这一页已经留下来了'}[phase],
        'event': route['event'] if route and phase == 'investigate' else '',
        'choices': [{'id': key, 'label': item['label'], 'hint': item['hint'],
            'cost': item.get('cost', 1)} for key, item in options.items()],
        'edition': RESOLUTIONS.get(project.get('resolution'), {}).get('edition'),
        'outcome': RESOLUTIONS.get(project.get('resolution'), {}).get('outcome'),
    }


def start_project(source, logical_time):
    if not source or source.get('role') != 'ai' or source.get('completion_status') != 'completed':
        raise ValueError('请先在对话中商量，并选择已完成的角色回复作为确认来源')
    return {'id': str(uuid.uuid4()), 'kind': 'window-page', 'version': 0,
        'title': INTRO['title'], 'status': 'active', 'phase': 'route', 'route': None,
        'resolution': None, 'budget': 3, 'cards': [], 'decisions': [], 'note': '',
        'source': copy.deepcopy(source), 'provenance': 'player_confirmed_fictional_activity',
        'started_at': logical_time, 'updated_at': logical_time}


def advance_project(project, action, *, choice='', title='', note='', logical_time=''):
    if action == 'abandon':
        if project['status'] not in ('active', 'paused'):
            raise ValueError('这份手册已经结束')
        project['status'] = 'abandoned'
    elif action == 'resume':
        if project['status'] != 'paused':
            raise ValueError('只有暂停的手册可以继续')
        project['status'] = 'active'
        project.pop('pause_reason', None)
    elif action == 'pause':
        if project['status'] != 'active':
            raise ValueError('当前手册无法暂停')
        project['status'] = 'paused'
        project['pause_reason'] = '由你暂时合上'
    elif action == 'edit':
        if project['status'] != 'completed':
            raise ValueError('请先完成这一页，再修改页边注')
        _write_page(project, title, note)
    else:
        if project['status'] != 'active':
            raise ValueError('请先继续这份手册')
        if action == 'choose':
            if project['phase'] == 'route' and choice in ROUTES:
                project['route'] = choice
                project['budget'] -= 1
                project['cards'].append(copy.deepcopy(ROUTES[choice]['card']))
                project['phase'] = 'investigate'
            elif project['phase'] == 'investigate' and choice in RESOLUTIONS:
                project['resolution'] = choice
                project['budget'] -= RESOLUTIONS[choice]['cost']
                if choice != 'leave_open':
                    card = ROUTES[project['route']]['verified' if choice == 'verify' else 'broader']
                    project['cards'].append(copy.deepcopy(card))
                project['phase'] = 'compose'
            else:
                raise ValueError('这个选择不属于当前步骤，请同步手册')
            project['decisions'].append({'choice': choice, 'world_time': logical_time})
        elif action == 'finish' and project['phase'] == 'compose':
            _write_page(project, title, note)
            project['phase'], project['status'] = 'finished', 'completed'
        else:
            raise ValueError('当前步骤不支持这个操作')
    project['version'] += 1
    project['updated_at'] = logical_time


def _write_page(project, title, note):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError('请为这一页填写 1–100 字的标题')
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError('页边注不能超过 1000 字')
    project['title'], project['note'] = title.strip(), note.strip()


def validate_project(project):
    """Fail closed on corrupt saves; cards and outcomes must match the rules."""
    if project.get('kind') != 'window-page' or not isinstance(project.get('id'), str):
        raise ValueError('手册类型或标识无效')
    if project.get('status') not in ('active', 'paused', 'completed', 'abandoned'):
        raise ValueError('手册状态无效')
    phase, route, resolution = project.get('phase'), project.get('route'), project.get('resolution')
    if phase not in ('route', 'investigate', 'compose', 'finished'):
        raise ValueError('手册阶段无效')
    if (phase == 'finished') != (project['status'] == 'completed'):
        raise ValueError('手册完成状态不一致')
    expected_cards, expected_choices, budget = [], [], 3
    if phase != 'route':
        if route not in ROUTES:
            raise ValueError('手册缺少有效路线')
        expected_cards.append(ROUTES[route]['card'])
        expected_choices.append(route)
        budget -= 1
    elif route is not None:
        raise ValueError('手册路线与阶段不一致')
    if phase in ('compose', 'finished'):
        if resolution not in RESOLUTIONS:
            raise ValueError('手册缺少有效取舍')
        expected_choices.append(resolution)
        budget -= RESOLUTIONS[resolution]['cost']
        if resolution != 'leave_open':
            expected_cards.append(ROUTES[route]['verified' if resolution == 'verify' else 'broader'])
    elif resolution is not None:
        raise ValueError('手册取舍与阶段不一致')
    decisions = project.get('decisions')
    if (project.get('cards') != expected_cards or project.get('budget') != budget or
            not isinstance(decisions, list) or any(not isinstance(x, dict) for x in decisions) or
            [x.get('choice') for x in decisions] != expected_choices):
        raise ValueError('手册材料或选择记录不一致')
    if type(project.get('version')) is not int or project['version'] < len(expected_choices):
        raise ValueError('手册版本无效')
    source = project.get('source')
    if (not isinstance(source, dict) or source.get('role') != 'ai' or not source.get('message_id') or
            source.get('completion_status') != 'completed'):
        raise ValueError('手册缺少已完成的确认对话来源')
    _write_page(copy.deepcopy(project), project.get('title'), project.get('note'))
