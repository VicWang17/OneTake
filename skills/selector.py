"""Skill 选择器（P6）：选题 → 最匹配的 Skill。

LLM 只做匹配判断（读各 Skill 的 match 段），不做创作——
从 P1"LLM 现场编风格"升级为"LLM 从库里选"（受控选择）。
无匹配时返回 None → 管线回退到 LLM 自决风格（Skill 是增强不是门槛）。
"""

import json

from gateway import core as gw
from skills import loader

SELECT_SYSTEM = """你是内容策划。用户给一个视频选题，你从已注册的 Skill 列表中选最匹配的一个。
输出严格 JSON：{"skill": "Skill 名称或 null", "reason": "一句话理由（20 字内）"}
规则：
1. 依据每个 Skill 的 match.genres（适用题材）与选题的语义匹配度判断；
2. 没有明确匹配时输出 null（宁缺毋滥，不要硬选）；
3. 只输出 JSON。"""


def choose_skill(topic: str, project_id: str | None = None) -> dict:
    """返回 {"skill": name|None, "reason": str}。"""
    available = loader.list_skills()
    if not available:
        return {"skill": None, "reason": "无已注册 Skill"}
    catalog = "\n".join(
        f"- {s['name']}：适用题材 {s['data']['match'].get('genres')}"
        for s in available)
    r = gw.call("llm", {
        "system": SELECT_SYSTEM,
        "user": f"选题：{topic}\n\n已注册 Skill：\n{catalog}",
    }, project_id=project_id)
    skill = r["data"].get("skill")
    if skill and not any(s["name"] == skill for s in available):
        return {"skill": None, "reason": f"选择器返回了未注册名称 {skill}，按无匹配处理"}
    return {"skill": skill, "reason": r["data"].get("reason", "")}
