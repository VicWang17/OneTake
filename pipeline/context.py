"""Context Builder（P1）：按阶段组装 LLM 上下文，全程记录来源与消耗。

定位：回答「这次模型调用的上下文由什么构成」——每个分节带来源（哪个文件/
哪张表）、版本（brief version / Skill version / 记忆条数）、Token 估算。
组成记录落盘 projects/{pid}/context/<stage>.json，供审计与回放（计划 §5）。

阶段：outline（策划）/ storyboard（分镜）/ shot_image（单镜生成）/ repair（修复）。
当前已接入：outline；其余阶段仍走各节点原有组装，后续迁移。

Token 估算：中文按约 2 字符 ≈ 1 token 粗估（不精确，用于预算与趋势对比）。
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

STAGES = ("outline", "storyboard", "shot_image", "repair")


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _section(name: str, source: str, version: str, text: str) -> dict:
    return {"name": name, "source": source, "version": version,
            "text": text, "tokens_est": _est_tokens(text)}


def _extern_section(pid: str, name: str, source: str, version: str, text: str) -> dict:
    """分节文本超限则外置为文件引用（P1），上下文只留摘要行与读取方式。"""
    from pipeline import refs
    r = refs.externalize(pid, f"ctx_{name}", text)
    if "inline" in r:
        return _section(name, source, version, text)
    sec = _section(name, source, version, refs.ref_line(r))
    sec["externalized"] = r["ref"]
    return sec


def build_outline(topic: str, pid: str, *, brief: dict | None = None,
                  skill: dict | None = None, memory_block: str = "",
                  feedback: str | None = None) -> dict:
    """策划阶段上下文。分节顺序即优先级声明：
    选题 → 用户硬约束（brief）→ Skill 方法论 → 打回意见（当前请求）→ 记忆（末位近因）。

    返回 ContextPack：{user, sections, tokens_est}；sections 供落盘审计。"""
    from pipeline import brief as brief_mod

    sections = [_section("topic", "user_input", "-", f"选题：{topic}")]

    brief_block = brief_mod.prompt_block(brief)
    if brief_block:
        sections.append(_extern_section(pid, "brief", f"projects/{pid}/brief.json",
                                        f"v{brief.get('version', 1)}", brief_block.strip()))

    if skill:
        d = skill["data"]
        skill_text = (f"创作方法论（Skill「{skill['name']}」，必须遵循）："
                      f"\n叙事结构：{' → '.join(d['structure'])}"
                      f"\n语言风格：{d['style']['tone']}"
                      f"\n画面风格：{d['style']['visual']}"
                      f"\n配音建议：{d['style']['voice']}")
        sections.append(_extern_section(pid, "skill", f"skills 注册表/{skill['name']}",
                                        str(d.get("version", "-")), skill_text))

    if feedback:
        sections.append(_extern_section(pid, "feedback", "user_input(打回意见)", "current",
                                        f"上次的大纲被退回，修改意见（请采纳并重新设计）：{feedback}"))

    if memory_block.strip():
        sections.append(_extern_section(pid, "memory", "memories 表 Top-K", "-",
                                        memory_block.strip()))

    user = "\n\n".join(s["text"] for s in sections)
    return {"user": user, "sections": sections,
            "tokens_est": _est_tokens(user)}


def save_pack(pid: str, stage: str, pack: dict, name: str | None = None) -> Path:
    """ContextPack 组成记录落盘（不含全文 text，防文件膨胀；text 由各节名可追溯）。
    name：同阶段多实例区分（如 shot_image_shot01 / repair_shot03）。"""
    assert stage in STAGES, f"未知阶段: {stage}"
    fname = f"{stage}{'_' + name if name else ''}.json"
    out = PROJECTS_DIR / pid / "context" / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "stage": stage, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tokens_est": pack["tokens_est"],
        "sections": [{k: s[k] for k in ("name", "source", "version", "tokens_est")
                      if k in s} | ({"externalized": s["externalized"]}
                                    if s.get("externalized") else {})
                     for s in pack["sections"]],
    }
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_storyboard(outline: dict, pid: str, *, brief: dict | None = None) -> dict:
    """分镜阶段上下文：brief 硬约束（时长→分镜时长分配、禁止→画面约束）+ 大纲全文。"""
    from pipeline import brief as brief_mod

    sections = []
    brief_block = brief_mod.prompt_block(brief)
    if brief_block:
        sections.append(_extern_section(pid, "brief", f"projects/{pid}/brief.json",
                                        f"v{brief.get('version', 1)}", brief_block.strip()))
    sections.append(_extern_section(
        pid, "outline", f"projects/{pid}/script.json", "-",
        f"视频大纲：\n{json.dumps(outline, ensure_ascii=False)}"))
    user = "\n\n".join(s["text"] for s in sections)
    return {"user": user, "sections": sections, "tokens_est": _est_tokens(user)}


def build_shot_image(script: dict, shot: dict, brief: dict | None,
                     prompt: str, pid: str) -> dict:
    """单镜生成阶段：出图 prompt 的组成审计（prompt 本身由 build_image_prompt 构造，
    此处记录它由哪些锚点/约束拼成，供追溯「这张图为什么长这样」）。"""
    from pipeline import brief as brief_mod

    sections = []
    if script.get("style_anchor"):
        sections.append(_section("style_anchor", f"projects/{pid}/script.json",
                                 "-", script["style_anchor"]))
    if shot.get("has_character") and script.get("character_anchor"):
        sections.append(_section("character_anchor", f"projects/{pid}/script.json",
                                 "-", script["character_anchor"]))
    sections.append(_section("visual_prompt", f"projects/{pid}/script.json",
                             f"shot{int(shot['idx']):02d}", shot["visual_prompt"]))
    forbidden = brief_mod.hard_constraints(brief)["forbidden"] if brief else []
    if forbidden:
        sections.append(_section("forbidden", f"projects/{pid}/brief.json",
                                 f"v{brief.get('version', 1)}",
                                 "画面严格避免：" + "、".join(forbidden)))
    return {"user": prompt, "sections": sections, "tokens_est": _est_tokens(prompt)}


def build_repair(pid: str, *, motion_prompt: str, issue: str,
                 brief: dict | None = None) -> dict:
    """修复阶段（质检重生成）：运动提示词 + 上轮失败原因 + brief 约束。"""
    from pipeline import brief as brief_mod

    sections = [_section("motion_prompt", f"projects/{pid}/script.json", "-",
                         motion_prompt),
                _section("issue", "VLM 评审", "current", f"上轮问题：{issue}")]
    brief_block = brief_mod.prompt_block(brief)
    if brief_block:
        sections.append(_extern_section(pid, "brief", f"projects/{pid}/brief.json",
                                        f"v{brief.get('version', 1)}", brief_block.strip()))
    user = "\n\n".join(s["text"] for s in sections)
    return {"user": user, "sections": sections, "tokens_est": _est_tokens(user)}
