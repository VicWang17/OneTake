"""质检编排（P7）：对项目全部镜头跑 VLM 质检，不合格自动重生成（≤2 次）。

产出：final/quality.json（每镜头双维度分数 + 一次通过率）+ eval 事件进数据链路。
"""

import json
import os
from pathlib import Path

from db import dao
from datapipe import events
from gateway import core as gw
from nodes import judge
from pipeline import brief as brief_mod
from pipeline import context as context_mod

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
MAX_REGEN = 2


def _next_version_path(pdir: Path, idx: int) -> Path:
    """下一个候选版本路径：shot_XX_src.vN.mp4（N 从 2 递增，避开已存在的版本）。"""
    ver = 2
    while (pdir / "clips" / f"shot_{idx:02d}_src.v{ver}.mp4").exists():
        ver += 1
    return pdir / "clips" / f"shot_{idx:02d}_src.v{ver}.mp4"


def judge_project(pid: str) -> dict:
    pdir = PROJECTS_DIR / pid
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    conn = dao.get_conn()
    report = []

    for s in script["shots"]:
        idx = int(s["idx"])
        original = pdir / "clips" / f"shot_{idx:02d}_src.mp4"
        if not original.exists():
            continue
        current, attempts, issue = original, 0, None
        while True:
            r = judge.judge_shot(current, s["visual_prompt"], s.get("narration", ""),
                                 pid, prev_issue=issue)
            r["idx"], r["attempts"] = idx, attempts + 1
            if r["passed"]:
                if current != original:  # 新版本通过质检：原子切换引用
                    os.replace(current, original)
                    r["switched"] = True
                break
            attempts += 1
            issue = r["issues"] or "评分不达标"
            if attempts > MAX_REGEN:
                r["final"] = "人工介入"
                r["kept_old_version"] = True  # 旧版不动，失败候选留作证据
                break
            # 重生成（P0 版本切换语义）：先写新版本文件，通过质检后才替换引用；
            # 失败则旧版本与失败证据（vN 文件）都保留。失败原因写进运动提示词
            print(f"    shot {idx:02d} 不合格（语义 {r['semantic']} 质量 {r['quality']}："
                  f"{issue[:40]}），第 {attempts} 次重生成…")
            candidate = _next_version_path(pdir, idx)
            regen_prompt = (s.get("motion_prompt", s["visual_prompt"])
                            + f"。避免以下问题：{issue}")
            # 修复阶段 ContextPack：运动提示词 + 上轮失败原因 + brief 约束，落盘可审计
            context_mod.save_pack(
                pid, "repair",
                context_mod.build_repair(
                    pid, motion_prompt=s.get("motion_prompt", s["visual_prompt"]),
                    issue=issue, brief=brief_mod.load_brief(pid)),
                name=f"shot{idx:02d}")
            gw.call("video", {
                "prompt": regen_prompt,
                "out_path": str(candidate),
                "model": "doubao-seedance-2-0-fast-260128",
                "seconds": 5, "resolution": "480p",
                "first_frame_url": None,  # 重生成走文生视频（避免首帧锚定延续错误构图）
            }, project_id=pid)
            current = candidate
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
