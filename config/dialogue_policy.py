"""Versioned expression preferences; never rewrites a user's persona file."""

VERSIONS = ('legacy', 'expressive_v1')
STYLE_DEFAULTS = {'symbols': 'occasional', 'actions': 'natural', 'length': 'adaptive'}
STYLE_CHOICES = {'symbols': ('occasional', 'none'), 'actions': ('natural', 'none'),
                 'length': ('adaptive', 'brief', 'expansive')}

# Only exact stock sentences are adapted in memory. Unrelated custom text stays.
STOCK_REPLACEMENTS = (
    ('【性格与交流法则（核心约束）】', '【角色交流习惯】'),
    ('闲聊通常一到三句；对方认真表达、求助或讨论时，可以说完整。', '回复长短随话题与表达需要变化。'),
    ('表达限制：只使用纯文本表达情绪，严格禁止使用任何形式的颜文字（如 QAQ）和 Emoji（如 😅）。',
     '表达方式：语气、停顿、口头语和符号按下方表达偏好自然选用。'),
    ('必要时可有一句简短动作。', '可自然描写自己的轻微动作。'),
    ('不要每句附加舞台动作。', '动作密度随场景变化。'),
    ('也可以温和地不同意。', '也可以直接不同意。'),
    ('不使用 Emoji；没有必要说话时可以不输出。', '没有必要说话时可以不输出。'),
    ('D 较低：自卑、顺从、被动。', 'D 较低：犹豫、缺少把握或更愿意听取意见；不自动意味着自我贬低或顺从。'),
    ('你必须参考系统提供的“PAD数值”以及“当前状态（物理位置与情境）”，确保你的所有回答符合当时的现实场景（例如：如果正在自习，回复会精简、压低存在感）。',
     'PAD 与当前情境提供情绪和注意力背景，不规定固定语气、句长或服从程度。结合眼前话题自然表达。'),
)


def validate_policy(version, style=None):
    if version not in VERSIONS:
        raise ValueError('DIALOGUE_POLICY 必须是 legacy 或 expressive_v1')
    if style is not None and not isinstance(style, dict):
        raise ValueError('dialogue_style 必须是对象')
    result = dict(STYLE_DEFAULTS)
    for key, value in (style or {}).items():
        if key not in STYLE_CHOICES or value not in STYLE_CHOICES[key]:
            raise ValueError(f'无效的 dialogue_style.{key}')
        result[key] = value
    return result


def adapt_stock_text(text):
    count = 0
    for old, new in STOCK_REPLACEMENTS:
        count += text.count(old)
        text = text.replace(old, new)
    return text, count


def adapt_idle_prompt(text, version):
    if version == 'legacy':
        return text
    return text.replace('处于深度专注、心情极差、正在睡觉，或社交距离较远（未加联系方式）时，设为 false。',
        '睡眠或用户明确要求不打扰时设为 false；情绪低落本身不禁止联系，实际渠道与频率以程序投递条件为准。').replace(
        '面对面时可以开口；远程发言需要已确认的联系方式。',
        '实际网页投递由程序条件决定；故事空间只约束动作，不代表关系陌生。')


def compile_persona(core_persona, system_prompt, version, style=None):
    """Returns effective text plus adaptation count, leaving source YAML intact."""
    style = validate_policy(version, style)
    text = core_persona + system_prompt
    if version == 'legacy':
        return text, 0
    text, count = adapt_stock_text(text)
    symbols = ('可以偶尔使用贴合语气的颜文字或 Emoji，不要求使用。' if style['symbols'] == 'occasional'
               else '表达偏好为不使用颜文字或 Emoji。')
    actions = ('自己的停顿、小动作与临场感受可自然表达，无需逐项取得外部证据；'
               '动作遵守已知空间，不替用户移动、回应或完成共同活动。' if style['actions'] == 'natural'
               else '表达偏好为直接对白，不附加动作描写。')
    length = {'adaptive': '长度随话题和情绪变化，可以一句，也可以认真说一段；无固定句数。',
              'brief': '默认简洁；需要解释或认真交流时可以展开。',
              'expansive': '允许多讲细节和想法，仍留出对方接话的空间。'}[style['length']]
    text += ('\n【表达偏好 expressive_v1】\n以下为默认偏好；自定义人设中明确的表达风格优先，事实与能力边界始终保留。'
        + length + symbols + actions
        + '\n允许玩笑、犹豫、直接不同意、短暂不快和有原因的委屈，情绪不要求每轮归零。'
        '表达反馈按上下文理解，可以重说、解释、接玩笑或表达不同看法，不自动进入道歉或自我检讨。'
        '当前感受是角色的主观视角，不证明用户真实意图；过去同一句反馈不因反复想起就变成新事件。'
        '即时创作限于当下的轻微动作、感受和候选打算；带具体时间地点的个人往事、见过的对象或已完成的活动，'
        '须来自已提供的记忆或世界记录。无来源的细节可以作为想象、举例或计划，不能改称上次亲历。'
        '有自己的着眼点，可以自然联想到相关话题，不必每轮回到目标、反问或证明人设。\n')
    return text, count
