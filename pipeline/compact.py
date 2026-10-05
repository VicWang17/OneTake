"""摘要压缩（P1）：交互历史 → 结构化摘要，硬约束压缩免疫。

分工：硬约束（时长/禁止元素/风格）存 brief.json——结构化、永不被压缩；
交互历史（确认/反馈/打回）落 projects/{pid}/interactions.jsonl——叙述性、
会膨胀，超过阈值由 LLM 压缩为 summary.json，schema 按 plan：
facts（关键事实）/ open_questions（未解决问题）/ decisions（决策依据）/
artifact_refs（产物引用）。

压缩后恢复用 recovery_context()：brief 硬约束 + 最新摘要 + 未覆盖的新交互，
保证「三轮反馈 + 一次压缩」后硬约束仍可准确恢复（cc-06 场景）。
"""

import json
import time
from pathlib import Path

from gateway import core as gw
from pipeline import brief as brief_mod

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

COMPACT_THRESHOLD = 10  # 交互日志超过此条数触发压缩

COMPACT_SYSTEM = """你是项目记忆压缩器。给你一段创作项目的交互记录（JSONL），压缩为结构化摘要，输出严格 JSON：
{
  "facts": ["仍有效的关键事实/决定，每条一句话"],
  "open_questions": ["尚未解决的问题或待用户决定事项"],
  "decisions": ["关键决策及一句话依据"],
  "artifact_refs": ["提到的产物文件/路径"]
}
规则：只保留对后续工作有指导价值的信息；过场的寒暄、已被后续推翻的早期决定（除留一句依据外）丢弃；只输出 JSON。"""


def _log_path(pid: str) -> Path:
    return PROJECTS_DIR / pid / "interactions.jsonl"


def _summary_path(pid: str) -> Path:
    return PROJECTS_DIR / pid / "summary.json"


def log_interaction(pid: str, kind: str, content: str) -> None:
    """追加一条交互记录。kind：confirm_script / confirm_images / feedback / user_request 等。"""
    p = _log_path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "kind": kind, "content": content},
                           ensure_ascii=False) + "\n")


def _read_log(pid: str) -> list[dict]:
    p = _log_path(pid)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def needs_compaction(pid: str) -> bool:
    covered = 0
    s = _summary_path(pid)
    if s.exists():
        covered = json.loads(s.read_text(encoding="utf-8")).get("covered_entries", 0)
    return len(_read_log(pid)) - covered >= COMPACT_THRESHOLD


def compact(pid: str) -> dict | None:
    """压缩未覆盖的交互记录为结构化摘要（brief 硬约束自动汇入 facts）。
    无待压缩记录返回 None。"""
    entries = _read_log(pid)
    s_path = _summary_path(pid)
    covered = 0
    if s_path.exists():
        covered = json.loads(s_path.read_text(encoding="utf-8")).get("covered_entries", 0)
    pending = entries[covered:]
    if not pending:
        return None

    brief = brief_mod.load_brief(pid)
    hard = brief_mod.hard_constraints(brief) if brief else {}

    lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in pending)
    r = gw.call("llm", {"system": COMPACT_SYSTEM,
                        "user": f"交互记录（{len(pending)} 条）：\n{lines}"},
                project_id=pid)
    data = r["data"]

    summary = {
        "facts": list(data.get("facts", [])),
        "open_questions": list(data.get("open_questions", [])),
        "decisions": list(data.get("decisions", [])),
        "artifact_refs": list(data.get("artifact_refs", [])),
        "covered_entries": len(entries),
        "compact_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 硬约束压缩免疫：brief 的当前生效约束无条件汇入 facts（结构化事实不依赖 LLM 摘要质量）
    if hard.get("duration"):
        summary["facts"].insert(0, f"硬约束-目标时长：{hard['duration']} 秒")
    if hard.get("style"):
        summary["facts"].insert(0, f"硬约束-指定风格：{hard['style']}")
    for item in hard.get("forbidden", []):
        summary["facts"].insert(0, f"硬约束-禁止：{item}")
    for item in hard.get("required", []):
        summary["facts"].insert(0, f"硬约束-必需：{item}")

    s_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def recovery_context(pid: str) -> dict:
    """压缩/中断后的恢复上下文：硬约束 + 最新摘要 + 未压缩的新交互。"""
    brief = brief_mod.load_brief(pid)
    s_path = _summary_path(pid)
    summary = json.loads(s_path.read_text(encoding="utf-8")) if s_path.exists() else None
    covered = summary.get("covered_entries", 0) if summary else 0
    return {
        "hard_constraints": brief_mod.hard_constraints(brief) if brief else {},
        "summary": summary,
        "new_interactions": _read_log(pid)[covered:],
    }
