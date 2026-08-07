"""角色设定表节点（P1）：生成项目级角色/视觉锚点描述（文本一致性手段）。

设计：分镜图逐张独立生成，模型无记忆，角色会"换脸"。本节点把角色的固定外观
（造型/配色/标志性元素）钉成一段文本常量，1.4 出图时拼进每个 visual_prompt。
零成本基础一致性手段；参考图链（图像锚点）在 1.4 叠加。
"""

import json

from gateway import core as gw

CHARACTER_SYSTEM = """你是角色设计师。根据视频大纲和分镜，设计贯穿全片的视觉锚点，输出严格 JSON：
{
  "character_anchor": "主角色/吉祥物的固定外观（造型/配色/标志性配饰），60-100 字，只讲角色本身",
  "style_anchor": "全片统一视觉风格（画风/主色调/构图/反复出现的物件意象），40-80 字，不含角色"
}
要求：
1. 两段严格分离：character_anchor 不含风格词，style_anchor 不含角色——因为角色只在部分镜头出场（分镜有 has_character 标记），风格则全片统一；
2. 与大纲 style.visual 一致并具体化；角色设计简单、几何化、适合扁平插画风复现；
3. 只输出 JSON。"""


def generate_character_sheet(outline: dict, shots: list[dict], project_id: str) -> dict:
    """大纲 + 分镜 → 双段锚点（经网关计费）。空段即抛错。
    返回 {"character_anchor": ..., "style_anchor": ...}。"""
    user = (f"视频大纲：\n{json.dumps(outline, ensure_ascii=False)}\n\n"
            f"分镜概要：\n" + "\n".join(
                f"镜头{s['idx']}（{s['purpose']}，{'角色出场' if s.get('has_character') else '无角色'}）：{s['narration'][:20]}"
                for s in shots))
    r = gw.call("llm", {"system": CHARACTER_SYSTEM, "user": user},
                project_id=project_id)
    data = r["data"]
    char = (data.get("character_anchor") or "").strip()
    style = (data.get("style_anchor") or "").strip()
    if not char or not style:
        raise ValueError(f"锚点段落缺失: char={bool(char)} style={bool(style)}")
    return {"character_anchor": char, "style_anchor": style}
