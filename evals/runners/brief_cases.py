"""CreativeBrief 确定性用例（P1）：优先级裁决、冲突留痕、持久化往返。

零 API 成本：纯数据结构 + 临时目录读写。
用法：uv run python evals/runners/brief_cases.py
产出：evals/reports/brief_<sha8>_<date>.json
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


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def run_cases() -> list[dict]:
    results = []

    # 1. 当前请求覆盖历史偏好（cc-05/qb-04 场景），且冲突留痕
    br = b.new_brief()
    b.set_constraint(br, "style", "标题带数字的悬念风", "memory")
    b.set_constraint(br, "style", "平实不带数字", "user_current", note="用户本轮要求")
    results.append({"case_id": "brief-override-memory", "checks": [
        ("current_request_wins",
         br["style"]["value"] == "平实不带数字" and br["style"]["source"] == "user_current"),
        ("conflict_logged",
         any(c.get("action") == "overridden" and c["slot"] == "style"
             for c in br["changelog"])),
    ]})

    # 2. 低优先级无法覆盖高优先级（memory 偏好改不动已确认项目约束），拒绝也留痕
    br = b.new_brief()
    b.set_constraint(br, "duration", 45, "project_confirmed")
    b.set_constraint(br, "duration", 60, "memory")
    results.append({"case_id": "brief-priority-holds", "checks": [
        ("project_constraint_survives", br["duration"]["value"] == 45),
        ("rejection_logged",
         any(c.get("action") == "rejected_lower_priority" for c in br["changelog"])),
    ]})

    # 3. 同级来源后者覆盖前者（用户改主意）
    br = b.new_brief()
    b.set_constraint(br, "duration", 45, "user_current")
    b.set_constraint(br, "duration", 50, "user_current")
    results.append({"case_id": "brief-same-level-override", "checks": [
        ("latest_wins", br["duration"]["value"] == 50),
    ]})

    # 4. 禁止/必需清单 + 去重 + 硬约束视图
    br = b.new_brief()
    b.add_list_constraint(br, "forbidden", "出现人物", "user_current")
    b.add_list_constraint(br, "forbidden", "出现人物", "memory")  # 去重升级来源
    b.add_list_constraint(br, "required", "结尾给行动建议", "project_confirmed")
    hard = b.hard_constraints(br)
    results.append({"case_id": "brief-list-constraints", "checks": [
        ("dedup_single_entry", len(br["forbidden"]) == 1),
        ("forbidden_in_view", "出现人物" in hard["forbidden"]),
        ("required_in_view", "结尾给行动建议" in hard["required"]),
    ]})

    # 5. 持久化往返（brief.json 落盘再读回一致）
    with tempfile.TemporaryDirectory() as tmp:
        old_root = b.PROJECTS_DIR
        b.PROJECTS_DIR = Path(tmp)
        try:
            b.set_constraint(br, "duration", 45, "project_confirmed")
            b.save_brief("pbrief", br)
            loaded = b.load_brief("pbrief")
        finally:
            b.PROJECTS_DIR = old_root
    results.append({"case_id": "brief-persistence", "checks": [
        ("roundtrip_equal", loaded == br),
        ("changelog_preserved", len(loaded["changelog"]) == len(br["changelog"])),
    ]})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/brief_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (pure data structures, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"brief_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
