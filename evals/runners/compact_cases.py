"""摘要压缩用例（P1）：交互日志、结构化压缩、硬约束免疫、增量压缩、恢复上下文。

零 API 成本：临时目录 + monkeypatch gw.call（LLM 压缩）。
用法：uv run python evals/runners/compact_cases.py
产出：evals/reports/compact_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gateway import core as gw  # noqa: E402
from pipeline import brief as b  # noqa: E402
from pipeline import compact as c  # noqa: E402

_REAL_GW_CALL = gw.call
_LLM_SUMMARY = {"facts": ["用户要求换第 2 镜类比"], "open_questions": [],
                "decisions": ["第 3 镜画面重画：用户认为构图不符"],
                "artifact_refs": ["projects/pcmp/shots/shot_03.png"]}


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _fake_llm(task_type, payload, tier="draft", project_id=None):  # noqa: ARG001
    return {"data": dict(_LLM_SUMMARY), "cost": 0.0, "model": "fake"}


def _setup(tmp: Path, pid: str = "pcmp") -> None:
    c.PROJECTS_DIR = b.PROJECTS_DIR = tmp
    br = b.new_brief()
    b.set_constraint(br, "duration", 50, "user_current")
    b.add_list_constraint(br, "forbidden", "出现人物", "user_current")
    b.save_brief(pid, br)
    # 三轮反馈
    c.log_interaction(pid, "user_request", "总时长 50 秒以内；禁止出现人物")
    c.log_interaction(pid, "feedback", "第 2 镜换一个类比")
    c.log_interaction(pid, "confirm_images", '{"redo": {"3": "构图不符"}}')


def run_cases() -> list[dict]:
    results = []

    # 1. cc-06 核心：压缩后硬约束可准确恢复
    with tempfile.TemporaryDirectory() as tmp_s:
        _setup(Path(tmp_s))
        gw.call = _fake_llm
        try:
            summary = c.compact("pcmp")
        finally:
            gw.call = _REAL_GW_CALL
        facts = summary["facts"]
        results.append({"case_id": "compact-constraints-survive", "checks": [
            ("duration_survives", any("50 秒" in f for f in facts)),
            ("forbidden_survives", any("出现人物" in f for f in facts)),
            ("schema_complete",
             all(k in summary for k in
                 ("facts", "open_questions", "decisions", "artifact_refs"))),
            ("llm_facts_merged", "用户要求换第 2 镜类比" in facts),
        ]})

    # 2. 增量压缩：已覆盖条目不重复压缩；新交互进入恢复上下文
    with tempfile.TemporaryDirectory() as tmp_s:
        _setup(Path(tmp_s))
        gw.call = _fake_llm
        try:
            s1 = c.compact("pcmp")
            c.log_interaction("pcmp", "feedback", "BGM 换轻快一点的")
            s2 = c.compact("pcmp")  # 只压缩第 4 条
            rc = c.recovery_context("pcmp")
        finally:
            gw.call = _REAL_GW_CALL
        results.append({"case_id": "compact-incremental", "checks": [
            ("covered_count_grows",
             s1["covered_entries"] == 3 and s2["covered_entries"] == 4),
            ("new_interactions_visible",
             len(rc["new_interactions"]) == 0  # 第 4 条也被 s2 覆盖了
             and rc["summary"]["covered_entries"] == 4),
            ("no_pending_returns_none", c.needs_compaction("pcmp") is False),
        ]})

    # 3. 恢复上下文：硬约束 + 摘要 + 新交互三段齐备
    with tempfile.TemporaryDirectory() as tmp_s:
        _setup(Path(tmp_s))
        gw.call = _fake_llm
        try:
            c.compact("pcmp")
            c.log_interaction("pcmp", "user_request", "标题改得有悬念一点")
            rc = c.recovery_context("pcmp")
        finally:
            gw.call = _REAL_GW_CALL
        results.append({"case_id": "compact-recovery-context", "checks": [
            ("hard_constraints_present",
             rc["hard_constraints"]["duration"] == 50
             and "出现人物" in rc["hard_constraints"]["forbidden"]),
            ("summary_present", rc["summary"] is not None),
            ("new_turn_visible",
             any("悬念" in e["content"] for e in rc["new_interactions"])),
        ]})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/compact_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (monkeypatched llm, temp dirs, no api)",
        "results": [
            {**r, "checks": [{"check": c2, "ok": ok} for c2, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"compact_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c2 for c2, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
