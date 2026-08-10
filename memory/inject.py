"""记忆注入（P6）：新项目启动时检索相关记忆 → 拼入大纲 prompt。

检索策略：规模小（几十条），LLM 按选题语义挑 Top-K，优于向量检索；
注入排序 = 置信度降序（位置即优先级的软信号）。
无记忆时返回空串——prompt 零变化，记忆是增强不是门槛。
"""

import json

from gateway import core as gw
from memory import store

PICK_SYSTEM = """你是记忆检索员。用户给一个视频选题和候选记忆清单，你挑出与本次创作最相关的至多 K 条。
输出严格 JSON：{"pick": ["记忆id", ...]}（按相关度排序，没有相关的输出空数组）。
只输出 JSON。"""


def get_relevant(topic: str, k: int = 3, project_id: str | None = None) -> list[dict]:
    """选题 → Top-K 相关记忆（置信度 ≥ 注入阈值，按置信度排序）。"""
    candidates = store.list_all(min_confidence=store.CONF_INJECT_MIN)
    if not candidates:
        return []
    catalog = "\n".join(f'[{m["id"]}]（{m["type"]}，置信度 {m["confidence"]:.2f}）'
                        f'{m["content"]}' for m in candidates)
    r = gw.call("llm", {
        "system": PICK_SYSTEM,
        "user": f"选题：{topic}\nK={k}\n\n候选记忆：\n{catalog}",
    }, project_id=project_id)
    picked = r["data"].get("pick", [])
    by_id = {m["id"]: m for m in candidates}
    return [by_id[i] for i in picked if i in by_id][:k]


def format_for_prompt(memories: list[dict]) -> str:
    """把选中的记忆格式化为 prompt 注入段。"""
    if not memories:
        return ""
    lines = "\n".join(f"- {m['content']}" for m in memories)
    return f"\n\n【创作偏好与经验（请遵循）】\n{lines}"
