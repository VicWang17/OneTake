"""超长工具结果外置（P1）：大结果存文件，上下文留引用摘要。

动机：动辄几千字的工具返回（厂商原始响应、长校验清单）直接进上下文会
挤占预算且稀释关键信息。外置后上下文只留：状态、错误、关键数字、文件
路径与读取方式——需要细节时按路径取回（「引用优于内联」）。

阈值 DEFAULT_THRESHOLD 字符以下原样内联（小结果不值得外置）。
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

DEFAULT_THRESHOLD = 2000
# 摘要保留的关键字段（状态/错误/关键数字）
_KEY_FIELDS = ("status", "error", "cost", "task_id", "duration", "passed")


def externalize(pid: str, name: str, payload: Any,
                threshold: int = DEFAULT_THRESHOLD) -> dict:
    """超限外置：写 projects/{pid}/refs/{name}.txt，返回引用摘要；未超限返回 {"inline": payload}。"""
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2)
    if len(text) <= threshold:
        return {"inline": payload}

    out = PROJECTS_DIR / pid / "refs" / f"{name}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    summary = {"ref": str(out), "chars": len(text),
               "read": f"cat '{out}'"}
    if isinstance(payload, dict):
        kept = {k: payload[k] for k in _KEY_FIELDS if k in payload}
        summary.update(kept)
        summary["keys"] = list(payload.keys())[:10]
    return summary


def ref_line(ref: dict) -> str:
    """引用摘要 → 上下文中的一行说明（替代原文本分节）。"""
    parts = [f"结果过大（{ref['chars']} 字符）已外置：{ref['ref']}"]
    for k in _KEY_FIELDS:
        if k in ref:
            parts.append(f"{k}={ref[k]}")
    parts.append(f"读取方式：{ref['read']}")
    return "；".join(parts)
