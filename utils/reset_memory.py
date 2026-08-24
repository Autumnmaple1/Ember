"""
Ember 记忆清除工具 — 白板测试专用

用法:
  python utils/reset_memory.py            # 全量重置（带确认提示）
  python utils/reset_memory.py -y         # 全量重置（无确认）
  python utils/reset_memory.py --dry-run  # 预览将要执行的操作，不实际修改

选择性重置:
  python utils/reset_memory.py --short-term   # 仅清除对话历史
  python utils/reset_memory.py --state        # 仅重置情绪状态
  python utils/reset_memory.py --postgres     # 清空 PostgreSQL 记忆与实体关系表

可选扩展:
  python utils/reset_memory.py --all --audio --logs -y
"""

import argparse
import json
import os
import sys
from glob import glob
from pathlib import Path

# 将项目根目录加入 sys.path，使 config.settings 可直接导入
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _abs(rel: str) -> Path:
    """将相对路径解析为绝对路径（相对于项目根目录）。"""
    return ROOT / rel


def _print_result(label: str, msg: str, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {label:<16} {msg}")


# ──────────────────────────────────────────────
# 各层重置函数
# ──────────────────────────────────────────────

def reset_short_term(dry_run: bool = False) -> bool:
    """清除短期对话记忆：chat_memory.json 写入 []，chat_history.log 清空。"""
    memory_file = _abs("config/chat_memory.json")
    history_file = _abs("config/chat_history.log")

    # 统计当前条数（用于输出汇报）
    count = 0
    if memory_file.exists():
        try:
            data = json.loads(memory_file.read_text(encoding="utf-8"))
            count = len(data) if isinstance(data, list) else 0
        except Exception:
            count = -1

    if dry_run:
        _print_result("短期记忆", f"[预览] 将清除 {count} 条消息 + 对话日志")
        return True

    try:
        memory_file.write_text("[]", encoding="utf-8")
        history_file.write_text("", encoding="utf-8")
        _print_result("短期记忆", f"已清除 {count} 条消息 + 对话日志")
        return True
    except Exception as e:
        _print_result("短期记忆", f"失败: {e}", ok=False)
        return False


def reset_state(dry_run: bool = False) -> bool:
    """将 state.json 覆盖为 state_default.json 的出厂默认值。"""
    default_file = _abs("config/state_default.json")
    state_file = _abs("config/state.json")

    if not default_file.exists():
        _print_result("状态快照", "找不到 state_default.json，跳过", ok=False)
        return False

    try:
        default_data = json.loads(default_file.read_text(encoding="utf-8"))
    except Exception as e:
        _print_result("状态快照", f"读取默认状态失败: {e}", ok=False)
        return False

    scenario = str(default_data.get("客观情境", ""))[:30]

    if dry_run:
        _print_result("状态快照", f"[预览] 将重置为: P={default_data.get('P')} A={default_data.get('A')} D={default_data.get('D')}  场景: {scenario}…")
        return True

    try:
        state_file.write_text(
            json.dumps(default_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _print_result("状态快照", f"已重置: P={default_data.get('P')} A={default_data.get('A')} D={default_data.get('D')}  场景: {scenario}…")
        return True
    except Exception as e:
        _print_result("状态快照", f"失败: {e}", ok=False)
        return False


def reset_postgres(dry_run: bool = False) -> bool:
    """清空 PostgreSQL 中的对话、情景记忆和实体关系表。"""
    try:
        import psycopg2
        from config.settings import settings
    except ImportError as e:
        _print_result("PostgreSQL", f"依赖缺失: {e}  →  {sys.executable}", ok=False)
        return False

    try:
        conn = psycopg2.connect(
            dbname=settings.PG_DB,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            connect_timeout=5,
        )
        # autocommit=True：每条语句独立执行，避免某条 SELECT COUNT 失败后
        # 事务进入 aborted 状态，导致后续 TRUNCATE 被拒绝
        conn.autocommit = True
    except Exception as e:
        _print_result("PostgreSQL", f"连接失败（数据库可能未启动）: {e}", ok=False)
        return False

    # 先列关系表，再列实体表，顺序也便于 dry-run 输出理解依赖关系。
    tables = [
        "knowledge_relations",
        "knowledge_entities",
        "episodic_memory",
        "message_list",
        "state_list",
    ]
    counts: dict[str, int] = {}

    try:
        with conn.cursor() as cur:
            for tbl in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    counts[tbl] = cur.fetchone()[0]
                except Exception:
                    counts[tbl] = -1  # 表不存在时记为 -1，不影响下一张表

            def _fmt(tbl: str) -> str:
                return f"{tbl}={'不存在' if counts[tbl] == -1 else counts[tbl]}"

            if dry_run:
                _print_result("PostgreSQL", f"[预览] 将清除: {', '.join(_fmt(t) for t in tables)}")
                return True

            # 逐表单独 TRUNCATE，跳过不存在的表（counts == -1）
            for tbl in tables:
                if counts[tbl] >= 0:
                    cur.execute(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE")

        _print_result("PostgreSQL", f"已清除 ({', '.join(_fmt(t) for t in tables)})")
        return True

    except Exception as e:
        _print_result("PostgreSQL", f"操作失败: {e}", ok=False)
        return False
    finally:
        conn.close()


def reset_audio(dry_run: bool = False) -> bool:
    """删除 data/audio/ 目录下所有 .mp3 文件。"""
    audio_dir = _abs("data/audio")
    files = list(audio_dir.glob("*.mp3")) if audio_dir.exists() else []

    if dry_run:
        _print_result("TTS 音频", f"[预览] 将删除 {len(files)} 个 .mp3 文件")
        return True

    deleted = 0
    failed = 0
    for f in files:
        try:
            f.unlink()
            deleted += 1
        except Exception:
            failed += 1

    msg = f"已删除 {deleted} 个文件"
    if failed:
        msg += f"（{failed} 个失败）"
    _print_result("TTS 音频", msg, ok=failed == 0)
    return failed == 0


def reset_logs(dry_run: bool = False) -> bool:
    """清空 data/logs/system.log 运行日志。"""
    log_file = _abs("data/logs/system.log")

    if not log_file.exists():
        _print_result("系统日志", "日志文件不存在，跳过")
        return True

    size_kb = log_file.stat().st_size // 1024

    if dry_run:
        _print_result("系统日志", f"[预览] 将清空日志（当前 {size_kb} KB）")
        return True

    try:
        log_file.write_text("", encoding="utf-8")
        _print_result("系统日志", f"已清空（原 {size_kb} KB）")
        return True
    except Exception as e:
        _print_result("系统日志", f"失败: {e}", ok=False)
        return False


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def main() -> None:
    # 强制 UTF-8 输出，避免 Windows GBK 终端乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Ember 记忆清除工具 — 白板测试专用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-a", "--all",        action="store_true", help="清除全部（默认行为）")
    parser.add_argument("-s", "--short-term", action="store_true", help="清除短期对话历史")
    parser.add_argument(      "--state",      action="store_true", help="重置情绪状态到出厂默认")
    parser.add_argument("-p", "--postgres",   action="store_true", help="清空 PostgreSQL 长期记忆")
    parser.add_argument(      "--audio",      action="store_true", help="删除 TTS 音频缓存（可选）")
    parser.add_argument(      "--logs",       action="store_true", help="清空系统日志（可选）")
    parser.add_argument("-y", "--yes",        action="store_true", help="跳过确认提示")
    parser.add_argument(      "--dry-run",    action="store_true", help="预览模式：显示操作但不实际执行")

    args = parser.parse_args()

    # 确定要执行的操作
    explicit = args.short_term or args.state or args.postgres
    do_short_term = args.all or args.short_term or not explicit
    do_state      = args.all or args.state      or not explicit
    do_postgres   = args.all or args.postgres   or not explicit
    do_audio      = args.audio
    do_logs       = args.logs

    dry_run = args.dry_run

    # ── 输出标题
    prefix = "[预览模式] " if dry_run else ""
    print(f"\nEmber 记忆清除工具  {prefix}")
    print("=" * 40)
    print(f"Python: {sys.executable}")
    print("将执行以下操作:")
    if do_short_term: print("  [1] 短期记忆  (chat_memory.json + chat_history.log)")
    if do_state:      print("  [2] 状态快照  (state.json → state_default.json)")
    if do_postgres:   print("  [3] PostgreSQL (对话、情景记忆、实体和关系)")
    if do_audio:      print("  [4] TTS 音频   (data/audio/*.mp3)")
    if do_logs:       print("  [5] 系统日志   (data/logs/system.log)")
    print()

    # ── 确认提示
    if not dry_run and not args.yes:
        try:
            answer = input("确认执行？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)
        if answer not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    print()

    # ── 执行各层重置
    results: list[bool] = []
    if do_short_term: results.append(reset_short_term(dry_run))
    if do_state:      results.append(reset_state(dry_run))
    if do_postgres:   results.append(reset_postgres(dry_run))
    if do_audio:      results.append(reset_audio(dry_run))
    if do_logs:       results.append(reset_logs(dry_run))

    # ── 汇总
    print()
    if dry_run:
        print("=" * 40)
        print("预览完成，未修改任何数据。")
    else:
        ok_count = sum(1 for r in results if r)
        total = len(results)
        print("=" * 40)
        if ok_count == total:
            print(f"重置完成（{ok_count}/{total} 项成功）。")
            print("重启 Ember 即可开始新的白板测试。")
        else:
            print(f"部分完成（{ok_count}/{total} 项成功），请检查以上错误信息。")
    print()


if __name__ == "__main__":
    main()
