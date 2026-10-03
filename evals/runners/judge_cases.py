"""质检重生成版本切换用例（P0）：新版本验证通过后切换引用，失败保留旧版与证据。

零 API 成本：monkeypatch gw.call（假视频生成）与 judge.judge_shot（可控评分），
临时目录 + 临时 SQLite + 本地 ffmpeg 占位 mp4。
用法：uv run python evals/runners/judge_cases.py
产出：evals/reports/judge_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from db import dao  # noqa: E402
from datapipe import events  # noqa: E402
from editing.ffmpeg import FFMPEG  # noqa: E402
from gateway import core as gw  # noqa: E402
from nodes import judge as nj  # noqa: E402
from pipeline import judge as pj  # noqa: E402

PID = "pjudge"

# 保存原始引用：monkeypatch 全局模块后跨用例必须用原始函数/恢复现场
_REAL_GET_CONN = dao.get_conn
_REAL_JUDGE_SHOT = nj.judge_shot
_REAL_GW_CALL = gw.call
_REAL_EMIT = events.emit


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _make_mp4(path: Path, color: str = "gray") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=854x480:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, check=True)


def _setup(tmp: Path, judge_results: list[bool]):
    """搭临时项目：1 镜头 script.json + 原始 clip；注入假 VLM/假视频生成/假事件。"""
    pdir = tmp / PID
    (pdir / "clips").mkdir(parents=True)
    (pdir / "script.json").write_text(json.dumps(
        {"shots": [{"idx": 1, "visual_prompt": "测试画面", "narration": "测试台词"}]},
        ensure_ascii=False), encoding="utf-8")
    original = pdir / "clips" / "shot_01_src.mp4"
    _make_mp4(original, "gray")

    results_iter = iter(judge_results)

    def fake_judge(video, v, n, pid, prev_issue=None):  # noqa: ARG001
        passed = next(results_iter)
        return {"semantic": 5 if passed else 1, "quality": 4,
                "issues": "" if passed else "画面与意图不符", "passed": passed}
    nj.judge_shot = fake_judge

    def fake_call(task_type, payload, tier="draft", project_id=None):  # noqa: ARG001
        assert task_type == "video"
        _make_mp4(Path(payload["out_path"]), "blue")
        return {"file_path": payload["out_path"], "cost": 0.71, "model": "fake"}
    gw.call = fake_call
    events.emit = lambda *a, **kw: None

    conn = _REAL_GET_CONN(tmp / "test.db")
    dao.get_conn = lambda *a, **kw: conn  # judge_project 内部取连接指向临时库
    return pdir, original


def run_case(name: str, judge_results: list[bool]) -> dict:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        old_project_root = pj.PROJECTS_DIR
        pj.PROJECTS_DIR = tmp
        try:
            pdir, original = _setup(tmp, judge_results)
            original_bytes = original.read_bytes()
            result = pj.judge_project(PID)
            report = json.loads((pdir / "final" / "quality.json").read_text(encoding="utf-8"))
        finally:  # 恢复全部 monkeypatch，防泄漏到下一个用例
            pj.PROJECTS_DIR = old_project_root
            nj.judge_shot = _REAL_JUDGE_SHOT
            gw.call = _REAL_GW_CALL
            events.emit = _REAL_EMIT
            dao.get_conn = _REAL_GET_CONN

        checks = []
        if name == "qb-01-pass-on-retry":
            checks.append(("switched_to_new_version",
                           original.read_bytes() != original_bytes))
            checks.append(("switched_flag_recorded",
                           report[0].get("switched") is True))
            checks.append(("candidate_moved_not_copied",
                           not (pdir / "clips" / "shot_01_src.v2.mp4").exists()))
        else:  # qb-01-always-fail
            checks.append(("old_version_retained",
                           original.read_bytes() == original_bytes))
            checks.append(("failure_evidence_kept",
                           (pdir / "clips" / "shot_01_src.v2.mp4").exists()
                           and (pdir / "clips" / "shot_01_src.v3.mp4").exists()))
            checks.append(("escalated_to_human",
                           report[0].get("final") == "人工介入"
                           and report[0].get("kept_old_version") is True))
        checks.append(("quality_json_written", bool(report)))
        return {"case_id": name, "checks": checks, "result_summary": result,
                "verdict": "pass" if all(ok for _, ok in checks) else "fail"}


def main() -> None:
    results = [
        run_case("qb-01-pass-on-retry", [False, True]),    # 首评失败 → 重生成后通过
        run_case("qb-01-always-fail", [False, False, False]),  # 三连败 → 人工介入
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/judge_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (monkeypatched vl/video, temp sqlite, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"judge_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
