"""大纲生成节点（P1）：选题 → 大纲 JSON + LLM 自选风格（项目级锁定）。

设计：两阶段生成的第一阶段。LLM 在大纲阶段自行设计 style（tone/visual/voice），
固化进 script.json 与 projects.style_json，后续所有分镜 prompt 统一注入——
"LLM 选型，项目内锁定"，P6 Skill 选择器的雏形。
"""

from db import dao
from gateway import core as gw

OUTLINE_SYSTEM = """你是短视频策划。用户给一个选题，输出严格 JSON 大纲：
{
  "title": "视频标题",
  "logline": "一句话主题",
  "audience": "目标受众",
  "target_duration": 60,
  "structure": [{"part": "段落名", "summary": "该段讲什么"}],
  "style": {
    "tone": "语言风格（语速感、句式、信息密度）",
    "visual": "画面风格（画风、配色、构图偏好；避免人物大场景，优先扁平插画/物件/风景）",
    "voice": "配音建议（音色、语速）"
  }
}
要求：
1. style 由你根据题材自行设计，定下来就是全片统一标准，后续镜头不得偏离；
2. structure 4-6 段，覆盖 钩子 → 展开 → 收尾；
3. target_duration 取 50-90 之间的整数（秒）；
4. 只输出 JSON，不要任何其他文字。"""

OUTLINE_SYSTEM_SKILLED = """你是短视频策划。用户给一个选题，并给定创作方法论（Skill）的结构与风格，你输出严格 JSON 大纲：
{
  "title": "视频标题",
  "logline": "一句话主题",
  "audience": "目标受众",
  "target_duration": 60,
  "structure": [{"part": "段落名", "summary": "该段讲什么"}],
  "style": {"tone": "……", "visual": "……", "voice": "……"}
}
要求：
1. structure 必须按 Skill 给定的叙事结构展开，每段对应 structure 数组的一项；
2. style 必须使用 Skill 给定的 tone/visual/voice（可适度具体化，但不得偏离）；
3. target_duration 取 50-90 之间的整数（秒）；
4. 只输出 JSON，不要任何其他文字。"""

REQUIRED_KEYS = {"title", "logline", "audience", "target_duration", "structure", "style"}


def generate_outline(topic: str, project_id: str, feedback: str | None = None,
                     skill: dict | None = None, memory_block: str = "") -> dict:
    """选题 → 大纲 dict（经网关计费）。校验缺字段即抛错（重试在调用方）。
    feedback：脚本确认节点打回时的修改意见，注入 prompt 重新生成。
    skill：选中的 Skill（P6）——结构与风格由 YAML 给定，不再 LLM 自决。
    memory_block：P6 记忆注入段（偏好与经验，一次注入全程生效）。"""
    user = f"选题：{topic}"
    if skill:
        d = skill["data"]
        user += (f"\n\n创作方法论（Skill「{skill['name']}」，必须遵循）："
                 f"\n叙事结构：{' → '.join(d['structure'])}"
                 f"\n语言风格：{d['style']['tone']}"
                 f"\n画面风格：{d['style']['visual']}"
                 f"\n配音建议：{d['style']['voice']}")
    if feedback:
        user += f"\n\n上次的大纲被退回，修改意见（请采纳并重新设计）：{feedback}"
    user += memory_block  # 记忆注入放最后（近因效应位置）
    system = OUTLINE_SYSTEM_SKILLED if skill else OUTLINE_SYSTEM
    r = gw.call("llm", {"system": system, "user": user},
                project_id=project_id)
    data = r["data"]
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise ValueError(f"大纲缺字段: {missing}")
    if not isinstance(data["structure"], list) or not data["structure"]:
        raise ValueError("structure 必须是非空数组")
    return data
