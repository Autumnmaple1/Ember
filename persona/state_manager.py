"""Serialize scene preparation, idle evolution, and archive operations."""
import copy
import json
import logging
import random
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import EventBus, Event
from core.time_utils import to_epoch, time_period
from memory.memory_process import Hippocampus
from memory.short_term import ShortTermMemory
from tools.processor import ToolCallProcessor
from persona.scene import normalize_scene, validate_scene, has_time_conflict
from memory.journal import append_log

logger = logging.getLogger(__name__)

SCENE_RULES = """
【当前事实与时空约束】
系统提供的当前时间、日期和时段是唯一时间来源，禁止覆盖。客观情境只写位置与活动，不重复日期、时刻或时段。
历史有时间戳时，今天/明天/晚安属于该消息发生时；时间未知的历史只能提供背景，不能证明当前场景。
必须输出完整状态 JSON，包含 P/A/D、客观情境、近期综合轨迹、内心活动、近期目标，以及 scene：
{"character_location":字符串或null,"user_location":字符串或null,
"communication_mode":"face_to_face"或"remote"或"unknown","remote_contact_confirmed":布尔值}。
用户位置、交流方式、远程联系方式仅可依据本轮用户明确表达更新。更新时在 JSON 最外层输出 scene_evidence，值必须是本轮用户输入完整原文字符串；
新位置使用引用中出现的地点原词。没有证据保持未知或原值。双方位置确实相同时才能 face_to_face；
本轮用户明确自述是新的事实，不需要旧记忆证明；记忆检索未命中不能否定本轮位置或联系方式声明。
remote 必须有已确认的远程联系方式。不得从角色位置猜用户位置，不得虚构用户行动或共同经历。
空闲/离线时可以推演角色自己的活动，但不代表确实发生；用户位置、联系方式不可推演。
近期目标中的约定必须有明确且带时间的证据；旧历史或旧目标不能证明今天仍有约定。
这是回复前更新，不是回复后的总结。不得预先编造角色本轮已经说过的话、同意或完成的事情。
用户没有描述场景变化时，保持现有位置和交流方式；不要每轮重建初见或用“确认位置/关系”替换原有目标。
保留未完成的具体角色意图，只有事件、时间或用户输入确实改变它时才调整。未知空间不等于关系陌生。
"""


class StateManager:
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="state_mgr")

    def __init__(self, event_bus: EventBus, hippocampus: Hippocampus,
                 short_term_memory: ShortTermMemory, tool_processor=None):
        self.event_bus = event_bus
        self.hippocampus = hippocampus
        self.short_term_memory = short_term_memory
        self.llm_client = LLMClient()
        self.tool_processor = tool_processor or ToolCallProcessor.create_with_memory_tool(hippocampus)
        self.current_state = copy.deepcopy(settings.STATE)
        self.current_state['scene'] = normalize_scene(self.current_state)
        self._thinking_lock = threading.Lock()
        self._thinking = False
        self.is_sleeping = False
        self.dialogue_count = 0
        self.ready = False
        self.last_error = None
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
        self.last_interaction_logical_time = event_bus.logical_now
        self.last_interaction_real_time = time.monotonic()
        event_bus.subscribe("system.tick", self._on_tick)

    @property
    def is_thinking(self):
        with self._thinking_lock:
            return self._thinking

    @is_thinking.setter
    def is_thinking(self, value):
        with self._thinking_lock:
            self._thinking = value

    def _get_logical_now(self):
        return self.event_bus.logical_now

    def _format_logical_time(self, value, fmt="%Y-%m-%d %H:%M:%S"):
        return self.event_bus.format_logical_time(value, fmt)

    def _get_floating_timeout(self, timeout):
        return random.uniform(timeout * 0.9, timeout * 1.1)

    def _format_duration(self, seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 86400}天{seconds % 86400 // 3600}小时{seconds % 3600 // 60}分钟"

    def _get_idle_info(self, now):
        return {"idle_duration": self._format_duration(now - self.last_interaction_logical_time),
                "current_time": self._format_logical_time(now),
                "old_state": json.dumps(self.current_state, ensure_ascii=False)}

    def _ask_llm_with_tools(self, system_prompt, user_prompt, call_type="state_update", max_iterations=1):
        messages = [
            {"role": "system", "content": self.tool_processor.build_system_prompt_with_tools(system_prompt + SCENE_RULES)},
            {"role": "user", "content": user_prompt},
        ]
        response = self.llm_client.one_chat(settings.SMALL_LLM, messages, timeout=30, call_type=call_type)
        if not response:
            return ""
        result = self.tool_processor.process_llm_output(
            response, execute=True, caller=f"StateManager[{call_type}]")
        if result["has_tool_calls"]:
            messages.extend([
                {"role": "assistant", "content": response},
                {"role": "user", "content": result["results_text"] + "\n请消费这些结果输出完整状态 JSON。禁止再次调用工具。"},
            ])
            response = self.llm_client.one_chat(settings.SMALL_LLM, messages, timeout=30, call_type=call_type)
        return self.tool_processor.remove_tool_calls(response or "")

    def _async_log(self, filename, content):
        # This log is consumed by the hippocampus: order and archive consistency matter.
        append_log(filename, content)

    def _update_state(self, new_state, logical_now=None):
        data = copy.deepcopy(new_state)
        data["对应时间"] = self._format_logical_time(logical_now if logical_now is not None else self._get_logical_now())
        file = Path("./config/state.json")
        file.parent.mkdir(parents=True, exist_ok=True)
        temp = file.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(file)
        self.current_state = data
        self._async_log('./config/chat_history.log', json.dumps({'source_type': 'role_state',
            'state_source': data.get('state_source', 'legacy'), 'timestamp': data['对应时间'],
            'content': self.state_zip}, ensure_ascii=False))
        self.event_bus.publish(Event("state.update", {"new_state": copy.deepcopy(data)}))

    def _fallback(self, now, reason, offline=False):
        state = copy.deepcopy(self.current_state)
        location = state['scene']['character_location'] or '位置待确认'
        state['客观情境'] = f"{location}；当前活动待确认"
        # Preparation failure is a service condition, not a new character goal.
        # Keep same-session intentions; expired/offline intentions stay unknown.
        if offline or has_time_conflict(self.current_state.get('客观情境', ''), now):
            state['内心活动'] = ''
            state['近期目标'] = ''
        if offline:
            state['scene']['user_location'] = None
            state['scene']['communication_mode'] = 'unknown'
            state['近期综合轨迹'] = '上次状态已保存 -> 离线期间未知 -> 当前活动待确认'
        state['state_source'] = 'fallback'
        state['scene_status'] = 'needs_confirmation'
        self.last_error = reason
        self._update_state(state, now)
        self.ready = True

    def _generate_state(self, prompt, now, user_text='', allow_inferred=False, call_type='state_update', offline=False, update_pad=True):
        for attempt in range(2):
            response = self._ask_llm_with_tools(settings.CORE_PERSONA, prompt, call_type=call_type)
            try:
                candidate = self.llm_client._extract_json(response)
                if not isinstance(candidate, dict):
                    raise ValueError("模型未返回状态对象")
                if 'scene' not in candidate and any(key in candidate for key in ('character_location', 'user_location', 'communication_mode', 'remote_contact_confirmed')):
                    candidate['scene'] = normalize_scene(candidate)
                    for key in ('character_location', 'user_location', 'communication_mode', 'remote_contact_confirmed'):
                        candidate.pop(key, None)
                state = copy.deepcopy(self.current_state)
                state.update(candidate)
                if not update_pad:
                    for key in ('P', 'A', 'D'):
                        state[key] = self.current_state.get(key, 5)
                for key in ('客观情境', '近期综合轨迹', '内心活动', '近期目标'):
                    if not isinstance(state.get(key), str):
                        raise ValueError(f"状态字段无效: {key}")
                for key in ('P', 'A', 'D'):
                    number = state.get(key, 5)
                    if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 10:
                        raise ValueError(f"PAD 值无效: {key}")
                if has_time_conflict(state['客观情境'], now):
                    raise ValueError("客观情境的时间或时段与系统时钟冲突")
                state['scene'] = validate_scene(candidate, self.current_state, user_text, allow_inferred)
                proposed = normalize_scene(candidate)
                previous_scene = normalize_scene(self.current_state)
                protected = ('user_location', 'communication_mode', 'remote_contact_confirmed')
                if user_text and any(proposed[key] != previous_scene[key] and state['scene'][key] != proposed[key] for key in protected):
                    raise ValueError('场景更新缺少可核对的 scene_evidence，请在最外层逐字引用本轮完整用户输入')
                state.pop('scene_evidence', None)
                if offline:
                    state['scene']['user_location'] = None
                    state['scene']['communication_mode'] = 'unknown'
                state['state_source'] = 'inferred' if allow_inferred else 'interaction'
                state['scene_status'] = 'ready'
                pulse = state.pop('action_pulse', {})
                self._update_state(state, now)
                self.ready, self.last_error = True, None
                return pulse
            except (ValueError, TypeError) as exc:
                logger.warning("场景校验失败: %s", exc)
                prompt += f"\n上次输出校验失败：{exc}。请修正，只输出完整状态 JSON。"
        self._fallback(now, '状态生成或时空校验失败', offline)
        return {}

    def prepare_startup(self):
        with self.event_bus.operation_lock:
            self.is_thinking = True
            try:
                now = self._get_logical_now()
                try:
                    old_time = to_epoch(self.current_state.get('对应时间', now))
                except (ValueError, TypeError):
                    old_time = now
                gap = now - old_time
                if settings.STATE_IS_DEFAULT:
                    gap = 0
                conflict = has_time_conflict(self.current_state.get('客观情境', ''), now)
                if abs(gap) > 300 or conflict:
                    prompt = (
                        f"启动场景准备。当前准确时间 {self._format_logical_time(now)}，时段 {time_period(now)}。"
                        f"上次状态距当前 {self._format_duration(abs(gap))}，时间方向 {'向前' if gap >= 0 else '回退'}。\n"
                        f"上次状态仅为历史背景：{json.dumps(self.current_state, ensure_ascii=False)}\n"
                        "离线期间用户位置、行为与共同经历均未知。只推演角色自己的当前活动；"
                        "不得把旧状态中的明天、夜间或约定直接当作今天。"
                    )
                    self._generate_state(prompt, now, allow_inferred=True, call_type='startup_scene', offline=True)
                else:
                    self.current_state['scene_status'] = 'ready'
                    self._update_state(self.current_state, now)
                    self.ready = True
            except Exception as exc:
                logger.exception("启动场景准备失败")
                self._fallback(self._get_logical_now(), str(exc), offline=True)
            finally:
                self.last_interaction_logical_time = self._get_logical_now()
                self.last_interaction_real_time = time.monotonic()
                self.is_thinking = False

    def prepare_dialogue(self, user_text):
        with self.event_bus.operation_lock:
            self.is_thinking = True
            self.dialogue_count += 1
            try:
                now = self._get_logical_now()
                interval = max(1, settings.STATE_UPDATE_INTERVAL)
                history = self.short_term_memory.get_memory()['history']
                prompt = (
                    f"这是回复前的场景更新，尚未生成本轮回答。\n当前准确时间：{self._format_logical_time(now)}"
                    f"；时段：{time_period(now)}。\n当前状态：{json.dumps(self.current_state, ensure_ascii=False)}"
                    f"\n带时间的历史：{json.dumps(history, ensure_ascii=False)}"
                    f"\n本轮用户明确输入：{user_text}\n{settings.STATE_UPDATE_PROMPT}"
                    "\n日常接话可以沿用当前目标；状态更新故障不代表角色想盘问地点或疏远对方。"
                    "\n必须输出状态对象，其中 scene 字段包含双方位置、communication_mode 和 remote_contact_confirmed；scene_evidence 放在最外层。"
                )
                if self.dialogue_count % interval:
                    prompt += "\n本轮保持 P/A/D 原值，仅更新时间、空间和活动。"
                self._generate_state(prompt, now, user_text=user_text,
                    update_pad=self.dialogue_count % interval == 0)
            except Exception as exc:
                logger.exception("回复前状态准备失败")
                self._fallback(self._get_logical_now(), str(exc))
            finally:
                self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
                self.last_interaction_logical_time = self._get_logical_now()
                self.last_interaction_real_time = time.monotonic()
                self.is_thinking = False

    def _on_llm_state_update(self, event):
        # Compatibility entry point; Brain now invokes preparation BEFORE speaking.
        self.prepare_dialogue(event.data.get('text', ''))

    def _on_tick(self, event):
        if self.event_bus.paused or self.event_bus.world_paused or getattr(self.event_bus, 'archive_active', False) or not self.ready:
            return
        if not self.event_bus.operation_lock.acquire(blocking=False):
            return
        try:
            elapsed = time.monotonic() - self.last_interaction_real_time
            if elapsed > self._get_floating_timeout(self.state_update_timeout):
                self.state_update_timeout = min(self.state_update_timeout * 1.5, settings.STATE_IDLE_MAX_TIMEOUT)
                self._update_state_due_to_idle(self._get_logical_now())
        finally:
            self.event_bus.operation_lock.release()

    def _update_state_due_to_idle(self, now):
        self.is_thinking = True
        try:
            info = self._get_idle_info(now)
            prompt = (
                f"空闲推演，非已确认事件。当前准确时间 {info['current_time']}；时段 {time_period(now)}；"
                f"距离上次更新 {info['idle_duration']}。\n历史状态：{info['old_state']}\n"
                f"带时间历史：{json.dumps(self.short_term_memory.get_memory()['history'], ensure_ascii=False)}"
                f"\n{settings.IDLE_STATE_UPDATE_PROMPT}"
            )
            pulse = self._generate_state(prompt, now, allow_inferred=True, call_type='idle_evolve')
            if pulse.get('memory_encode'):
                self.event_bus.publish(Event('memory.preprocess', {'reason': 'idle'}))
            sleeping = pulse.get('is_sleeping') is True
            if sleeping and not self.is_sleeping:
                self.event_bus.publish(Event('memory.sleep', {}))
            self.is_sleeping = sleeping
            scene = self.current_state['scene']
            can_speak = scene['communication_mode'] == 'face_to_face' or (
                scene['communication_mode'] == 'remote' and scene['remote_contact_confirmed'])
            if pulse.get('should_speak') and can_speak and not sleeping:
                self.event_bus.publish(Event('idle_speak', {}))
        except Exception as exc:
            logger.exception("闲置演化失败")
            self.last_error = str(exc)
        finally:
            self.last_interaction_logical_time = self._get_logical_now()
            self.last_interaction_real_time = time.monotonic()
            self.is_thinking = False

    def after_restore(self):
        self.current_state['scene'] = normalize_scene(self.current_state)
        self.event_bus.restore_logical_time(self.current_state['对应时间'])
        self.last_interaction_logical_time = self._get_logical_now()
        self.last_interaction_real_time = time.monotonic()
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
        self.dialogue_count = 0
        self.is_sleeping = False
        self.ready = True

    @property
    def prompt_injection(self):
        return (
            f"\n【角色当前状态与事实来源】\n{self.state_zip_full}\n"
            f"【当前准确时间】{self.event_bus.formatted_logical_now}；{time_period(self.event_bus.logical_now)}\n"
            "事实优先级：系统时钟和结构化场景 > 本轮明确输入 > 带时间的历史 > 时间未知的旧历史。"
            "活动若标记 inferred 或 needs_confirmation，只是推演或待确认。不能假装知道用户位置，"
            "本轮用户明确自述属于新事实；长期记忆未命中不能否定本轮自述。"
            "不能把过去的晚安/明天/约定当作现在；不确定时承认不确定。\n"
            "交流方式若不是 face_to_face，不能描述正在触碰用户、递送物品或双方同处一地；remote 只能远程交谈。\n"
            "未知位置、未确认联系方式只约束不能虚构物理动作，并不禁止接话、关心、讨论或邀请。"
            "不为普通闲聊逐项盘问位置和联系方式，也不因读不到旧记忆而重新自我介绍。"
            "来源与待确认标记用于内部判断，不在日常台词里反复解释协议或推演。"
            "优先回应对方这句话中的具体事情，并带上与你当前意图有关的真实偏好；不必每轮把话题引回近期目标。\n"
            "共同注意的对象必须有来源：能引用消息原文、用户提供的图片内容、已提交的活动材料或工具结果。"
            "远程时双方不自动共看同一页纸；没有共享材料时，不用‘你看这里/是不是这里’假装指给对方看。"
            "未提供的题目、代码、书页细节保持未知，可以提出猜测或自然请对方发原文，不假称已定位或读到。\n"
            + (self.event_bus.experience.prompt_context() if hasattr(self.event_bus, 'experience') else '')
            + (self.short_term_memory.working.prompt_context() if hasattr(self.short_term_memory, 'working') else '')
        )

    @property
    def state_zip_full(self):
        return self.state_zip + "\n近期综合轨迹:" + self.current_state.get('近期综合轨迹', '')

    @property
    def state_zip(self):
        state = self.current_state
        return (f"[状态 {state.get('对应时间', '')} | P:{state.get('P', 5)} A:{state.get('A', 5)} D:{state.get('D', 5)}]\n"
                f"情境:{state.get('客观情境', '')}\n内心:{state.get('内心活动', '')}\n目标:{state.get('近期目标', '')}\n"
                f"scene:{json.dumps(state.get('scene', {}), ensure_ascii=False)}\n"
                f"来源:{state.get('state_source', 'legacy')}；场景状态:{state.get('scene_status', 'pending')}")

    @property
    def speaking_prompt_injection(self):
        return "\n【主动交互任务】\n" + settings.IDLE_SPEAKING_UPDATE_PROMPT + self.prompt_injection

    @property
    def state_detail(self):
        return {'state': self.current_state, 'last_update': self._format_logical_time(self.last_interaction_logical_time)}
