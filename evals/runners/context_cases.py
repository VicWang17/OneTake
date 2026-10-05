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
