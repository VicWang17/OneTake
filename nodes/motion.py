"""运动提示词节点（P2）：分镜（画面 + 运镜 + 台词）→ 视频模型的运动指令。

设计：visual_prompt 只描述"画什么"，视频模型还需要"怎么动"。本节点把
运镜字段（推/拉/摇/固定）翻译为具体镜头运动，并补充主体动作与节奏，
与首帧（分镜图）一起构成图生视频的完整输入。
"""

import json

from gateway import core as gw

MOTION_SYSTEM = """你是视频导演。用户给你一个分镜的画面描述、运镜要求和台词，你输出一句运动提示词（motion prompt），指导视频模型"让画面动起来"。

要求：
1. 输出严格 JSON：{"motion_prompt": "……"}（40-80 字中文）；
2. 必须包含三要素：①镜头运动（把运镜要求具体化，如"推"→"镜头缓慢推近主体"）；②主体动作（画面中的角色/物体怎么动，简单、物理合理，1-2 个动作）；③节奏（舒缓/轻快/紧张）；
3. 动作要少而稳：视频只有 5 秒，复杂动作会崩坏；
4. 不要改变画面内容，只描述运动；
5. 只输出 JSON。"""

CAMERA_HINTS = {
    "推": "镜头缓慢推近主体（zoom in）",
    "拉": "镜头缓慢拉远展现全景（zoom out）",
    "摇": "镜头缓慢横摇/环绕展示环境（pan）",
    "固定": "镜头固定不动，只有画面内的主体在动",
}


def generate_motion_prompt(shot: dict, project_id: str) -> str:
    """单个分镜 → 运动指令。空结果即抛错。"""
    camera = shot.get("camera", "固定")
    user = (
        f"画面描述：{shot['visual_prompt']}\n"
        f"运镜要求：{camera}（{CAMERA_HINTS.get(camera, CAMERA_HINTS['固定'])}）\n"
        f"台词参考：{shot.get('narration', '')}"
    )
    r = gw.call("llm", {"system": MOTION_SYSTEM, "user": user},
                project_id=project_id)
    motion = (r["data"].get("motion_prompt") or "").strip()
    if not motion:
        raise ValueError("运动提示词为空")
    return motion
