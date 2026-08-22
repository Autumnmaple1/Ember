"""
Ember Benchmark Runner v1.0
===========================
自动化基准测试工具，衡量 Token 消耗、Prompt 构成、响应延迟和记忆有效性。

用法:
  python utils/benchmark_runner.py               # 标准 A/B 测试
  python utils/benchmark_runner.py --stress      # 覆盖配置，强制开启压力测试
  python utils/benchmark_runner.py --no-latency  # 关闭延迟记录

输出:
  benchmark_report_[timestamp].md
"""

import sys
import os
import re
import json
import time
import queue
import shutil
import atexit
import argparse
from datetime import datetime
from pathlib import Path

# ── 路径设置（必须在 import 项目模块之前）──────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOG_FILE = ROOT / "data" / "logs" / "system.log"
ENV_FILE = ROOT / ".env"
ENV_BACKUP = ROOT / ".env.benchmark_backup"
SCRIPT_FILE = ROOT / "utils" / "test_script.json"
CONFIG_FILE = ROOT / "utils" / "test_config.json"

TIMEOUT = 120  # 每轮最长等待秒数


# ── 环境变量控制 ───────────────────────────────────────────────────────────────


def _env_set_temperature(temp: float):
    """在 .env 中写入或更新 LLM_TEMPERATURE。"""
    if not ENV_FILE.exists():
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if re.match(r"#?\s*LLM_TEMPERATURE\s*=", line):
            new_lines.append(f"LLM_TEMPERATURE={temp}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"LLM_TEMPERATURE={temp}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def backup_and_lock_env():
    """备份 .env，强制 LLM_TEMPERATURE=0，注册恢复钩子。"""
    if ENV_FILE.exists():
        shutil.copy2(ENV_FILE, ENV_BACKUP)
        print(f"  [Env] .env 已备份 → .env.benchmark_backup")
    _env_set_temperature(0.0)
    print(f"  [Env] LLM_TEMPERATURE 强制设为 0.0（确保回复确定性）")
    atexit.register(restore_env)


def restore_env():
    """从备份恢复 .env（atexit 自动调用）。"""
    if ENV_BACKUP.exists():
        shutil.copy2(ENV_BACKUP, ENV_FILE)
        ENV_BACKUP.unlink(missing_ok=True)
        print(f"\n  [Env] .env 已从备份恢复")


# ── 日志解析 ───────────────────────────────────────────────────────────────────

_USAGE_RE = re.compile(
    r"\[LLM Usage\] Type: (?P<type>\S+) \| Model: (?P<model>\S+) \| Prompt: (?P<prompt>\d+)"
    r" \| Completion: (?P<completion>\d+) \| Total: (?P<total>\d+)"
    r"(?:.*?CachedHit: (?P<cached>\d+))?"
)
_BREAKDOWN_RE = re.compile(
    r"\[Prompt Breakdown\] base=(?P<base>\d+) mem=(?P<mem>\d+)"
    r" history=(?P<history>\d+) state=(?P<state>\d+)"
)
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_logs_after(since_ts: float):
    """
    解析 system.log 中 since_ts 之后的 [LLM Usage] 和 [Prompt Breakdown] 行。
    返回 (usages: list[dict], breakdowns: list[dict])
    """
    usages, breakdowns = [], []
    if not LOG_FILE.exists():
        return usages, breakdowns
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _TS_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            if ts < since_ts:
                continue
            mu = _USAGE_RE.search(line)
            if mu:
                usages.append(
                    dict(
                        type=mu.group("type"),
                        model=mu.group("model"),
                        prompt=int(mu.group("prompt")),
                        completion=int(mu.group("completion")),
                        total=int(mu.group("total")),
                        cached=int(mu.group("cached") or 0),
                    )
                )
            mb = _BREAKDOWN_RE.search(line)
            if mb:
                breakdowns.append(
                    dict(
                        base=int(mb.group("base")),
                        mem=int(mb.group("mem")),
                        history=int(mb.group("history")),
                        state=int(mb.group("state")),
                    )
                )
    return usages, breakdowns


# ── 模块初始化 ─────────────────────────────────────────────────────────────────


def init_modules():
    """初始化 Ember 后端模块（与 main.py 相同的接线顺序）。"""
    from core.event_bus import EventBus
    from core.heartbeat import Heartbeat
    from persona.state_manager import StateManager
    from brain.core import Brain
    from memory.short_term import ShortTermMemory
    from memory.db_memory import DBMemory
    from memory.episodic_memory import EpisodicMemory
    from memory.memory_process import Hippocampus
    from tools.processor import ToolCallProcessor
    from config.settings import settings
    import config.logging_config  # noqa: F401 — 必须激活日志配置

    event_bus = EventBus()
    heartbeat = Heartbeat(event_bus, interval=settings.HEARTBEAT_INTERVAL)
    memory = ShortTermMemory(
        base_prompt=settings.SYSTEM_PROMPT,
        max_memory_size=settings.CONTEXT_WINDOW_SIZE,
    )
    episodic_memory = EpisodicMemory(event_bus)
    hippocampus = Hippocampus(event_bus)
    tool_processor = ToolCallProcessor.create_with_memory_tool(hippocampus)
    state_manager = StateManager(event_bus, hippocampus, memory, tool_processor)
    brain = Brain(event_bus, state_manager, memory, hippocampus, tool_processor)
    db_memory = DBMemory(event_bus)
    heartbeat.start()

    return {
        "event_bus": event_bus,
        "heartbeat": heartbeat,
        "memory": memory,
        "episodic_memory": episodic_memory,
        "hippocampus": hippocampus,
        "state_manager": state_manager,
        "brain": brain,
        "db_memory": db_memory,
    }


def reset_memory():
    """白板重置：清空所有记忆层。"""
    from utils.reset_memory import (
        reset_short_term,
        reset_state,
        reset_postgres,
    )

    reset_short_term()
    reset_state()
    reset_postgres()


# ── 单组运行器 ─────────────────────────────────────────────────────────────────


def run_group(
    label: str, modules: dict, turns: list, meta: dict, cfg: dict
) -> list[dict]:
    """
    运行一组对话，收集每轮的 Token 数据、Prompt 分布、延迟和 ROI。

    EventBus 无 unsubscribe，使用 Queue 订阅一次处理所有轮次。
    """
    from core.event_bus import Event

    event_bus = modules["event_bus"]
    brain = modules["brain"]

    finished_q = queue.Queue()
    event_bus.subscribe(
        "llm.finished", lambda e: finished_q.put(e.data.get("text", ""))
    )

    # ── TTFT 追踪（订阅一次，通过 _current 共享当前轮次）──
    _current = {"idx": -1}
    ttft_tracker = {}  # turn_idx → 首个 chunk 到达时刻
    latency_on = cfg.get("latency", {}).get("enabled", False)

    if latency_on:

        def _on_chunk(e):
            idx = _current["idx"]
            if idx >= 0 and idx not in ttft_tracker:
                ttft_tracker[idx] = time.time()

        event_bus.subscribe("llm.chunk", _on_chunk)

    # ── 记忆 ROI 检查点：{turn_idx (0-based) → [keywords]} ──
    roi_checks = {}
    if cfg.get("memory_roi", {}).get("enabled", False):
        for cp in meta.get("memory_checkpoints", []):
            roi_checks[cp["check_at_turn"] - 1] = cp.get("keywords", [])

    results = []
    for i, turn in enumerate(turns):
        msg = turn["text"]
        _current["idx"] = i
        t0 = time.time()

        print(f"  [{label}] 轮{i + 1} → {msg[:22]}…", flush=True)
        event_bus.publish(Event(name="user.input", data={"text": msg}))

        # 1. 等待 LLM 流结束
        response_text = ""
        try:
            response_text = finished_q.get(timeout=TIMEOUT)
        except queue.Empty:
            print(f"  [{label}] 轮{i + 1} ⚠ 超时 {TIMEOUT}s，继续")

        t_end = time.time()

        # 2. 轮询 _is_processing，等待 finally 块释放
        deadline = time.time() + 10
        while brain._is_processing and time.time() < deadline:
            time.sleep(0.05)

        # 3. 等待日志落盘
        time.sleep(1)

        usages, breakdowns = parse_logs_after(t0 - 1)

        # 分离对话和其他类型的调用
        dialogue_usages = [u for u in usages if u.get("type") == "dialogue"]
        other_usages = [u for u in usages if u.get("type") != "dialogue"]

        row: dict = {
            "turn": i + 1,
            "text": msg,
            "response": response_text[:120] if response_text else "",
            "background_tokens": sum(u["total"] for u in other_usages),
        }

        if dialogue_usages:
            u = dialogue_usages[-1]
            row.update(
                prompt=u["prompt"],
                completion=u["completion"],
                total=u["total"],
                cached=u["cached"],
            )
        else:
            row.update(prompt=-1, completion=-1, total=-1, cached=0)

        row["breakdown"] = breakdowns[-1] if breakdowns else None

        if latency_on:
            ttft = round(ttft_tracker[i] - t0, 3) if i in ttft_tracker else None
            row["ttft"] = ttft
            row["total_latency"] = round(t_end - t0, 3)

        if i in roi_checks and response_text:
            kws = roi_checks[i]
            hits = [kw for kw in kws if kw in response_text]
            row["roi"] = {
                "keywords": kws,
                "hits": hits,
                "score": round(len(hits) / len(kws), 2) if kws else 0,
            }

        results.append(row)

        # 终端摘要
        cached_str = f"  cached={row['cached']}" if row.get("cached", 0) > 0 else ""
        latency_str = (
            f"  TTFT={row.get('ttft')}s" if latency_on and row.get("ttft") else ""
        )
        print(
            f"         Prompt={row['prompt']}  Comp={row['completion']}{cached_str}{latency_str}"
        )

    return results


# ── 压力测试 ───────────────────────────────────────────────────────────────────


def run_stress_test(modules: dict, turns_base: list, n: int, cfg: dict) -> list[dict]:
    """运行 N 轮（循环脚本），寻找 Token 增长天花板和图谱检索瓶颈。"""
    extended = []
    while len(extended) < n:
        extended.extend(turns_base)
    extended = extended[:n]
    print(f"\n[压力测试] 运行 {n} 轮（脚本循环）...")
    return run_group("Stress", modules, extended, {}, cfg)


# ── 报告生成 ───────────────────────────────────────────────────────────────────


def _bd_pct(bd: dict, field: str, total_chars: int) -> str:
    v = bd.get(field, 0)
    if total_chars == 0:
        return str(v)
    return f"{v}c ({v * 100 // total_chars}%)"


def generate_report(
    a_results: list,
    b_results: list,
    meta: dict,
    cfg: dict,
    stress_results=None,
    run_ts: str = None,
) -> Path:
    ts_str = run_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ROOT / f"benchmark_report_{ts_str}.md"
    baseline = cfg.get("baseline", {})

    # 预计算汇总
    total_a_p = sum(r["prompt"] for r in a_results if r.get("prompt", -1) >= 0)
    total_b_p = sum(r["prompt"] for r in b_results if r.get("prompt", -1) >= 0)
    total_a_c = sum(r.get("cached", 0) for r in a_results)
    total_b_c = sum(r.get("cached", 0) for r in b_results)
    total_a_bg = sum(r.get("background_tokens", 0) for r in a_results)
    total_b_bg = sum(r.get("background_tokens", 0) for r in b_results)
    growth = (total_b_p - total_a_p) / total_a_p * 100 if total_a_p > 0 else 0
    b_manual_a = baseline.get("a_total_tokens")
    b_manual_b = baseline.get("b_total_tokens")

    L = []  # report lines

    L.append("# Ember Benchmark Report")
    L.append(f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(
        f"**测试脚本**: `utils/test_script.json`（{len(meta.get('turns', []))} 轮对话）"
    )
    L.append(f"**LLM_TEMPERATURE**: `0.0`（强制锁定，确保回复确定性）")
    L.append(f"\n---\n")

    # ── 1. 逐轮 Prompt 分布归因 ──────────────────────────────────────────────
    L.append("## 1. 逐轮 Prompt Token 分布归因\n")
    L.append("> `base` = 固定人设 System Prompt 字符数，`mem` = 注入的记忆字符数，")
    L.append("> `history` = 打包后的对话历史字符数，`state` = PAD 状态注入字符数。\n")

    for group_label, group_results in [
        ("A（白板）", a_results),
        ("B（累积）", b_results),
    ]:
        L.append(f"### {group_label}\n")
        L.append(
            "| 轮 | Prompt | Comp | Cached | base | mem | history | state | 记忆占比 |"
        )
        L.append(
            "|---|--------|------|--------|------|-----|---------|-------|---------|"
        )
        for r in group_results:
            bd = r.get("breakdown") or {}
            total_chars = sum(bd.get(k, 0) for k in ("base", "mem", "history", "state"))
            mem_pct = (
                f"{bd.get('mem', 0) * 100 // total_chars}%"
                if total_chars > 0
                else "N/A"
            )
            L.append(
                f"| {r['turn']} | {r['prompt']} | {r['completion']} | {r['cached']}"
                f" | {bd.get('base', 'N/A')} | {bd.get('mem', 'N/A')}"
                f" | {bd.get('history', 'N/A')} | {bd.get('state', 'N/A')} | {mem_pct} |"
            )
        L.append("")

    # ── 2. A/B 对比 ───────────────────────────────────────────────────────────
    L.append("## 2. A/B 组 Prompt Token 对比\n")
    L.append("| 轮次 | A-Prompt | A-Cached | B-Prompt | B-Cached | Δ(B-A) |")
    L.append("|------|----------|----------|----------|----------|--------|")
    for a, b in zip(a_results, b_results):
        diff = (
            b["prompt"] - a["prompt"]
            if a["prompt"] >= 0 and b["prompt"] >= 0
            else "N/A"
        )
        diff_str = f"+{diff}" if isinstance(diff, int) and diff > 0 else str(diff)
        L.append(
            f"| {a['turn']} | {a['prompt']} | {a['cached']}"
            f" | {b['prompt']} | {b['cached']} | {diff_str} |"
        )
    L.append(
        f"| **合计** | **{total_a_p}** | {total_a_c}"
        f" | **{total_b_p}** | {total_b_c} | **+{total_b_p - total_a_p}** |"
    )
    L.append("")
    if total_a_p > 0:
        avg_delta = (total_b_p - total_a_p) // max(len(a_results), 1)
        L.append(
            f"> B 组比 A 组多消耗 **{total_b_p - total_a_p}** prompt tokens"
            f"（**+{growth:.1f}%**），平均每轮额外注入 **{avg_delta}** tokens。\n"
        )

    # ── 2.5. 后台调用 Token 统计 ───────────────────────────────────────────────
    L.append("## 2.5. 后台调用 Token 统计\n")
    L.append(
        "> 后台调用包括：状态更新(state_update)、闲置演化(idle_evolve)、记忆检索(memory_query)、记忆编码(memory_encode)\n"
    )
    L.append("| 组别 | 后台 Total | 对话 Total | 后台占比 |")
    L.append("|------|-----------|-----------|---------|")
    for grp_lbl, grp_p, grp_bg in [
        ("A", total_a_p, total_a_bg),
        ("B", total_b_p, total_b_bg),
    ]:
        bg_pct = grp_bg / (grp_p + grp_bg) * 100 if (grp_p + grp_bg) > 0 else 0
        L.append(f"| {grp_lbl} | {grp_bg} | {grp_p} | {bg_pct:.1f}% |")
    L.append("")
    total_bg = total_a_bg + total_b_bg
    total_dialogue = total_a_p + total_b_p
    if total_bg > 0:
        L.append(
            f"> 后台调用合计消耗 **{total_bg}** tokens，占总消耗 **{total_bg / (total_bg + total_dialogue) * 100:.1f}%**。\n"
        )

    # ── 3. 与历史基线对比 ──────────────────────────────────────────────────────
    if b_manual_a and b_manual_b:
        L.append("## 3. 与历史基线对比\n")
        L.append(
            f"> 历史基线来自 `{baseline.get('manual_test_date', '?')}` 手动测试。\n"
        )
        L.append("| 指标 | 历史基线 | 本次测试 | 变化 |")
        L.append("|------|---------|---------|------|")
        delta_a = total_a_p - b_manual_a
        delta_b = total_b_p - b_manual_b
        pct_a = delta_a / b_manual_a * 100 if b_manual_a else 0
        pct_b = delta_b / b_manual_b * 100 if b_manual_b else 0
        hist_growth = (b_manual_b - b_manual_a) / b_manual_a * 100 if b_manual_a else 0
        arrow_a = "↑" if delta_a > 0 else "↓"
        arrow_b = "↑" if delta_b > 0 else "↓"
        L.append(
            f"| A 组总 Prompt | {b_manual_a} | {total_a_p} | {arrow_a} {abs(delta_a)} ({abs(pct_a):.1f}%) |"
        )
        L.append(
            f"| B 组总 Prompt | {b_manual_b} | {total_b_p} | {arrow_b} {abs(delta_b)} ({abs(pct_b):.1f}%) |"
        )
        L.append(f"| B/A 增长率 | {hist_growth:.1f}% | {growth:.1f}% | - |")
        L.append("")
        if delta_a < 0:
            L.append(
                f"> ✅ A 组基线比历史减少 **{abs(delta_a)}** tokens（优化节省 {abs(pct_a):.1f}%）。\n"
            )
        elif delta_a > 0:
            L.append(
                f"> ⚠️ A 组基线比历史增加 **{delta_a}** tokens（+{pct_a:.1f}%），建议排查增量原因。\n"
            )
        else:
            L.append("> ✅ A 组基线与历史完全一致。\n")

    # ── 4. 延迟分析 ────────────────────────────────────────────────────────────
    latency_on = cfg.get("latency", {}).get("enabled", False)
    if latency_on:
        L.append("## 4. 延迟分析\n")
        L.append("| 轮次 | 组别 | TTFT (s) | 总耗时 (s) | Prompt Tokens |")
        L.append("|------|------|----------|------------|---------------|")
        for grp_lbl, grp in [("A", a_results), ("B", b_results)]:
            for r in grp:
                if "ttft" in r:
                    L.append(
                        f"| {r['turn']} | {grp_lbl}"
                        f" | {r.get('ttft', 'N/A')} | {r.get('total_latency', 'N/A')}"
                        f" | {r['prompt']} |"
                    )
        L.append("")

        # 简单相关性观察
        all_data = [
            (r["prompt"], r.get("total_latency"))
            for r in a_results + b_results
            if r.get("total_latency") is not None and r.get("prompt", -1) > 0
        ]
        if len(all_data) >= 4:
            max_tok = max(d[0] for d in all_data)
            max_lat = max(d[1] for d in all_data)
            min_tok = min(d[0] for d in all_data)
            min_lat = min(d[1] for d in all_data)
            L.append(
                f"> 最低 Prompt: {min_tok} tokens → 总耗时 {min_lat}s；"
                f"最高 Prompt: {max_tok} tokens → 总耗时 {max_lat}s。"
                f" Token 增长与响应总耗时通常正相关，TTFT 主要受模型首 token 推理速度影响。\n"
            )

    # ── 5. 记忆 ROI 评估 ───────────────────────────────────────────────────────
    roi_rows = [(r, "A") for r in a_results if "roi" in r] + [
        (r, "B") for r in b_results if "roi" in r
    ]
    if roi_rows:
        L.append("## 5. 记忆 ROI 评估\n")
        L.append("| 轮次 | 组别 | 检查关键词 | 命中 | ROI 分数 |")
        L.append("|------|------|----------|------|---------|")
        for r, grp in roi_rows:
            roi = r["roi"]
            L.append(
                f"| {r['turn']} | {grp} | {', '.join(roi['keywords'])}"
                f" | {', '.join(roi['hits']) or '无'} | {roi['score']} |"
            )
        L.append("")
        avg_roi = sum(r["roi"]["score"] for r, _ in roi_rows) / len(roi_rows)
        L.append(f"> 平均记忆 ROI 分数: **{avg_roi:.2f}**（1.0 = 全部关键词命中）\n")

    # ── 6. 压力测试 ────────────────────────────────────────────────────────────
    if stress_results:
        n = len(stress_results)
        L.append(f"## 6. 极端压力测试（{n} 轮）\n")
        L.append("| 轮次 | Prompt | Completion | CachedHit |")
        L.append("|------|--------|------------|-----------|")
        for r in stress_results:
            L.append(
                f"| {r['turn']} | {r['prompt']} | {r['completion']} | {r['cached']} |"
            )
        prompts = [r["prompt"] for r in stress_results if r.get("prompt", -1) > 0]
        if len(prompts) >= 5:
            last5_avg = sum(prompts[-5:]) / 5
            first5_avg = sum(prompts[:5]) / 5
            L.append(
                f"\n> 前 5 轮平均 Prompt: {first5_avg:.0f} tokens；"
                f"末 5 轮平均: {last5_avg:.0f} tokens（增长天花板估算）。"
                f" 总增长: {prompts[-1] - prompts[0]} tokens over {n} turns。\n"
            )

    # ── 7. 已启用优化清单 ──────────────────────────────────────────────────────
    L.append("## 7. 已启用 Token 优化措施\n")
    L.append("| 措施 | 状态 | 描述 |")
    L.append("|------|------|------|")
    L.append(
        "| Short-term 剥除 `<thought>` 标签 | ✅ 已启用 | assistant 消息存入前移除内心独白，避免历史重复传输 |"
    )
    L.append(
        "| Memory 检索结果字段截断 | ✅ 已启用 | episodic content≤200字, insight≤100字, graph bio≤80字 |"
    )
    L.append(
        "| 语境探照灯碎片选取 | ✅ 已启用 | graph 实体字段按关键词相关性选取最多 3 条碎片 |"
    )
    L.append(
        "| Explicit Cache (system prompt) | ✅ 已启用 | 5min TTL，cached token 费率约为正常 10% |"
    )
    L.append("")
    if b_manual_a and total_a_p > 0:
        saved = b_manual_a - total_a_p
        if saved > 0:
            L.append(
                f"> **综合节省**: 本次 A 组基线 {total_a_p} tokens，"
                f"较历史基线 {b_manual_a} tokens 节省 **{saved} tokens（{saved / b_manual_a * 100:.1f}%）**。\n"
            )

    report_text = "\n".join(L)
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\n  [报告] 已生成: {out_path.name}")
    return out_path


# ── 终端汇总表 ─────────────────────────────────────────────────────────────────


def print_summary_table(a_results: list, b_results: list):
    total_a = sum(r.get("prompt", 0) for r in a_results if r.get("prompt", -1) >= 0)
    total_b = sum(r.get("prompt", 0) for r in b_results if r.get("prompt", -1) >= 0)
    growth = (total_b - total_a) / total_a * 100 if total_a > 0 else 0

    print("\n" + "=" * 72)
    print("对比汇总")
    print("=" * 72)
    print(
        f"{'轮次':<5} {'A-Prompt':>10} {'A-Cached':>10} {'B-Prompt':>10} {'B-Cached':>10} {'Δ(B-A)':>8}"
    )
    print("-" * 72)
    for a, b in zip(a_results, b_results):
        diff = b["prompt"] - a["prompt"] if a["prompt"] >= 0 else 0
        print(
            f"  轮{a['turn']}  {a['prompt']:>10}  {a.get('cached', 0):>9}  "
            f"{b['prompt']:>10}  {b.get('cached', 0):>9}  +{diff:>6}"
        )
    print("-" * 72)
    print(
        f"{'合计':<5} {total_a:>10}  {'':>9}  {total_b:>10}  {'':>9}  "
        f"+{total_b - total_a:>6} (+{growth:.1f}%)"
    )
    print("=" * 72)

    # A 组轮间 Prompt 增量
    print("\nA 组轮间 Prompt 增量（短期历史积累）:")
    for i in range(1, len(a_results)):
        prev = a_results[i - 1].get("prompt", -1)
        curr = a_results[i].get("prompt", -1)
        if prev > 0 and curr > 0:
            print(f"  轮{i} → 轮{i + 1}: +{curr - prev} tokens")


# ── 主流程 ─────────────────────────────────────────────────────────────────────


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Ember Benchmark Runner")
    parser.add_argument(
        "--stress",
        action="store_true",
        help="强制开启压力测试（覆盖 utils/test_config.json）",
    )
    parser.add_argument(
        "--stress-turns", type=int, default=None, help="压力测试轮数（覆盖配置）"
    )
    parser.add_argument("--no-latency", action="store_true", help="关闭延迟记录")
    parser.add_argument("--skip-b", action="store_true", help="只跑 A 组（跳过 B 组）")
    args = parser.parse_args()

    print("\nEmber Benchmark Runner v1.0")
    print("=" * 50)

    # ── 加载脚本和配置 ──
    if not SCRIPT_FILE.exists():
        print(f"[错误] 找不到 {SCRIPT_FILE}，请先创建 test_script.json")
        sys.exit(1)
    with open(SCRIPT_FILE, encoding="utf-8") as f:
        script_meta = json.load(f)
    turns = script_meta.get("turns", [])
    if not turns:
        print("[错误] test_script.json 中 'turns' 为空")
        sys.exit(1)

    cfg: dict = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)

    # 命令行覆盖
    if args.stress:
        cfg.setdefault("stress", {})["enabled"] = True
    if args.stress_turns:
        cfg.setdefault("stress", {})["turns"] = args.stress_turns
    if args.no_latency:
        cfg.setdefault("latency", {})["enabled"] = False

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── 步骤 1: 锁定环境变量 ──
    print("\n[步骤 1] 环境变量控制...")
    backup_and_lock_env()

    # ── 步骤 2: 白板重置 ──
    print("\n[步骤 2] 白板重置（清除所有记忆层）...")
    reset_memory()
    print("  完成，等待 2 秒...")
    time.sleep(2)

    # ── 步骤 3: 初始化模块 ──
    print("\n[步骤 3] 初始化模块...")
    modules = init_modules()
    time.sleep(1)

    from config.settings import settings

    print(f"  [验证] LLM_TEMPERATURE = {settings.LLM_TEMPERATURE}")

    # ── 步骤 4: A 组 ──
    print(f"\n[A 组] 白板状态（{len(turns)} 轮）\n")
    a_results = run_group("A", modules, turns, script_meta, cfg)
    print(f"\n  A 组完成，等待 3 秒让记忆写入稳定...")
    time.sleep(3)

    b_results = []
    if not args.skip_b:
        # ── 步骤 5: B 组 ──
        print(f"\n[B 组] 记忆累积后（{len(turns)} 轮）\n")
        b_results = run_group("B", modules, turns, script_meta, cfg)

    # ── 步骤 6: 压力测试（可选）──
    stress_results = None
    stress_cfg = cfg.get("stress", {})
    if stress_cfg.get("enabled", False):
        n = stress_cfg.get("turns", 20)
        print(f"\n[步骤 6] 压力测试准备：白板重置...")
        reset_memory()
        time.sleep(1)
        stress_modules = init_modules()
        time.sleep(1)
        stress_results = run_stress_test(stress_modules, turns, n, cfg)
        try:
            stress_modules["heartbeat"].stop()
        except Exception:
            pass

    # ── 停止心跳 ──
    try:
        modules["heartbeat"].stop()
    except Exception:
        pass

    # ── 终端汇总 ──
    if b_results:
        print_summary_table(a_results, b_results)
    else:
        print(
            f"\nA 组合计 Prompt: {sum(r.get('prompt', 0) for r in a_results if r.get('prompt', -1) >= 0)} tokens"
        )

    # ── 生成 Markdown 报告 ──
    print("\n[报告] 生成 Markdown 报告...")
    report_path = generate_report(
        a_results, b_results, script_meta, cfg, stress_results, run_ts
    )

    print(f"\n✅ 基准测试完成！报告: {report_path.name}")


if __name__ == "__main__":
    main()
