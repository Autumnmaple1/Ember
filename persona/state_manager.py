import json
import time
import logging
from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import EventBus, Event
from memory.memory_process import Hippocampus
from memory.short_term import ShortTermMemory
import random
from concurrent.futures import ThreadPoolExecutor
import threading
from tools.processor import ToolCallProcessor

logger = logging.getLogger(__name__)


class StateManager:
    # 类级别线程池
    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="state_mgr")

    def __init__(
        self,
        event_bus: EventBus,
        hippocampus: Hippocampus,
        short_term_memory: ShortTermMemory,
        tool_processor: ToolCallProcessor = None,
    ):
        self.event_bus = event_bus
        self.hippocampus = hippocampus
        self.short_term_memory = short_term_memory
        self.llm_client = LLMClient()
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT

        self.last_interaction_logical_time = self.event_bus.logical_now

        self.current_state = settings.STATE
        self._thinking_lock = threading.Lock()  # 保护 is_thinking
        self._state_lock = threading.Lock()  # 保护 state.json 文件写入
        self._bg_lock = threading.Lock()  # 保护 _is_bg_processing
        self._suspend_lock = threading.Lock()  # 保护 _suspend_count
        self._suspend_count = 0  # 状态更新暂停计数器（>0 时禁止写入）
        self._interrupt_idle = threading.Event()  # 用户输入中断空闲演化信号
        self._thinking = False
        self._is_bg_processing = False  # 后台总结互斥标志（不阻塞前端）
        self.is_sleeping = False
        self.dialogue_count = 0

        # 工具调用处理器（外部传入或自动创建）
        self.tool_processor = (
            tool_processor or ToolCallProcessor.create_with_memory_tool(hippocampus)
        )

        self.event_bus.subscribe("user_interaction", self._on_llm_state_update)
        self.event_bus.subscribe("system.tick", self._on_tick)

    @property
    def is_thinking(self):
        with self._thinking_lock:
            return self._thinking

    @is_thinking.setter
    def is_thinking(self, value):
        with self._thinking_lock:
            self._thinking = value

    @property
    def _state_updates_suspended(self):
        with self._suspend_lock:
            return self._suspend_count > 0

    def suspend_state_updates(self):
        """暂停状态更新（存档加载等关键操作前调用）"""
        with self._suspend_lock:
            self._suspend_count += 1
        logger.info("状态更新已暂停 (count=%d)", self._suspend_count)

    def resume_state_updates(self):
        """恢复状态更新"""
        with self._suspend_lock:
            self._suspend_count = max(0, self._suspend_count - 1)
        logger.info("状态更新已恢复 (count=%d)", self._suspend_count)

    def _get_logical_now(self):
        return self.event_bus.logical_now

    def _format_logical_time(self, logical_time, fmt="%Y-%m-%d %H:%M:%S"):
        return self.event_bus.format_logical_time(logical_time, fmt)

    def _get_floating_timeout(self, time: float):
        lower = time * 0.9
        upper = time * 1.1
        return random.uniform(lower, upper)

    def _format_duration(self, seconds: float):
        days = int(seconds // (24 * 3600))
        rem = seconds % (24 * 3600)
        hours = int(rem // 3600)
        rem %= 3600
        minutes = int(rem // 60)
        secs = int(rem % 60)

        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if secs > 0 or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    def _get_idle_info(self, logical_now):
        logical_elapsed = logical_now - self.last_interaction_logical_time
        return {
            "idle_duration": self._format_duration(logical_elapsed),
            "current_time": self._format_logical_time(logical_now, "%Y-%m-%d %H:%M:%S"),
            "old_state": json.dumps(self.current_state, ensure_ascii=False),
        }

    def _apply_idle_template(self, template, info):
        return (
            template.replace("{{idle_minutes}}", info["idle_duration"])
            .replace("{{current_time}}", info["current_time"])
            .replace("{{old_state}}", info["old_state"])
        )

    def _ask_llm(self, system_prompt, user_prompt, call_type: str = "state_update"):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.llm_client.one_chat(
            model_config=settings.SMALL_LLM,
            messages=messages,
            call_type=call_type,
        )

    def _ask_llm_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        call_type: str = "state_update",
        max_iterations: int = 1,
    ) -> str:
        """
        支持工具调用的 LLM 请求

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            call_type: 调用类型
            max_iterations: 最大迭代次数（防止无限循环）

        Returns:
            LLM 的最终输出（已处理工具调用）
        """
        # 构建带工具说明的系统提示
        enhanced_prompt = self.tool_processor.build_system_prompt_with_tools(
            system_prompt
        )

        messages = [
            {"role": "system", "content": enhanced_prompt},
            {"role": "user", "content": user_prompt},
        ]

        iteration = 0
        final_response = ""

        while iteration < max_iterations:
            iteration += 1
            response = self.llm_client.one_chat(
                model_config=settings.SMALL_LLM,
                messages=messages,
                call_type=call_type,
            )

            if not response:
                break

            # 处理工具调用（带调用来源标识）
            caller = f"StateManager._ask_llm_with_tools[{call_type}]"
            result = self.tool_processor.process_llm_output(
                response, execute=True, caller=caller
            )

            if not result["has_tool_calls"]:
                # 没有工具调用，直接返回
                final_response = response
                break

            # 有工具调用，将结果反馈给 LLM
            logger.info(f"[StateManager] 检测到 {len(result['tool_calls'])} 个工具调用")

            # 构建后续消息
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {
                    "role": "user",
                    "content": f"{result['results_text']}\n请根据工具执行结果继续处理。",
                }
            )

            # 保存最终清理后的文本
            final_response = result["clean_text"]

        # 检查最终输出是否仍包含工具标签，如果是则重试
        retry_count = 0
        max_retry = 2
        while (
            self.tool_processor.has_tool_calls(final_response)
            and retry_count < max_retry
        ):
            retry_count += 1
            logger.warning(
                f"[StateManager] 最终输出仍包含工具调用标签，请求 LLM 重新生成 (第 {retry_count} 次)"
            )

            retry_messages = messages + [
                {"role": "assistant", "content": final_response},
                {
                    "role": "user",
                    "content": "你的回复中包含了工具调用标签，这是不允许的。请直接给出你的回复，不要使用任何工具调用。",
                },
            ]

            retry_response = self.llm_client.one_chat(
                model_config=settings.SMALL_LLM,
                messages=retry_messages,
                call_type=call_type,
            )

            if retry_response:
                final_response = retry_response

        # 最终仍有工具标签，强制清理
        if self.tool_processor.has_tool_calls(final_response):
            logger.warning("[StateManager] 重试后仍有工具标签，强制清理")
            final_response = self.tool_processor.remove_tool_calls(final_response)

        return final_response

    def _async_log(self, filename, content):
        def _log():
            try:
                with open(filename, "a", encoding="utf-8", buffering=1) as f:
                    f.write(content + "\n")
            except Exception as e:
                logger.error(f"Error writing log: {e}")

        self._executor.submit(_log)

    def _update_state(self, new_state, logical_now=None):
        # 存档加载等关键操作期间，禁止任何状态写入
        if self._state_updates_suspended:
            logger.warning("状态更新已暂停，丢弃本次更新")
            return

        # 安全地复制并删除近期综合轨迹（用于日志记录），不影响原始数据
        log_data = new_state.copy()
        log_data.pop("近期综合轨迹", None)  # 使用 pop 避免 KeyError
        self._async_log(
            "./config/chat_history.log",
            f"{self.state_zip}",
        )

        # 先将增量合并到 current_state，保证状态完整性
        self.current_state.update(new_state)

        # 广播的是完整的合并后状态，而非仅仅增量
        self.event_bus.publish(Event("state.update", data={"new_state": self.current_state.copy()}))

        # 使用锁保护文件写入，防止竞争条件
        with self._state_lock:
            try:
                # 写入完整的 current_state 而非仅 new_state
                with open("./config/state.json", "w", encoding="utf-8") as f:
                    json.dump(self.current_state, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"状态文件写入失败: {e}")

    def _on_tick(self, event: Event):
        if self._state_updates_suspended:
            return
        # 使用 _is_bg_processing 作为重入保护，不再用 is_thinking
        # 这样后台总结不会阻塞前端输入
        with self._bg_lock:
            if self._is_bg_processing:
                return

        logical_now = self._get_logical_now()
        logical_elapsed = logical_now - self.last_interaction_logical_time
        if logical_elapsed > self._get_floating_timeout(self.state_update_timeout):
            self.state_update_timeout = min(
                self.state_update_timeout * 1.5, settings.STATE_IDLE_MAX_TIMEOUT
            )
            self._update_state_due_to_idle(logical_now)

    def _on_llm_state_update(self, event: Event):
        if self.is_thinking:
            # 空闲演化正在运行，触发中断并等待其被中断后释放
            self._interrupt_idle.set()
            for _ in range(50):
                if not self.is_thinking:
                    break
                time.sleep(0.1)
            else:
                logger.warning("等待空闲演化释放超时，放弃本次状态更新")
                return
        if self._state_updates_suspended:
            return
        # 使用 _is_bg_processing 作为重入保护，不再阻碍前端
        with self._bg_lock:
            if self._is_bg_processing:
                return
        self.dialogue_count += 1
        if self.dialogue_count % settings.STATE_UPDATE_INTERVAL != 0:
            logger.info(
                f"对话轮次 {self.dialogue_count}，跳过状态更新（间隔={settings.STATE_UPDATE_INTERVAL}）"
            )
            return
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT

        # 设置后台处理标志（不影响 is_thinking）
        with self._bg_lock:
            self._is_bg_processing = True

        history = event.data.get("history", [])
        # 预处理历史记录：明确角色标识，防止 LLM 混淆
        processed_history = []
        for msg in history:
            role_name = "对方" if msg["role"] == "user" else "依鸣(你自己)"
            processed_history.append({
                "role": msg["role"],
                "content": f"{role_name}: {msg['content']}"
            })

        logical_now = self._get_logical_now()
        logical_now_str = self._format_logical_time(logical_now)
        logger.info(f"[{logical_now_str}] 收到用户交互事件，准备异步更新状态...")

        # 将重量级 LLM 调用提交到线程池异步执行，不阻塞当前线程
        self._executor.submit(
            self._do_llm_state_update, processed_history, logical_now, logical_now_str
        )

    def _do_llm_state_update(self, history, logical_now, logical_now_str):
        """在线程池中执行的对话后状态更新（不阻塞主流程）"""
        prompt = f"当前的准确时间: {logical_now_str}\n\n[先前状态]\n{json.dumps(self.current_state, ensure_ascii=False)}\n\n[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}\n\n{settings.STATE_UPDATE_PROMPT}"

        try:
            # 使用支持工具调用的 LLM 请求
            response = self._ask_llm_with_tools(
                settings.CORE_PERSONA, prompt, call_type="state_update"
            )
            if response:

                new_state = self.llm_client._extract_json(response)
                if new_state is None:
                    raise json.JSONDecodeError("Failed to extract JSON", response, 0)

                new_state["对应时间"] = logical_now_str
                self._update_state(new_state, logical_now=logical_now)

                # 使用压缩格式记录日志
                logger.info(f"[{logical_now_str}] 对话引发状态更新:{self.state_zip}")

        except Exception as e:
            logger.error(f"对话更新失败: {e}")
        finally:
            with self._bg_lock:
                self._is_bg_processing = False
            self.last_interaction_logical_time = self._get_logical_now()

    def _update_state_due_to_idle(self, logical_now):
        # 设置后台处理标志（不影响 is_thinking）
        with self._bg_lock:
            if self._is_bg_processing:
                return
            self._is_bg_processing = True

        self._interrupt_idle.clear()
        self.is_thinking = True
        self.dialogue_count = 0  # 重置对话计数器
        logical_now_str = self._format_logical_time(logical_now)
        logger.info(f"[{logical_now_str}] 收到闲置事件，准备异步更新状态...")

        info = self._get_idle_info(logical_now)
        history = self.short_term_memory.get_memory().get("history", [])

        # 将重量级 LLM 调用提交到线程池异步执行
        self._executor.submit(
            self._do_idle_state_update, info, history, logical_now, logical_now_str
        )

    def _do_idle_state_update(self, info, history, logical_now, logical_now_str):
        """在线程池中执行的闲置状态演化（不阻塞心跳线程）"""
        # 不再直接调用 hippocampus.load_memory，改为让 LLM 通过工具主动获取记忆
        # 这样与对话系统的记忆加载方式保持一致

        user_content = f"""【环境变更推断任务】
距离上次互动已经过去 {info['idle_duration']} 分钟，当前时间为 {info['current_time']}。
历史状态：{info['old_state']}
[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}
"""

        prompt = user_content + "\n\n" + settings.IDLE_STATE_UPDATE_PROMPT

        try:
            # 使用支持工具调用的 LLM 请求
            response = self._ask_llm_with_tools(
                settings.CORE_PERSONA, prompt, call_type="idle_evolve"
            )

            # 检查是否被用户输入中断
            if self._interrupt_idle.is_set():
                self._interrupt_idle.clear()
                logger.info("空闲演化被用户输入中断，丢弃结果")
                return

            if response:
                data = self.llm_client._extract_json(response)
                if data is None:
                    raise json.JSONDecodeError("Failed to extract JSON", response, 0)

                impulse = data.get("action_pulse", {})

                if "action_pulse" in data:
                    del data["action_pulse"]

                data["对应时间"] = logical_now_str
                self._update_state(data, logical_now=logical_now)

                if impulse.get("memory_encode", False):
                    self.event_bus.publish(
                        Event(
                            name="memory.preprocess",
                            data={},
                        )
                    )

                if impulse.get("is_sleeping", False) and not self.is_sleeping:
                    self.is_sleeping = True
                    self.event_bus.publish(
                        Event(
                            name="memory.sleep",
                            data={},
                        )
                    )

                if not impulse.get("is_sleeping", False):
                    self.is_sleeping = False

                if impulse.get("should_speak", False):
                    idle_duration_seconds = max(
                        0.0, logical_now - self.last_interaction_logical_time
                    )
                    self.event_bus.publish(
                        Event(
                            name="idle_speak",
                            data={"idle_duration_seconds": idle_duration_seconds},
                        )
                    )

                # 使用压缩格式记录日志
                logger.info(f"[{logical_now_str}] 闲置逻辑演化:{self.state_zip}")

        except Exception as e:
            logger.error(f"闲置更新失败: {e}")
        finally:
            self._interrupt_idle.clear()
            self.is_thinking = False
            with self._bg_lock:
                self._is_bg_processing = False
            self.last_interaction_logical_time = self._get_logical_now()

    @property
    def prompt_injection(self):
        return f"\n\n【角色的先前状态】\n{self.state_zip_full}\n\n"

    @property
    def state_zip_full(self):
        s = self.current_state
        line = s.get("近期综合轨迹", "")
        return f"{self.state_zip}\n近期综合轨迹:{line}\n"

    @property
    def state_zip(self):
        """压缩状态注入：完整保留字段，用紧凑格式节省 token"""
        s = self.current_state
        time_str = s.get("对应时间", "")
        pad = f"P:{s.get('P',5)} A:{s.get('A',5)} D:{s.get('D',5)}"
        location = s.get("当前位置", "常规位置")
        action = s.get("当前行为", "无特定行为")
        situation = s.get("客观情境", "")
        inner = s.get("内心活动", "")
        goal = s.get("近期目标", "")

        # 紧凑格式，完整保留内容
        return f"\n[状态 {time_str} | {pad}]\n位置:{location} (正在:{action})\n情境:{situation}\n内心:{inner}\n目标:{goal}\n"

    @property
    def speaking_prompt_injection(self):
        logical_now = self._get_logical_now()
        info = self._get_idle_info(logical_now)
        # 将动态内容构建到用户消息中，保持 system prompt 静态
        prompt = f"""距离上次互动已过去 {info['idle_duration']} 分钟，当前 {info['current_time']}。原状态为 {info['old_state']}。
{settings.IDLE_SPEAKING_UPDATE_PROMPT}
接下来提供之前的聊天记录供参考。"""
        return f"\n\n###你的任务###\n{prompt}\n\n"

    @property
    def state_detail(self):
        return {
            "state": self.current_state,
            "last_update": self._format_logical_time(
                self.last_interaction_logical_time
            ),
        }
