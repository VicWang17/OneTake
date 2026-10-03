"""图版入口语义用例（P0 主入口统一）：n_storyboard 的 Skill 选择与续跑装载。

零 API 成本：monkeypatch sb.create_storyboard / selector.choose_skill，临时项目目录。
用法：uv run python evals/runners/graph_cases.py
产出：evals/reports/graph_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import graph as g  # noqa: E402
from pipeline import storyboard as sb  # noqa: E402
from skills import selector  # noqa: E402

_REAL_CREATE = sb.create_storyboard
_REAL_CHOOSE = selector.choose_skill
_SCRIPT = {"outline": {"title": "t"}, "shots": [], "character_sheet": ""}


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def run_cases() -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as tmp_s:
        g.PROJECTS_DIR = Path(tmp_s)  # graph 模块的项目根指向临时目录
        pid = "pgraph"
        (Path(tmp_s) / pid).mkdir()

        calls = {}

        def fake_create(topic=None, pid=None, feedback=None, skill_name=None,
                        brief=None):  # noqa: A002
            calls["create"] = {"topic": topic, "pid": pid, "skill_name": skill_name}
            (Path(tmp_s) / pid / "script.json").write_text(
                json.dumps(_SCRIPT, ensure_ascii=False), encoding="utf-8")
            return {"pid": pid, "shots": []}

        def fake_choose(topic, project_id=None):  # noqa: ARG001
            calls["choose"] = topic
            return {"skill": "知识科普解说", "reason": "测试"}
        sb.create_storyboard = fake_create
        selector.choose_skill = fake_choose
        try:
            # 1. 未指定 skill → 走选择器，选择结果传给 create_storyboard
            g.n_storyboard({"topic": "测试选题", "pid": pid})
            results.append({"case_id": "graph-skill-auto", "checks": [
                ("selector_called", calls.get("choose") == "测试选题"),
                ("skill_passed", calls["create"]["skill_name"] == "知识科普解说"),
            ]})

            # 2. 显式 skill → 不调用选择器，原样透传
            calls.clear()
            (Path(tmp_s) / pid / "script.json").unlink()
            g.n_storyboard({"topic": "测试选题", "pid": pid, "skill": "影视片段解说"})
            results.append({"case_id": "graph-skill-forced", "checks": [
                ("selector_not_called", "choose" not in calls),
                ("skill_passed", calls["create"]["skill_name"] == "影视片段解说"),
            ]})

            # 3. 续跑（script.json 已存在）→ 直接装载，不重建不选 Skill
            calls.clear()
            g.n_storyboard({"topic": "测试选题", "pid": pid})
            results.append({"case_id": "graph-resume-loads", "checks": [
                ("no_create", "create" not in calls),
                ("no_choose", "choose" not in calls),
            ]})
        finally:
            sb.create_storyboard = _REAL_CREATE
            selector.choose_skill = _REAL_CHOOSE
    return [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in results
    ]


def main() -> None:
    results = run_cases()
    sha = _git_sha()
    report = {
        "runner": "evals/runners/graph_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (monkeypatched storyboard/selector, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"graph_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
