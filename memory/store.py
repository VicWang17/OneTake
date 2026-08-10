"""记忆存储与合并（P6）：memories 表的 CRUD + LLM 语义归并。

设计要点（Mem0 式轻量实现）：
- 新记忆不直接插表：LLM 对比现有记忆后输出决策（add/merge/contradict），代码执行
- confidence：新条 0.5 起步；merge +0.15（封顶 0.95）；contradict 旧条 -0.2
- 低于 0.3 的记忆不再注入（软删除，可审计不物理删除）
"""

import json
import time
import uuid

from db import dao
from gateway import core as gw

CONF_NEW = 0.5
CONF_MERGE_DELTA = 0.15
CONF_CONTRADICT_DELTA = -0.2
CONF_CAP = 0.95
CONF_INJECT_MIN = 0.3   # 低于此不注入（软删除）

MERGE_SYSTEM = """你是记忆管理员。用户给一条新记忆和现有记忆清单（同类型），你判断如何处理，输出严格 JSON：
- 与现有记忆无语义重合：{"action": "add"}
- 与某条语义重合（应归并）：{"action": "merge", "target": "记忆id", "merged_content": "归并后的完整表述", "reason": "一句话"}
- 与某条相互矛盾：{"action": "contradict", "target": "记忆id", "reason": "一句话"}
只输出 JSON。"""


def add(type_: str, content: str, project_id: str | None = None) -> dict:
    """添加记忆（经 LLM 合并决策）。返回执行结果。"""
    conn = dao.get_conn()
    existing = conn.execute(
        "SELECT id, content, confidence FROM memories WHERE type = ?",
        (type_,)).fetchall()

    action = {"action": "add"}
    if existing:
        catalog = "\n".join(f'[{r["id"]}] "{r["content"]}"（置信度 {r["confidence"]:.2f}）'
                            for r in existing)
        r = gw.call("llm", {
            "system": MERGE_SYSTEM,
            "user": f"新记忆：{content}\n\n现有记忆：\n{catalog}",
        }, project_id=project_id)
        action = r["data"]

    act = action.get("action", "add")
    target = action.get("target")
    valid_target = target and any(r["id"] == target for r in existing)

    if act == "merge" and valid_target and action.get("merged_content"):
        old = next(r for r in existing if r["id"] == target)
        new_conf = min(CONF_CAP, old["confidence"] + CONF_MERGE_DELTA)
        conn.execute(
            "UPDATE memories SET content = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (action["merged_content"], new_conf,
             time.strftime("%Y-%m-%d %H:%M:%S"), target))
        result = {"action": "merge", "id": target,
                  "content": action["merged_content"], "confidence": new_conf}
    elif act == "contradict" and valid_target:
        old = next(r for r in existing if r["id"] == target)
        new_conf = max(0.0, old["confidence"] + CONF_CONTRADICT_DELTA)
        conn.execute("UPDATE memories SET confidence = ?, updated_at = ? WHERE id = ?",
                     (new_conf, time.strftime("%Y-%m-%d %H:%M:%S"), target))
        mid = _insert(conn, type_, content)
        result = {"action": "contradict", "weakened": target, "new_id": mid,
                  "content": content, "confidence": CONF_NEW}
    else:  # add（含决策非法时的兜底）
        mid = _insert(conn, type_, content)
        result = {"action": "add", "id": mid, "content": content,
                  "confidence": CONF_NEW}
    conn.commit()
    conn.close()
    return result


def _insert(conn, type_: str, content: str) -> str:
    mid = uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO memories (id, type, content, confidence) VALUES (?, ?, ?, ?)",
        (mid, type_, content, CONF_NEW))
    return mid


def list_all(type_: str | None = None, min_confidence: float = 0.0) -> list[dict]:
    conn = dao.get_conn()
    sql = "SELECT * FROM memories WHERE confidence >= ?"
    params: list = [min_confidence]
    if type_:
        sql += " AND type = ?"
        params.append(type_)
    rows = conn.execute(sql + " ORDER BY confidence DESC", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
