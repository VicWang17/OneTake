"""分镜表生成节点（P1）：大纲 → 分镜表 JSON，四层校验 + 错误回灌自修复。

容错设计（面试高频题"LLM 输出不稳定怎么办"的标准解法）：
1. 结构化输出 + markdown 围栏零成本预处理；
2. 四层校验：JSON 语法 → schema 结构 → 业务约束 → 跨字段一致性；
3. 校验器产出带定位的错误清单（位置/现状/期望），多轮回灌让 LLM 自修；
4. ≤3 次仍不合法则失败退出（每次回灌都走网关计费，上限即预算护栏）。
"""

import json
import re

from gateway import core as gw

SHOTS_SYSTEM = """你是分镜师。根据用户给的视频大纲（含 style），输出 8-10 个分镜的严格 JSON：
{"shots": [{
  "idx": 1,                # 镜头号，从 1 开始连续递增
  "duration": 5,           # 秒，整数，4-8
  "narration": "……",      # 台词，15-40 字，口语化，符合 style.tone
  "visual_prompt": "……",  # 画面描述，必须统一体现 style.visual
  "camera": "推",           # 运镜，只能是：推 / 拉 / 摇 / 固定
  "purpose": "钩子"         # 叙事功能，只能是：钩子 / 铺垫 / 高潮 / 收尾
}]}
要求：
1. 全部 narration 按顺序拼接就是一篇完整解说稿，覆盖 structure 各段；
2. 全部 duration 之和与 target_duration 的偏差 ≤20%；
3. 只输出 JSON，不要任何其他文字。"""

CAMERAS = {"推", "拉", "摇", "固定"}
PURPOSES = {"钩子", "铺垫", "高潮", "收尾"}
MAX_RETRIES = 3

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fence(text: str) -> str:
    """剥离 markdown 代码围栏——最常见'错误'，零成本修复，不消耗重试。"""
    return _FENCE_RE.sub("", text.strip())


def validate(text: str, target_duration: int) -> tuple[dict | None, list[str]]:
    """四层校验。返回 (解析结果或 None, 错误清单)。错误带 位置/现状/期望。"""
    # ① 语法层
    try:
        data = json.loads(_strip_fence(text))
    except json.JSONDecodeError as e:
        return None, [f"JSON 语法错误：{e.msg}（第 {e.lineno} 行），请输出合法 JSON"]

    # ② 结构层
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        return None, ["顶层必须是 {\"shots\": [...]} 结构"]
    shots = data["shots"]
    if not 8 <= len(shots) <= 10:
        return None, [f"镜头数 = {len(shots)}，应为 8-10 个"]

    # ③ 业务约束层（逐镜头）
    errors: list[str] = []
    for i, s in enumerate(shots):
        loc = f"shots[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{loc} 不是对象")
            continue
        if s.get("idx") != i + 1:
            errors.append(f"{loc}.idx = {s.get('idx')!r}，应为 {i + 1}（从 1 连续递增）")
        dur = s.get("duration")
        if not isinstance(dur, int) or isinstance(dur, bool):
            errors.append(f"{loc}.duration = {dur!r}，应为整数（秒）")
        elif not 4 <= dur <= 8:
            errors.append(f"{loc}.duration = {dur}，超出 4-8 秒范围")
        nar = s.get("narration")
        if not isinstance(nar, str) or not 10 <= len(nar) <= 50:
            errors.append(f"{loc}.narration 长度 = {len(nar) if isinstance(nar, str) else type(nar).__name__}，应为 10-50 字")
        if not isinstance(s.get("visual_prompt"), str) or not s["visual_prompt"].strip():
            errors.append(f"{loc}.visual_prompt 缺失或为空")
        if s.get("camera") not in CAMERAS:
            errors.append(f"{loc}.camera = {s.get('camera')!r}，必须是 推/拉/摇/固定 之一")
        if s.get("purpose") not in PURPOSES:
            errors.append(f"{loc}.purpose = {s.get('purpose')!r}，必须是 钩子/铺垫/高潮/收尾 之一")
    if errors:
        return None, errors

    # ④ 一致性层
    total = sum(s["duration"] for s in shots)
    if target_duration and abs(total - target_duration) / target_duration > 0.2:
        errors.append(f"全部镜头时长合计 {total}s，与目标 {target_duration}s 偏差超过 20%")
    return (data, errors) if not errors else (None, errors)


def generate_storyboard(outline: dict, project_id: str) -> list[dict]:
    """大纲 → 分镜列表。校验失败时把错误清单回灌给 LLM 自修（≤3 次）。"""
    messages = [
        {"role": "system", "content": SHOTS_SYSTEM},
        {"role": "user", "content": f"视频大纲：\n{json.dumps(outline, ensure_ascii=False)}"},
    ]
    target = int(outline.get("target_duration") or 60)
    last_errors: list[str] = []
    for attempt in range(1, MAX_RETRIES + 1):
        r = gw.call("llm", {"messages": messages}, project_id=project_id)
        text = r["text"]
        data, errors = validate(text, target)
        if not errors:
            return data["shots"]
        last_errors = errors
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": (
            f"你的输出有 {len(errors)} 处错误：\n"
            + "\n".join(f"{i}. {e}" for i, e in enumerate(errors, 1))
            + "\n请修正后重新输出完整 JSON（全量，不要只输出修改的部分）。")})
    raise RuntimeError(f"分镜表 {MAX_RETRIES} 次修正仍不合法，转人工。最后错误：{last_errors}")


REWRITE_SYSTEM = """你是解说词编辑。用户会给你一句台词、它的真实朗读时长、目标时长范围。
改写台词让朗读时长落入目标范围：过长则压缩（保留核心信息，删掉修饰），过短则自然扩写（补充细节，不得注水）。
保持口语化与原有语气。输出严格 JSON：{"narration": "改写后的台词"}。只输出 JSON。"""


def rewrite_narration(narration: str, actual: float, lo: float, hi: float,
                      project_id: str) -> str:
    """台词时长越界 → LLM 改写（给具体的时长目标，同错误回灌思想）。"""
    user = (f"台词：{narration}\n真实朗读时长：{actual:.1f} 秒\n"
            f"目标范围：{lo:.1f} - {hi:.1f} 秒")
    r = gw.call("llm", {"system": REWRITE_SYSTEM, "user": user},
                project_id=project_id)
    new = (r["data"].get("narration") or "").strip()
    if not new:
        raise ValueError("改写结果为空")
    return new
