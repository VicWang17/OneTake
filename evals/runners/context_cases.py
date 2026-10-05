"""Context Builder 用例（P1）：分节组成、来源/版本记录、Token 估算、落盘审计。

零 API 成本：纯组装逻辑 + 临时目录。
用法：uv run python evals/runners/context_cases.py
产出：evals/reports/context_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import brief as b  # noqa: E402
from pipeline import context as ctx  # noqa: E402

_SKILL = {"name": "知识科普解说", "data": {
    "version": "1.0.0",
    "structure": ["钩子提问", "概念拆解", "类比举例", "反转总结"],
    "style": {"tone": "t", "visual": "v", "voice": "v"}}}


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _make_brief() -> dict:
    br = b.new_brief()
    b.set_constraint(br, "duration", 45, "user_current")
    b.add_list_constraint(br, "forbidden", "出现人物", "user_current")
    return br


def run_cases() -> list[dict]:
    results = []

    # 1. 全量分节：选题→brief→skill→feedback→记忆，顺序即优先级
    pack = ctx.build_outline("测试选题", "pctx", brief=_make_brief(), skill=_SKILL,
                             memory_block="\n\n[记忆] 偏好：标题带数字",
                             feedback="重写开头")
    names = [s["name"] for s in pack["sections"]]
    results.append({"case_id": "ctx-section-order", "checks": [
        ("order_is_priority",
         names == ["topic", "brief", "skill", "feedback", "memory"]),
        ("all_sections_in_user",
         all(s["text"] in pack["user"] for s in pack["sections"])),
    ]})

    # 2. 来源与版本记录（可追溯性）
    by_name = {s["name"]: s for s in pack["sections"]}
    results.append({"case_id": "ctx-provenance", "checks": [
        ("brief_source_version",
         by_name["brief"]["source"].endswith("brief.json")
         and by_name["brief"]["version"].startswith("v")),
        ("skill_version", by_name["skill"]["version"] == "1.0.0"),
        ("feedback_marked_current", by_name["feedback"]["version"] == "current"),
        ("tokens_est_positive",
         pack["tokens_est"] > 0
         and all(s["tokens_est"] > 0 for s in pack["sections"])),
    ]})

    # 3. 最小输入：只有选题，无冗余空节
    pack_min = ctx.build_outline("测试选题", "pctx")
    results.append({"case_id": "ctx-minimal", "checks": [
        ("topic_only", [s["name"] for s in pack_min["sections"]] == ["topic"]),
    ]})

    # 4. 落盘审计：context/outline.json 含分节清单与估算，不含全文
    with tempfile.TemporaryDirectory() as tmp_s:
        old_root = ctx.PROJECTS_DIR
        ctx.PROJECTS_DIR = Path(tmp_s)
        try:
            out = ctx.save_pack("pctx", "outline", pack)
            record = json.loads(out.read_text(encoding="utf-8"))
        finally:
            ctx.PROJECTS_DIR = old_root
    results.append({"case_id": "ctx-save-pack", "checks": [
        ("record_has_sections", len(record["sections"]) == len(pack["sections"])),
        ("record_has_tokens", record["tokens_est"] == pack["tokens_est"]),
        ("no_full_text_on_disk", all("text" not in s for s in record["sections"])),
    ]})

    # 5. 分镜阶段：brief 约束 + 大纲双分节
    pack_sb = ctx.build_storyboard({"title": "t", "target_duration": 45}, "pctx",
                                   brief=_make_brief())
    results.append({"case_id": "ctx-storyboard", "checks": [
        ("brief_and_outline_sections",
         [s["name"] for s in pack_sb["sections"]] == ["brief", "outline"]),
        ("both_in_user",
         "45 秒" in pack_sb["user"] and "视频大纲" in pack_sb["user"]),
    ]})

    # 6. 单镜阶段：锚点/画面/禁止三分节审计
    script = {"style_anchor": "扁平插画", "character_anchor": "月牙"}
    shot = {"idx": 1, "visual_prompt": "微波炉特写", "has_character": True}
    pack_si = ctx.build_shot_image(script, shot, _make_brief(),
                                   "扁平插画，月牙，微波炉特写，画面严格避免：出现人物",
                                   "pctx")
    results.append({"case_id": "ctx-shot-image", "checks": [
        ("sections_complete",
         [s["name"] for s in pack_si["sections"]]
         == ["style_anchor", "character_anchor", "visual_prompt", "forbidden"]),
        ("no_character_drops_anchor",
         [s["name"] for s in ctx.build_shot_image(
             script, {**shot, "has_character": False}, None, "p", "pctx")["sections"]]
         == ["style_anchor", "visual_prompt"]),
    ]})

    # 7. 修复阶段：运动提示词 + 上轮问题分节
    pack_rp = ctx.build_repair("pctx", motion_prompt="镜头推近", issue="画面崩坏",
                               brief=_make_brief())
    results.append({"case_id": "ctx-repair", "checks": [
        ("issue_section", any(s["name"] == "issue" and "画面崩坏" in s["text"]
                              for s in pack_rp["sections"])),
        ("brief_section", any(s["name"] == "brief" for s in pack_rp["sections"])),
    ]})

    # 8. 超长分节外置：>2000 字符的记忆块 → 引用摘要 + 落盘文件
    with tempfile.TemporaryDirectory() as tmp_s:
        old_root = ctx.PROJECTS_DIR
        ctx.PROJECTS_DIR = Path(tmp_s)
        try:
            huge = "\n\n[记忆] " + "很长的记忆内容" * 300
            pack_big = ctx.build_outline("测试选题", "pctx", memory_block=huge)
            mem_sec = next(s for s in pack_big["sections"] if s["name"] == "memory")
            ref_file = Path(mem_sec.get("externalized", ""))
            checks = [
                ("section_externalized", bool(mem_sec.get("externalized"))),
                ("summary_line_compact",
                 len(mem_sec["text"]) < 300 and "外置" in mem_sec["text"]
                 and "读取方式" in mem_sec["text"]),
                ("file_complete", ref_file.exists()
                 and len(ref_file.read_text(encoding="utf-8")) > 2000),
            ]
        finally:
            ctx.PROJECTS_DIR = old_root
    results.append({"case_id": "ctx-externalize", "checks": checks})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/context_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (pure assembly, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"context_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
