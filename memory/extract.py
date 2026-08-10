"""经验记忆提取（P6）：失败记录 → LLM 归纳 → 规避性教训（episode 记忆）。

画像记忆（profile）走手动 add（人工确认节点的意见经人确认后写入），
经验记忆（episode）从 analyze 的失败聚类自动提取——客观事实无需人工确认。
"""

import json

from db import dao
from gateway import core as gw
from memory import store

EXTRACT_SYSTEM = """你是经验提炼员。用户给一组系统运行中的失败记录，你归纳出 1-3 条对未来创作有指导价值的经验教训。
输出严格 JSON：{"lessons": ["……", "……"]}
要求：
1. 每条是具体可执行的教训（如"某模型对某类画面不稳定，应避免"），不是泛泛而谈；
2. 纯故障类（网络、熔断、演练注入）不构成创作教训，跳过；
3. 只输出 JSON。"""


def extract_from_failures(project_id: str | None = None) -> list[dict]:
    """从 generations 失败记录提取经验记忆并入库。返回新增/合并结果。"""
    conn = dao.get_conn()
    sql = "SELECT model, error FROM generations WHERE status = 'failed'"
    params: list = []
    if project_id:
        sql += " AND project_id = ?"
        params.append(project_id)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        return []

    catalog = "\n".join(f"- [{r['model']}] {(r['error'] or '')[:80]}" for r in rows)
    r = gw.call("llm", {
        "system": EXTRACT_SYSTEM,
        "user": f"失败记录：\n{catalog}",
    }, project_id=project_id)
    lessons = r["data"].get("lessons", [])
    return [store.add("episode", lesson, project_id) for lesson in lessons]
