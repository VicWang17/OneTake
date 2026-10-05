"""Plan 确定性用例（P2）：计划构建范围、静态校验拒绝、依赖分层、执行器门槛。

覆盖 le 家族核心场景（le-01 单镜画面 / le-02 旁白 / le-03 时长 / le-06 组合），
零 API 成本：handler 用替身函数（写假产物文件），验证器走真实文件检查。
用法：uv run python evals/runners/plan_cases.py
产出：evals/reports/plan_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from editing import ffmpeg  # noqa: E402
from pipeline import plan as pl  # noqa: E402

N_SHOTS = 6

# 替身产物不是真实媒体：ffprobe 验证器打桩为恒通过（时长 1s），
# 文件存在性检查仍走真实 stat。真实 ffprobe 行为已由 artifact/judge 用例覆盖。
ffmpeg.probe_duration = lambda p: 1.0


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _mk_project(pdir: Path) -> None:
    """已完成项目的最小快照：script.json + 全部镜头产物（图/音/视频）。"""
    pdir.mkdir(parents=True, exist_ok=True)
    shots = [{"idx": i, "duration": 10, "purpose": "铺垫",
              "narration": f"第{i}镜旁白"} for i in range(1, N_SHOTS + 1)]
    shots[-1]["purpose"] = "结尾总结"
    (pdir / "script.json").write_text(json.dumps(
        {"outline": {"title": "t", "target_duration": 60, "style": {}},
         "shots": shots}, ensure_ascii=False), encoding="utf-8")
    for i in range(1, N_SHOTS + 1):
        for rel in (pl._img(i), pl._aud(i), pl._clip(i)):
            f = pdir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"fake")


def _fake_handlers(record: list, fail_caps: set | None = None,
                   fail_once: set | None = None) -> dict:
    """替身 handler：为节点 outputs 写假产物。fail_caps 始终失败；
    fail_once 首次抛异常、重试成功。"""
    fail_caps, fail_once = fail_caps or set(), fail_once or set()
    tried: dict[str, int] = {}

    def mk(cap):
        def h(pid, node):
            tried[node.id] = tried.get(node.id, 0) + 1
            record.append(node.id)
            if cap in fail_caps:
                raise RuntimeError("boom")
            if cap in fail_once and tried[node.id] == 1:
                raise RuntimeError("transient")
            for o in node.outputs:
                f = HANDLER_CTX["project_dir"] / o
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"new")
        return h

    return {cap: mk(cap) for cap in pl.CAPABILITIES}


HANDLER_CTX: dict = {}  # handler 需要知道临时项目目录（execute_plan 只传 pid）


def run_cases() -> list[dict]:
    results = []

    # 1. 新建计划结构：分层正确，参考首图的镜头等待 shot_image_01
    plan = pl.plan_create("px", N_SHOTS, budget_cny=20, ref_first_shots=[2, 4])
    levels = pl.topo_levels(plan)
    level_of = {nid: lv for lv, ids in enumerate(levels) for nid in ids}
    results.append({"case_id": "plan-create-structure", "checks": [
        ("storyboard_first", level_of["storyboard"] == 0),
        ("ref_first_waits",
         level_of["shot_image_02"] > level_of["shot_image_01"]
         and level_of["shot_image_04"] > level_of["shot_image_01"]),
        ("plain_shot_parallel_with_first",
         level_of["shot_image_03"] == level_of["shot_image_01"]),
        ("video_waits_image_and_align",
         level_of["shot_video_03"] > level_of["shot_image_03"]
         and level_of["shot_video_03"] > level_of["align"]),
        ("render_last", level_of["render"] == len(levels) - 1),
    ]})

    # 2. le-01 单镜画面修改：范围只含 shot 3 链路 + 共享下游，其余产物受保护
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p1"
        _mk_project(pdir)
        plan = pl.plan_shot_edit("p1", 3, N_SHOTS, budget_cny=4)
        caps = sorted(n.capability for n in plan.nodes)
        issues = pl.validate_plan(plan, pdir)
        results.append({"case_id": "le-01-shot-edit-scope", "checks": [
            ("scope_minimal", caps == sorted(
                ["shot_image", "shot_video", "align", "edl", "render"])),
            ("only_shot3_nodes",
             all("03" in n.id or n.capability in ("align", "edl", "render")
                 for n in plan.nodes)),
            ("others_protected",
             pl._img(2) in plan.protected and pl._clip(5) in plan.protected
             and pl._img(3) not in plan.protected),
            ("validate_passes", issues == []),
        ]})

    # 3. le-02 旁白修改：只重做 TTS 链；本镜画面产物也受保护（时长传导到 EDL）
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p2"
        _mk_project(pdir)
        plan = pl.plan_narration_edit("p2", 5, N_SHOTS, budget_cny=3,
                                      new_text="转盘的作用就是让食物轮流经过热点。")
        caps = sorted(n.capability for n in plan.nodes)
        results.append({"case_id": "le-02-narration-edit-scope", "checks": [
            ("scope_audio_only",
             caps == sorted(["tts", "align", "edl", "render"])),
            ("own_visual_protected",
             pl._img(5) in plan.protected and pl._clip(5) in plan.protected),
            ("validate_passes", pl.validate_plan(plan, pdir) == []),
        ]})

    # 4. le-03 时长压缩：执行前产出删镜/复用决策，保留镜头零付费节点
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p3"
        _mk_project(pdir)
        plan = pl.plan_duration_adjust("p3", 45, budget_cny=5, project_dir=pdir)
        results.append({"case_id": "le-03-duration-adjust", "checks": [
            ("plan_before_execution", bool(plan.meta.get("dropped_shots"))),
            ("drops_low_priority_and_tail",
             plan.meta["dropped_shots"] == [N_SHOTS - 1, N_SHOTS]),
            ("est_within_target", plan.meta["est_duration"] <= 45),
            ("no_paid_nodes",
             all(pl.CAPABILITIES[n.capability]["cost_est"] == 0
                 for n in plan.nodes)),
            ("validate_passes", pl.validate_plan(plan, pdir) == []),
        ]})

    # 5. 非法计划执行前被拒：未知能力 / 循环依赖 / 缺失输入 / 超预算 / 受保护产物
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p4"
        _mk_project(pdir)
        bad_cap = pl.plan_shot_edit("p4", 3, N_SHOTS, 99)
        bad_cap.nodes[0].capability = "teleport"
        bad_dep = pl.plan_shot_edit("p4", 3, N_SHOTS, 99)
        bad_dep.node("edl").depends_on = ["render"]  # render 已依赖 edl → 环
        bad_inp = pl.plan_shot_edit("p4", 3, N_SHOTS, 99)
        bad_inp.nodes[0].inputs["ghost"] = "ghost/missing.png"
        bad_budget = pl.plan_shot_edit("p4", 3, N_SHOTS, budget_cny=0.5)
        bad_prot = pl.plan_narration_edit("p4", 5, N_SHOTS, 99)
        bad_prot.nodes[0].outputs.append(pl._img(5))  # 试图覆盖受保护产物
        for cid, p, expect in [
            ("reject-unknown-capability", bad_cap, "未知能力"),
            ("reject-circular-dependency", bad_dep, "循环依赖"),
            ("reject-missing-input", bad_inp, "输入缺失"),
            ("reject-over-budget", bad_budget, "预算超限"),
            ("reject-protected-artifact", bad_prot, "受保护产物"),
        ]:
            issues = pl.validate_plan(p, pdir)
            results.append({"case_id": cid, "checks": [
                ("rejected_with_reason",
                 any(expect in i for i in issues)),
            ]})

    # 6. 执行器门槛：产物持久化 + 验证通过才释放下游；失败下游整支跳过
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p5"
        _mk_project(pdir)
        HANDLER_CTX["project_dir"] = pdir
        ran: list = []
        plan = pl.plan_shot_edit("p5", 3, N_SHOTS, budget_cny=4)
        handlers = _fake_handlers(ran, fail_caps={"shot_video"})
        rep = pl.execute_plan(plan, handlers, pdir)
        results.append({"case_id": "executor-gates-downstream", "checks": [
            ("upstream_ran", "shot_image_03" in rep["succeeded"]),
            ("failed_node", rep["failed"] == ["shot_video_03"]),
            ("downstream_skipped",
             rep["skipped"] == ["align", "edl", "render"]
             and not any(n in ran for n in ("align", "edl", "render"))),
            ("state_persisted",
             pl.load_plan(pdir / "plans" / f"{plan.plan_id}.json").node(
                 "shot_video_03").status == "failed"),
        ]})

    # 7. 执行器重试：瞬时失败按 max_retries 重试后成功放行
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p6"
        _mk_project(pdir)
        HANDLER_CTX["project_dir"] = pdir
        ran = []
        plan = pl.plan_shot_edit("p6", 3, N_SHOTS, budget_cny=4)
        rep = pl.execute_plan(plan, _fake_handlers(ran, fail_once={"shot_video"}),
                              pdir)
        results.append({"case_id": "executor-retry-then-release", "checks": [
            ("all_succeeded", rep["failed"] == [] and rep["skipped"] == []),
            ("retried", ran.count("shot_video_03") == 2),
            ("order_respects_deps",
             ran.index("shot_image_03") < ran.index("shot_video_03")
             < ran.index("align") < ran.index("edl") < ran.index("render")),
        ]})

    # 8. 计划被拒时不调用任何 handler（工具执行前拦截）
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p7"
        _mk_project(pdir)
        HANDLER_CTX["project_dir"] = pdir
        ran = []
        plan = pl.plan_shot_edit("p7", 3, N_SHOTS, budget_cny=0.1)  # 超预算
        rep = pl.execute_plan(plan, _fake_handlers(ran), pdir)
        results.append({"case_id": "rejected-plan-runs-nothing", "checks": [
            ("rejected", rep["rejected"] is True),
            ("no_handler_called", ran == []),
        ]})

    # 9. 自然语言意图解析（le-01/02/03/05/06 输入形态）
    r1 = pl.parse_edit_request("第 3 镜画面改成炉内食物转盘的特写")
    r2 = pl.parse_edit_request("第 5 镜旁白改成：转盘的作用就是让食物轮流经过热点。")
    r3 = pl.parse_edit_request("整条视频压缩到 45 秒以内")
    r6 = pl.parse_edit_request("第 2 镜旁白缩短一半，第 4 镜画面换成更亮的场景")
    results.append({"case_id": "parse-edit-requests", "checks": [
        ("shot_edit", r1["intents"] == [
            {"kind": "shot_edit", "shot": 3, "feedback": "第 3 镜画面改成炉内食物转盘的特写"}]),
        ("narration_edit", r2["intents"][0]["kind"] == "narration_edit"
         and r2["intents"][0]["shot"] == 5),
        ("duration_adjust", r3["intents"] == [
            {"kind": "duration_adjust", "target_s": 45.0}]),
        ("combo_union", sorted((i["kind"], i["shot"]) for i in r6["intents"])
         == [("narration_edit", 2), ("shot_edit", 4)]),
        ("unparseable_returns_none", pl.parse_edit_request("今天天气不错") is None),
    ]})

    # 10. le-06 组合修改：两类意图的计划节点取并集，范围正确
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p8"
        _mk_project(pdir)
        pa = pl.plan_narration_edit("p8", 2, N_SHOTS, 5)
        pb = pl.plan_shot_edit("p8", 4, N_SHOTS, 5)
        merged_caps = {(n.capability) for n in pa.nodes} | {n.capability for n in pb.nodes}
        results.append({"case_id": "le-06-combo-union", "checks": [
            ("union_scope", merged_caps ==
             {"tts", "shot_image", "shot_video", "align", "edl", "render"}),
            ("shot4_visual_not_protected_in_b", pl._img(4) not in pb.protected),
            ("shot2_visual_protected_in_a", pl._img(2) in pa.protected),
        ]})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/plan_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (fake handlers + real file checks, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"plan_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    n_fail = sum(1 for r in results if r["verdict"] == "fail")
    print(f"\n报告: {out.relative_to(ROOT)}  ·  {len(results)-n_fail}/{len(results)} 通过")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
