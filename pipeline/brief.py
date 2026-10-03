"""CreativeBrief（P1）：创作意图与硬约束的结构化载体。

定位：项目的「这条视频要满足什么」的唯一事实源。约束分三级来源，
冲突时高优先级覆盖并留痕（changelog），不静默改值：
    user_current（本轮用户请求）> project_confirmed（已确认项目约束）> memory（历史偏好）
临时性选择（user_current）只影响本项目，不回写 memories 表升级成长期偏好。

持久化：projects/{pid}/brief.json（文档型数据，与 script.json 同级，不入 DB）。
"""

import json
import time
from pathlib import Path
from typing import Any, TypedDict

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

# 来源优先级（数值大者胜）
PRIORITY = {"memory": 1, "project_confirmed": 2, "user_current": 3}


class Constraint(TypedDict):
    value: Any
    source: str   # user_current / project_confirmed / memory
    since: str    # 写入时间


class CreativeBrief(TypedDict, total=False):
    audience: str               # 受众
    purpose: str                # 目的
    duration: Constraint | None     # 目标时长（秒）
    style: Constraint | None        # 风格
    required: list[Constraint]      # 必需内容
    forbidden: list[Constraint]     # 禁止内容
    budget_cny: float | None        # 预算上限
    acceptance: list[str]           # 验收条件
    changelog: list[dict]           # 约束变更留痕（含冲突处理）
    version: int


def new_brief(**fields) -> CreativeBrief:
    brief: CreativeBrief = {"version": 1, "changelog": [],
                            "required": [], "forbidden": [], "acceptance": []}
    brief.update({k: v for k, v in fields.items() if v is not None})
    return brief


def _path(pid: str) -> Path:
    return PROJECTS_DIR / pid / "brief.json"


def load_brief(pid: str) -> CreativeBrief | None:
    p = _path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_brief(pid: str, brief: CreativeBrief) -> Path:
    p = _path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def set_constraint(brief: CreativeBrief, slot: str, value: Any, source: str,
                   note: str = "") -> CreativeBrief:
    """写入一条约束（duration/style 槽位）。与既有约束冲突时按优先级裁决：
    新来源优先级 ≥ 既有 → 覆盖并记 changelog；否则拒绝并记 changelog（留痕可查）。"""
    if source not in PRIORITY:
        raise ValueError(f"未知来源: {source}")
    old = brief.get(slot)
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "slot": slot,
             "new": {"value": value, "source": source}, "note": note}
    if old and PRIORITY[source] < PRIORITY[old["source"]]:
        entry["action"] = "rejected_lower_priority"
        entry["old"] = old
        brief.setdefault("changelog", []).append(entry)
        return brief
    entry["action"] = "overridden" if old else "set"
    if old:
        entry["old"] = old
    brief[slot] = Constraint(value=value, source=source,
                             since=time.strftime("%Y-%m-%d %H:%M:%S"))
    brief.setdefault("changelog", []).append(entry)
    brief["version"] = brief.get("version", 1) + 1
    return brief


def add_list_constraint(brief: CreativeBrief, kind: str, value: str,
                        source: str) -> CreativeBrief:
    """追加必需/禁止内容（kind: required/forbidden）。同值去重；与既有高优先级
    同值项冲突时（如 memory 禁止 vs user 必需同一元素）以高优先级者为准并留痕。"""
    if source not in PRIORITY:
        raise ValueError(f"未知来源: {source}")
    items = brief.setdefault(kind, [])
    for it in items:
        if it["value"] == value:
            if PRIORITY[source] >= PRIORITY[it["source"]]:
                it.update(value=value, source=source,
                          since=time.strftime("%Y-%m-%d %H:%M:%S"))
            brief.setdefault("changelog", []).append(
                {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "slot": kind,
                 "new": {"value": value, "source": source}, "action": "dedup"})
            return brief
    items.append(Constraint(value=value, source=source,
                            since=time.strftime("%Y-%m-%d %H:%M:%S")))
    brief["version"] = brief.get("version", 1) + 1
    return brief


def hard_constraints(brief: CreativeBrief) -> dict:
    """当前生效的硬约束视图（验收用）：时长/风格 + 必需/禁止清单（纯值）。"""
    return {
        "duration": brief["duration"]["value"] if brief.get("duration") else None,
        "style": brief["style"]["value"] if brief.get("style") else None,
        "required": [c["value"] for c in brief.get("required", [])],
        "forbidden": [c["value"] for c in brief.get("forbidden", [])],
    }
