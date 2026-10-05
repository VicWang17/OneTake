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
        sections.append(_section("brief", f"projects/{pid}/brief.json",
                                 f"v{brief.get('version', 1)}", brief_block.strip()))

    if skill:
        d = skill["data"]
        skill_text = (f"创作方法论（Skill「{skill['name']}」，必须遵循）："
                      f"\n叙事结构：{' → '.join(d['structure'])}"
                      f"\n语言风格：{d['style']['tone']}"
                      f"\n画面风格：{d['style']['visual']}"
                      f"\n配音建议：{d['style']['voice']}")
        sections.append(_section("skill", f"skills 注册表/{skill['name']}",
                                 str(d.get("version", "-")), skill_text))

    if feedback:
        sections.append(_section("feedback", "user_input(打回意见)", "current",
                                 f"上次的大纲被退回，修改意见（请采纳并重新设计）：{feedback}"))

    if memory_block.strip():
        sections.append(_section("memory", "memories 表 Top-K", "-",
                                 memory_block.strip()))

    user = "\n\n".join(s["text"] for s in sections)
    return {"user": user, "sections": sections,
            "tokens_est": _est_tokens(user)}


def save_pack(pid: str, stage: str, pack: dict) -> Path:
    """ContextPack 组成记录落盘（不含全文 text，防文件膨胀；text 由各节名可追溯）。"""
    assert stage in STAGES, f"未知阶段: {stage}"
    out = PROJECTS_DIR / pid / "context" / f"{stage}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "stage": stage, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tokens_est": pack["tokens_est"],
        "sections": [{k: s[k] for k in ("name", "source", "version", "tokens_est")}
                     for s in pack["sections"]],
    }
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
