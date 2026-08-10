"""质检编排（P7）：对项目全部镜头跑 VLM 质检，不合格自动重生成（≤2 次）。

产出：final/quality.json（每镜头双维度分数 + 一次通过率）+ eval 事件进数据链路。
"""

import json
from pathlib import Path

from db import dao
from datapipe import events
from gateway import core as gw
from nodes import judge

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
MAX_REGEN = 2


def judge_project(pid: str) -> dict:
    pdir = PROJECTS_DIR / pid
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    conn = dao.get_conn()
    report = []

    for s in script["shots"]:
        idx = int(s["idx"])
        video = pdir / "clips" / f"shot_{idx:02d}_src.mp4"
        if not video.exists():
            continue
        attempts, issue = 0, None
        while True:
            r = judge.judge_shot(video, s["visual_prompt"], s.get("narration", ""),
                                 pid, prev_issue=issue)
            r["idx"], r["attempts"] = idx, attempts + 1
            if r["passed"]:
                break
            attempts += 1
            issue = r["issues"] or "评分不达标"
            if attempts > MAX_REGEN:
                r["final"] = "人工介入"
                break
            # 重生成：把失败原因写进运动提示词（删旧片 → 重新生成 → 复评）
            print(f"    shot {idx:02d} 不合格（语义 {r['semantic']} 质量 {r['quality']}："
                  f"{issue[:40]}），第 {attempts} 次重生成…")
            video.unlink()
            gw.call("video", {
                "prompt": s.get("motion_prompt", s["visual_prompt"])
                          + f"。避免以下问题：{issue}",
                "out_path": str(video),
                "model": "doubao-seedance-2-0-fast-260128",
                "seconds": 5, "resolution": "480p",
                "first_frame_url": None,  # 重生成走文生视频（避免首帧锚定延续错误构图）
            }, project_id=pid)
            r["regen"] = attempts
        report.append(r)
        dao.update_shot(conn, f"{pid}-s{idx:02d}",
                        status="judged" if r["passed"] else "judge_failed")
        events.emit("eval", ref_id=f"{pid}-s{idx:02d}", **r)

    out = pdir / "final" / "quality.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    dao.update_project(conn, pid, status="judged")
    conn.close()

    first_pass = sum(1 for r in report if r["passed"] and r["attempts"] == 1)
    passed = sum(1 for r in report if r["passed"])
    return {"pid": pid, "total": len(report), "passed": passed,
            "first_pass_rate": round(first_pass / len(report), 3) if report else 0,
            "report": str(out)}
