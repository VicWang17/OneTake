"""角色设定表节点（P1）：生成项目级角色/视觉锚点描述（文本一致性手段）。

设计：分镜图逐张独立生成，模型无记忆，角色会"换脸"。本节点把角色的固定外观
（造型/配色/标志性元素）钉成一段文本常量，1.4 出图时拼进每个 visual_prompt。
零成本基础一致性手段；参考图链（图像锚点）在 1.4 叠加。
"""

import json

from gateway import core as gw

CHARACTER_SYSTEM = """你是角色设计师。根据视频大纲和分镜，设计一个贯穿全片的视觉锚点，输出严格 JSON：
{
  "character_sheet": "一段 60-120 字的中文描述，包含：①主角色/吉祥物（如有）的固定外观——造型、配色、标志性配饰；②全片统一的视觉元素——画风、主色调、构图偏好"
}
要求：
1. 必须与大纲 style.visual 一致，把它具体化为可复用的固定描述；
2. 角色设计要简单、几何化、适合扁平插画风复现（避免复杂发型/写实五官）；
3. 若题材无人物（纯物件/风景科普），则设计一个"向导吉祥物"并统一视觉元素；
4. 只输出 JSON。"""


def generate_character_sheet(outline: dict, shots: list[dict], project_id: str) -> str:
    """大纲 + 分镜 → character_sheet 文本（经网关计费）。空描述即抛错。"""
    user = (f"视频大纲：\n{json.dumps(outline, ensure_ascii=False)}\n\n"
            f"分镜概要：\n" + "\n".join(
                f"镜头{s['idx']}（{s['purpose']}）：{s['narration'][:20]}" for s in shots))
    r = gw.call("llm", {"system": CHARACTER_SYSTEM, "user": user},
                project_id=project_id)
    sheet = (r["data"].get("character_sheet") or "").strip()
    if not sheet:
        raise ValueError("角色设定表为空")
    return sheet
