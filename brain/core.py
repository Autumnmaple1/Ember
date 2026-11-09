from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager
import threading
import logging
from memory.memory_process import Hippocampus
import json
import uuid
import re
from brain.tag_utils import validate_and_fix_llm_output, extract_thought_and_speech
from core.turns import TurnCancelled, TERMINAL
from brain.dialogue_context import build_dialogue_messages
from core.proactive import delivery_rules, ProactivePolicy
from core.proactive_events import EventProactivePolicy, ordinary_offer
from tools.processor import ToolCallProcessor

logger = logging.getLogger(__name__)


class Brain:

    # 最大单次对话工具调用次数
    MAX_TOOL_CALLS_PER_TURN = 5

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: StateManager,
        memory: ShortTermMemory,
        hippocampus: Hippocampus,
        tool_processor: ToolCallProcessor = None,
    ):
        # 工具调用处理器（外部传入或自动创建）
        self.tool_processor = (
            tool_processor
            or ToolCallProcessor.create_with_memory_tool(
                hippocampus, max_calls=self.MAX_TOOL_CALLS_PER_TURN
            )
        )

        # Brain 自身的属性
        self.lock = threading.Lock()
        self._is_processing = False
        self.llm_client = LLMClient()
        self.state_manager = state_manager
        self.memory = memory
        self.event_bus = event_bus
        self.hippocampus = hippocampus
        self.proactive_policy = (EventProactivePolicy(settings.PROACTIVE_MIN_INTERVAL,
            minimum_gap=settings.PROACTIVE_EVENT_GAP, hourly_limit=settings.PROACTIVE_HOURLY_LIMIT)
            if settings.PROACTIVE_POLICY == 'events_v1' else ProactivePolicy(settings.PROACTIVE_MIN_INTERVAL))
        event_bus.proactive_policy = self.proactive_policy

        # 订阅事件
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event):
        user_msg = event.data["text"]
        with self.event_bus.pending_condition:
            if self.event_bus.paused:
                return
            turn = self.event_bus.turns.begin(event.data.get('timestamp', int(self.event_bus.logical_now * 1000)),
                event.data.get('turn_id') or event.data.get('message_id'))
            event.data['turn_id'] = turn.id
            self.event_bus.pending_dialogues += 1
        self.event_bus.publish(Event('turn.started', turn.snapshot()))
        thread = threading.Thread(target=self._accepted_dialogue, args=(user_msg, event.data), daemon=True)
        thread.start()

    def _accepted_dialogue(self, text, metadata):
        turn = self.event_bus.turns.current
        try:
            self.process_dialogue(text, metadata, _accepted=True)
        finally:
            with self.event_bus.pending_condition:
                self.event_bus.pending_dialogues -= 1
                self._emit_turn_finished(turn)
                self.event_bus.pending_condition.notify_all()

    def _on_idle_speak(self, event: Event):
        if self.event_bus.quiet or self.event_bus.world_paused or getattr(self.event_bus, 'archive_active', False) or getattr(self.state_manager, 'is_sleeping', False) is True:
            return
        def _speak():
            with self.event_bus.operation_lock:
                with self.event_bus.pending_condition:
                    if self.event_bus.paused or self.event_bus.world_paused or self.event_bus.quiet or getattr(self.event_bus, 'archive_active', False) or self.event_bus.pending_dialogues:
                        return
                    history = self.memory.get_memory()['history']
                    offer = None
                    gates = {}
                    if isinstance(self.proactive_policy, EventProactivePolicy):
                        provider = getattr(self.state_manager, 'proactive_offer', None)
                        offer = provider() if callable(provider) else ordinary_offer(history)
                        gate_provider = getattr(self.state_manager, 'delivery_gates', None)
                        gates = gate_provider() if callable(gate_provider) else {}
                        if event.data and event.data.get('offer_id') and event.data['offer_id'] != offer['id']:
                            return
                    try:
                        accepted = self.proactive_policy.reserve(history, offer, **gates) if offer else self.proactive_policy.reserve(history)
                        if not accepted:
                            return
                    except OSError:
                        self.proactive_policy.error = '主动发言计时保存失败，已暂停主动发言'
                        logger.exception('Could not reserve proactive attempt')
                        return
                    turn = self.event_bus.turns.begin(int(self.event_bus.logical_now * 1000), source='proactive')
                    self._is_processing = True
                self.event_bus.publish(Event('turn.started', turn.snapshot()))
                try:
                    if offer and offer['kind'] == 'reminder':
                        self._deliver_notice(offer['summary'], turn)
                    else:
                        self._active_proactive_offer = offer
                        self.memory.update_base_prompt(self.tool_processor.build_system_prompt_with_tools(settings.SYSTEM_PROMPT))
                        self._llm_speak(self.memory, pack=True, proactive=True, turn=turn)
                except TurnCancelled:
                    self._finish_incomplete(turn, 'interrupted')
                except Exception:
                    logger.exception('Proactive preparation failed')
                    self._finish_incomplete(turn, 'interrupted' if turn.cancelled.is_set() else 'failed')
                finally:
                    if offer:
                        try:
                            delivered = any(row.get('message_id') == turn.message_id for row in self.memory.get_memory()['history'])
                            self.proactive_policy.finish(offer['id'], 'delivered' if delivered else 'silent' if turn.status == 'completed' else 'interrupted' if turn.status == 'interrupted' else 'failed')
                        except OSError:
                            self.proactive_policy.error = '投递结果保存失败，已暂停主动投递'
                            logger.exception('Proactive receipt persistence failed')
                    self._active_proactive_offer = None
                    self._is_processing = False
                    self._emit_turn_finished(turn)

        thread = threading.Thread(target=_speak, daemon=True, name='ember-proactive')
        thread.start()

    def _deliver_notice(self, text, turn):
        """An explicitly enabled reminder has a deterministic body and normal receipts."""
        turn.check()
        turn.set_phase('generating')
        self.event_bus.publish(Event('llm.started', {'message_id': turn.message_id, 'timestamp': turn.timestamp, 'turn_id': turn.id}))
        turn.partial = text
        self.event_bus.publish(Event('llm.chunk', {'text': text, 'turn_id': turn.id}))
        self.memory.add_message('assistant', text, timestamp=turn.timestamp, message_id=turn.message_id, turn_source='proactive')
        turn.finish('completed')
        self.event_bus.publish(Event('llm.finished', {'text': text, 'message_id': turn.message_id,
            'timestamp': turn.timestamp, 'turn_id': turn.id}))

    def process_dialogue(self, user_message, metadata=None, _accepted=False):
        turn = None
        with self.event_bus.operation_lock:
            if self.event_bus.paused and not _accepted:
                return
            turn = self._process_dialogue(user_message, metadata or {})
            if not _accepted:
                self._emit_turn_finished(turn)

    def _process_dialogue(self, user_message, metadata):
        # 防止并发处理
        if self._is_processing:
            logger.warning("正在处理中，忽略新输入")
            return
        turn = self.event_bus.turns.begin(metadata.get('timestamp', int(self.event_bus.logical_now * 1000)),
            metadata.get('turn_id') or metadata.get('message_id'))

        try:
            self._is_processing = True

            self.memory.add_message("user", user_message,
                timestamp=metadata.get('timestamp', int(self.event_bus.logical_now * 1000)),
                message_id=metadata.get('message_id', str(uuid.uuid4())))
            turn.check()
            self.state_manager.prepare_dialogue(user_message)
            turn.check()

            # 异步更新 base_prompt（不阻塞 LLM 调用）
            # 注：实际注入到消息中是在 _llm_speak 中完成的
            self.memory.update_base_prompt(
                self.tool_processor.build_system_prompt_with_tools(settings.SYSTEM_PROMPT)
            )

            self._llm_speak(self.memory, pack=True, turn=turn)
            # Test doubles and silent responses must also release the turn.
            if turn.status not in TERMINAL:
                turn.finish('completed')
        except TurnCancelled:
            self._finish_incomplete(turn, 'interrupted')
        except Exception:
            if not turn.cancelled.is_set():
                logger.exception('Dialogue preparation failed')
            self._finish_incomplete(turn, 'interrupted' if turn.cancelled.is_set() else 'failed')
        finally:
            self._is_processing = False
        return turn

    def _emit_turn_finished(self, turn):
        if turn and turn.status in TERMINAL:
            turn.release()
            self.event_bus.publish(Event('turn.finished', turn.snapshot()))

    def _finish_incomplete(self, turn, status):
        if not turn.finish(status):
            return
        raw = self.tool_processor.remove_tool_calls(turn.partial)
        raw = re.sub(r'<(?:tool|tool_call)>[\s\S]*$', '', raw, flags=re.IGNORECASE)
        _, speech = extract_thought_and_speech(raw)
        speech = re.sub(r'</?response>', '', speech, flags=re.IGNORECASE).strip()
        if speech:
            try:
                self.memory.add_message('assistant', speech, timestamp=turn.timestamp,
                    message_id=turn.message_id, completion_status='interrupted', turn_source=turn.source)
            except Exception:
                logger.exception('Could not save interrupted short-term message')
        self.event_bus.publish(Event('llm.interrupted', {'text': speech, 'message_id': turn.message_id,
            'timestamp': turn.timestamp, 'turn_id': turn.id, 'completion_status': 'interrupted'}))

    def _stream_reply(self, messages, turn):
        turn.check()
        stream = self.llm_client.stream_chat(model_config=settings.LARGE_LLM, messages=messages,
            call_type='proactive' if turn.source == 'proactive' else 'dialogue', cancel_token=turn)
        try:
            for chunk in stream:
                turn.check()
                turn.partial += chunk
                yield chunk
            turn.check()
        finally:
            if hasattr(stream, 'close'):
                stream.close()

    def _reset_reply(self, turn):
        turn.check()
        turn.partial = ''
        self.event_bus.publish(Event('llm.chunk', {'text': '', 'reset': True, 'turn_id': turn.id}))

    def _llm_speak(self, memory, pack: bool = False, memories: str = "", proactive=False, turn=None):
        own_turn = turn is None
        turn = turn or self.event_bus.turns.begin(int(self.event_bus.logical_now * 1000), source='proactive' if proactive else 'dialogue')
        try:
            self._generate_reply(memory, pack, memories, proactive, turn)
            if turn.status not in TERMINAL:
                turn.finish('completed')
        except TurnCancelled:
            self._finish_incomplete(turn, 'interrupted')
        except Exception:
            if not turn.cancelled.is_set():
                logger.exception('Reply generation failed')
            self._finish_incomplete(turn, 'interrupted' if turn.cancelled.is_set() else 'failed')
        finally:
            if own_turn:
                self._emit_turn_finished(turn)

    def _generate_reply(self, memory, pack, memories, proactive, turn):
        # 立即发布 llm.started 事件，让前端尽早显示"正在思考"
        turn.check()
        turn.set_phase('generating')
        turn.timestamp = int(self.event_bus.logical_now * 1000)
        message_id = turn.message_id
        timestamp = turn.timestamp
        self.event_bus.publish(Event(name="llm.started", data={'message_id': message_id, 'timestamp': timestamp, 'turn_id': turn.id}))

        # 在锁外准备数据，减少锁持有时间
        with self.lock:
            data = memory.get_full_messages()
            system_prompt = data[0]["content"]
            history = data[1:]

        # Preserve actual dialogue roles on both legacy and normal entry paths.
        # Dynamic facts belong to the system context, never a fake user transcript.
        messages = build_dialogue_messages(system_prompt, history, self.state_manager.prompt_injection,
            self.event_bus.logical_now, proactive=proactive,
            proactive_instruction=settings.IDLE_SPEAKING_UPDATE_PROMPT + delivery_rules(self.event_bus), memories=memories)
        offer = getattr(self, '_active_proactive_offer', None) if proactive else None
        if offer:
            messages[0]['content'] += '\n【本轮主动事件：程序记录】' + json.dumps(offer, ensure_ascii=False) + '\n围绕该事件自然开口，按内容说完整；不将提醒或计时解释成用户已经完成活动。'

        full_content = ""
        chunk_count = 0
        max_chunks = 10000

        try:
            stream_gen = self._stream_reply(messages, turn)

            for chunk in stream_gen:
                chunk_count += 1
                if chunk_count > max_chunks:
                    logger.warning("LLM 输出超过最大限制，截断")
                    turn.partial = full_content
                    raise RuntimeError('Reply exceeded the generation limit')

                full_content += chunk
                self.event_bus.publish(Event(name="llm.chunk", data={"text": chunk, 'turn_id': turn.id}))

        except Exception:
            raise

        if full_content:
            # 多轮工具调用处理
            max_tool_iterations = 1
            iteration = 0
            current_messages = messages.copy()

            while iteration < max_tool_iterations:
                iteration += 1

                # 检测并执行工具调用
                tool_calls = self.tool_processor.extract_tool_calls(full_content)

                if not tool_calls:
                    break  # 没有工具调用，结束循环

                logger.info(
                    f"[Tool] 第 {iteration} 轮检测到 {len(tool_calls)} 个工具调用"
                )

                # 执行工具调用（带调用来源标识）
                turn.check()
                turn.set_phase('tools')
                tool_results = self.tool_processor.execute_tool_calls(
                    tool_calls, caller=f"Brain._llm_speak[iter{iteration}]", cancel_check=turn.check
                )
                turn.check()

                # 格式化工具结果
                tool_results_text = self.tool_processor.format_tool_results_for_prompt(
                    tool_results
                )

                # 从原始内容中移除工具调用标签
                clean_content = self.tool_processor.remove_tool_calls(full_content)

                # 如果有工具结果，需要让 LLM 继续生成回复
                if tool_results_text:
                    # 发布工具执行结果事件
                    self.event_bus.publish(
                        Event(
                            name="tool.executed",
                            data={
                                "calls": tool_calls,
                                "results": tool_results,
                                "iteration": iteration,
                            },
                        )
                    )

                    # 构建包含工具结果的新消息
                    current_messages = current_messages + [
                        {"role": "assistant", "content": full_content},
                        {
                            "role": "user",
                            "content": f"{tool_results_text}\n\n【重要】你现在已经获取了所需的信息，请立刻生成你的回复。\n【重要】不要再调用任何工具！直接根据工具结果和提示词要求生成回复！",
                        },
                    ]

                    # 再次调用 LLM 生成回复
                    try:
                        follow_up_content = ""
                        self._reset_reply(turn)
                        turn.set_phase('generating')
                        stream_gen = self._stream_reply(current_messages, turn)

                        for chunk in stream_gen:
                            chunk_count += 1
                            follow_up_content += chunk
                            self.event_bus.publish(
                                Event(name="llm.chunk", data={"text": chunk, 'turn_id': turn.id})
                            )

                        full_content = follow_up_content

                    except TurnCancelled:
                        raise
                    except Exception as e:
                        logger.error(f"LLM 工具后续调用失败: {e}")
                        raise
                else:
                    full_content = clean_content
                    break

            # 确保移除所有工具调用标签（防止 LLM 在工具执行后又输出工具调用）
            retry_count = 0
            max_retry = 2
            while (
                self.tool_processor.has_tool_calls(full_content)
                and retry_count < max_retry
            ):
                retry_count += 1
                logger.warning(
                    f"[Tool] 最终输出仍包含工具调用标签，请求 LLM 重新生成 (第 {retry_count} 次)"
                )

                # 请求 LLM 重新生成（不带工具调用）
                retry_messages = current_messages + [
                    {"role": "assistant", "content": full_content},
                    {
                        "role": "user",
                        "content": "你的回复中包含了工具调用标签，这是不允许的。请直接给出你的回复，不要使用任何工具调用。",
                    },
                ]

                try:
                    retry_content = ""
                    self._reset_reply(turn)
                    stream_gen = self._stream_reply(retry_messages, turn)
                    for chunk in stream_gen:
                        retry_content += chunk
                        self.event_bus.publish(
                            Event(name="llm.chunk", data={"text": chunk, 'turn_id': turn.id})
                        )
                    full_content = retry_content
                except TurnCancelled:
                    raise
                except Exception as e:
                    logger.error(f"LLM 重试生成失败: {e}")
                    raise

            # 最终仍有工具标签，强制清理
            if self.tool_processor.has_tool_calls(full_content):
                logger.warning("[Tool] 重试后仍有工具标签，强制清理")
                full_content = self.tool_processor.remove_tool_calls(full_content)

            # 修复可能不完整的标签
            full_content = validate_and_fix_llm_output(full_content)

            logger.info(f"LLM回复: {full_content[:100]}...")
            turn.finish('completed')

            try:
                self.memory.add_message("assistant", full_content, timestamp=timestamp, message_id=message_id,
                    turn_source=turn.source)
            except Exception as e:
                logger.error(f"保存消息失败: {e}")

            self.event_bus.publish(
                Event(name="llm.finished", data={"text": full_content, 'message_id': message_id, 'timestamp': timestamp, 'turn_id': turn.id})
            )
