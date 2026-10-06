"""Execution 确定性用例（P3）：台账持久化、错误分类处置、失效传播、
过期 Attempt 防护、崩溃恢复、交付检查版本归属、预算熔断、无进展检测。

收官用例 acceptance-scenario 完整走阶段验收：多轮修改 + 一次质量失败 +
一次进程中断之后仍按最新约束完成，且能解释哪些步骤复用、哪些重做及原因。

零 API 成本：handler 全部替身，ffprobe 打桩。
用法：uv run python evals/runners/execution_cases.py
产出：evals/reports/execution_<sha8>_<date>.json
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
from pipeline import execution as ex  # noqa: E402
from pipeline import plan as pl  # noqa: E402

ffmpeg.probe_duration = lambda p: 1.0  # 替身产物非真实媒体，ffprobe 打桩恒过

N_SHOTS = 4
CTX: dict = {}  # handler 上下文：project_dir / 故障注入集合 / 崩溃钩子


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _write_edl(pdir: Path) -> None:
    (pdir / "final").mkdir(exist_ok=True)
    (pdir / "final" / "edl.json").write_text(json.dumps({
        "duration": 40, "tracks": {"video": [
            {"idx": i, "kind": "video", "src": f"clips/shot_{i:02d}_src.mp4",
             "src_dur": 10, "timeline_dur": 10, "speed": 1.0}
            for i in range(1, N_SHOTS + 1)]}}, ensure_ascii=False),
        encoding="utf-8")


def _mk_project(pdir: Path) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    shots = [{"idx": i, "duration": 10, "purpose": "铺垫",
              "narration": f"第{i}镜旁白"} for i in range(1, N_SHOTS + 1)]
    (pdir / "script.json").write_text(json.dumps(
        {"outline": {"title": "t", "target_duration": 40, "style": {}},
         "shots": shots}, ensure_ascii=False), encoding="utf-8")
    _write_edl(pdir)


def _handlers(fail: dict | None = None, crash_on: dict | None = None,
              mid_run_hook: dict | None = None) -> tuple[dict, list]:
    """fail: {node_id: evidence} 永远失败；crash_on: {node_id: None} 首次调用抛
    KeyboardInterrupt 模拟进程被杀；mid_run_hook: {node_id: fn} handler 内触发。"""
    fail, crash_on = fail or {}, crash_on or {}
    mid_run_hook, ran = mid_run_hook or {}, []
    crashed: set = set()

    def mk(cap):
        def h(pid, node):
            if node.id in crash_on and node.id not in crashed:
                crashed.add(node.id)
                raise KeyboardInterrupt("模拟进程被杀")
            if node.id in fail:
                raise RuntimeError(fail[node.id])
            if node.id in mid_run_hook:
                mid_run_hook[node.id]()
            for o in node.outputs:
                f = CTX["pdir"] / o
                f.parent.mkdir(parents=True, exist_ok=True)
                # .json 产物（script/edl）保留 fixture 合法内容，模拟「就地更新」
                if o.endswith(".json") and f.exists():
                    continue
                f.write_bytes(f"{node.id}@{time.time_ns()}".encode())
            ran.append(node.id)
        return h

    return {cap: mk(cap) for cap in pl.CAPABILITIES}, ran


def _task_plan(pdir, kind="shot_edit", shot=3, budget=20):
    if kind == "create":
        plan = pl.plan_create("px", N_SHOTS, budget)
    elif kind == "shot_edit":
        plan = pl.plan_shot_edit("px", shot, N_SHOTS, budget)
    else:
        plan = pl.plan_narration_edit("px", shot, N_SHOTS, budget)
    task = ex.submit_task(pdir, plan.goal, plan)
    return task, plan


def run_cases() -> list[dict]:
    results = []

    # 1. 台账持久化：Task + Attempt 落盘可重载，预算随成功节点累计
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p1"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        handlers, _ = _handlers()
        ex.execute_task(pdir, task, plan, handlers)
        t2 = ex.load_task(pdir, task["task_id"])
        atts = ex.load_attempts(pdir, task["task_id"])
        results.append({"case_id": "task-ledger-persistence", "checks": [
            ("task_fields", t2["plan_version"] == 1 and t2["status"] == "succeeded"
             and t2["budget_cny"] == 20),
            ("spent_tracked", t2["spent_cny"] > 0),
            ("attempts_logged", len(atts) == len(plan.nodes)
             and all(a["ok"] for a in atts)),
            ("attempt_has_version_and_rationale",
             all(a["plan_version"] == 1 and a["rationale"] for a in atts)),
        ]})

    # 2. 错误分类：transient 重试 / param 修复 / unknown 耗尽升级
    results.append({"case_id": "error-classification", "checks": [
        ("transient", ex.classify_error("handler 异常: NoAudioReceived") == "transient"),
        ("budget", ex.classify_error("预算超限") == "budget"),
        ("param", ex.classify_error("时长越界 ±20%") == "param"),
        ("artifact", ex.classify_error("缺失/空产物: ['clips/x.mp4']") == "artifact"),
        ("unknown", ex.classify_error("weird") == "unknown"),
        ("strategy_map", ex.STRATEGY["transient"] == "retry"
         and ex.STRATEGY["param"] == "repair" and ex.STRATEGY["unknown"] == "retry"
         and ex.STRATEGY["budget"] == "stop"),
    ]})

    # 3. 参数修复路径：param 错误 → repair_hook 修复 → 重试成功
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p3"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir, kind="narration", shot=3)  # tts_03 在这条链上
        repaired = []
        handlers, ran = _handlers(fail={"tts_03": "时长越界"})
        def repair(node, evidence):
            repaired.append(node.id)
            handlers["tts"] = _handlers()[0]["tts"]  # 修复后换成正常 handler
            return True
        rep = ex.execute_task(pdir, task, plan, handlers, repair_hook=repair)
        results.append({"case_id": "param-repair-then-success", "checks": [
            ("repair_called", repaired == ["tts_03"]),
            ("node_recovered", "tts_03" in rep["redone"]
             and not rep["failed"]),
            ("repair_logged", any(a["strategy"] == "repair"
                                  for a in ex.load_attempts(pdir, task["task_id"]))),
        ]})

    # 4. 无进展检测：同一错误重复 → 提前升级，不烧满重试次数
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p4"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        handlers, ran = _handlers(fail={"shot_image_03": "weird error xyz"})
        rep = ex.execute_task(pdir, task, plan, handlers)
        t4 = ex.load_task(pdir, task["task_id"])
        n_attempts = sum(1 for a in ex.load_attempts(pdir, task["task_id"])
                         if a["node_id"] == "shot_image_03")
        results.append({"case_id": "no-progress-escalation", "checks": [
            ("escalated_early",
             n_attempts == ex.NO_PROGRESS_LIMIT
             and rep["escalated"] == ["shot_image_03"]),
            ("downstream_skipped",
             rep["skipped"] == ["shot_video_03", "align", "edl", "render"]),
            ("open_question_recorded",
             t4["open_questions"][0]["node"] == "shot_image_03"
             and "tried" in t4["open_questions"][0]),
        ]})

    # 5. 预算熔断：开工前预估超预算即停，剩余节点 budget_stopped
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p5"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        # shot_edit 链: image(0.10) video(1.50) align(0.05) edl(0) render(0)
        task, plan = _task_plan(pdir, budget=5)  # 计划级校验要能通过
        task["budget_cny"] = 0.5  # 任务预算在执行中收紧（模拟预算策略调整）
        handlers, ran = _handlers()
        rep = ex.execute_task(pdir, task, plan, handlers)
        t5 = ex.load_task(pdir, task["task_id"])
        results.append({"case_id": "budget-circuit-breaker", "checks": [
            ("stops_at_budget", rep["budget_stopped"] == ["shot_video_03"]),
            ("spent_within_budget", t5["spent_cny"] <= 0.5),
            ("no_video_handler_call", "shot_video_03" not in ran),
            ("status", t5["status"] == "budget_stopped"),
        ]})

    # 6. 目标变更 → 镜头级失效传播 + 只重做必要节点
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p6"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir, kind="create", budget=99)
        handlers, _ = _handlers()
        ex.execute_task(pdir, task, plan, handlers)
        # 用户改主意：第 2 镜画面要改 → 失效应只覆盖 shot2 图/视频 + final
        ex.apply_goal_change(pdir, task, "改第 2 镜画面", ["shots/shot_02.png"])
        m = ex.load_manifest(pdir)
        stale = sorted(r for r, a in m.items() if a.get("stale"))
        task, plan2 = _reload_task_new_plan(pdir, task["task_id"],
                                            pl.plan_shot_edit("px", 2, N_SHOTS, 5))
        handlers2, ran2 = _handlers()
        rep = ex.execute_task(pdir, task, plan2, handlers2)
        results.append({"case_id": "goal-change-invalidation", "checks": [
            ("shot2_chain_stale",
             stale == sorted(["shots/shot_02.png", "clips/shot_02_src.mp4",
                              "final/draft.mp4", "final/edl.json"])
             if "final/edl.json" in m else False),
            ("others_untouched", not m["shots/shot_01.png"]["stale"]
             and not m["clips/shot_04_src.mp4"]["stale"]),
            ("version_bumped", task["plan_version"] == 2),
            # 旁白未变 → align 复用（缓存零成本语义）；edl/render 因 final 失效重做
            ("only_needed_redone",
             sorted(rep["redone"]) ==
             ["edl", "render", "shot_image_02", "shot_video_02"]),
            ("align_reused", "align" in rep["reused"]),
        ]})

    # 7. 过期 Attempt 防护：handler 运行期间目标变更（版本 +1）→ 结果丢弃不盖戳
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p7"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        holder = {"task": task}
        def bump():
            ex.apply_goal_change(pdir, holder["task"], "新目标", ["shots/shot_03.png"])
        handlers, _ = _handlers(mid_run_hook={"shot_image_03": bump})
        rep = ex.execute_task(pdir, task, plan, handlers)
        m = ex.load_manifest(pdir)
        art = m.get("shots/shot_03.png", {})
        results.append({"case_id": "stale-attempt-guard", "checks": [
            ("result_discarded", "shot_image_03" not in rep["redone"]),
            ("not_stamped_new_version",
             art.get("plan_version") != 2 or art.get("stale") is True),
            ("discard_logged", any("stale-attempt" in a["rationale"]
                                   for a in ex.load_attempts(pdir, task["task_id"]))),
        ]})

    # 8. 崩溃恢复：render 首次被杀 → 重跑执行器，上游全部复用，只补 render
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p8"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        handlers, ran = _handlers(crash_on={"render": None})
        try:
            ex.execute_task(pdir, task, plan, handlers)
        except KeyboardInterrupt:
            pass
        handlers2, ran2 = _handlers()
        rep = ex.execute_task(pdir, ex.load_task(pdir, task["task_id"]), plan,
                              handlers2)
        results.append({"case_id": "crash-resume-reuses-upstream", "checks": [
            ("crashed_before_render", "render" not in ran),
            ("upstream_reused", sorted(rep["reused"]) == sorted(
                ["shot_image_03", "shot_video_03", "align", "edl"])),
            ("only_render_redone", rep["redone"] == ["render"]),
            ("final_success", ex.load_task(pdir, task["task_id"])["status"]
             == "succeeded"),
        ]})

    # 9. 确认版本归属：确认 v1 → 重生成 → 确认自动失效 → 再确认清零
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p9"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        ex.execute_task(pdir, task, plan, _handlers()[0])
        ex.confirm(pdir, "clips/shot_03_src.mp4")
        ok_after_confirm = "clips/shot_03_src.mp4" not in ex.pending_confirmations(pdir)
        ex.stamp_artifacts(pdir, 1, ["clips/shot_03_src.mp4"])  # 重生成
        stale_after_regen = "clips/shot_03_src.mp4" in ex.pending_confirmations(pdir)
        ex.confirm(pdir, "clips/shot_03_src.mp4")
        results.append({"case_id": "confirmation-version-binding", "checks": [
            ("confirm_clears_pending", ok_after_confirm),
            ("regen_invalidates_confirmation", stale_after_regen),
            ("reconfirm_clears", "clips/shot_03_src.mp4"
             not in ex.pending_confirmations(pdir)),
        ]})

    # 10. 交付检查：分镜/FFprobe/EDL/VLM 四道闸 + EDL 不一致能被拦
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p10"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir, kind="create", budget=99)
        ex.execute_task(pdir, task, plan, _handlers()[0])
        _write_edl(pdir)  # 替身 handler 覆盖了 edl.json，重写为合法内容
        for rel in ex.load_manifest(pdir):
            ex.confirm(pdir, rel)
        d = ex.deliverable(pdir, vl_fn=lambda p: {"ok": True, "evidence": "VLM 通过"})
        # 破坏 EDL（镜头集合与分镜不一致）→ 不可交付
        edl = json.loads((pdir / "final" / "edl.json").read_text(encoding="utf-8"))
        edl["tracks"]["video"] = edl["tracks"]["video"][:-1]
        (pdir / "final" / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
        checks = ex.run_delivery_checks(pdir)
        edl_check = next(c for c in checks if c["check"] == "edl")
        results.append({"case_id": "delivery-gates", "checks": [
            ("deliverable_when_clean", d["deliverable"] is True),
            ("four_checks_present",
             sorted(c["check"] for c in d["checks"]) ==
             ["edl", "ffprobe", "storyboard", "vlm"]),
            ("edl_mismatch_caught", edl_check["ok"] is False),
            ("check_has_version",
             next(c for c in d["checks"] if c["check"] == "storyboard")["version"]
             is not None),
        ]})

    # 11. 恢复复核：盘上文件被篡改 + 待确认 + 未结任务的开放问题，全部列出
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p11"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        task, plan = _task_plan(pdir)
        ex.execute_task(pdir, task, plan, _handlers()[0])  # 干净跑一轮，产物盖戳
        m = ex.load_manifest(pdir)
        victim = next(iter(m))
        (pdir / victim).write_bytes(b"tampered")  # 篡改
        # 再来一轮失败任务制造未结开放问题：选旁白链（audio 未被上轮盖戳，
        # 不会触发复用短路）
        task2, plan2 = _task_plan(pdir, kind="narration", shot=3)
        ex.execute_task(pdir, task2, plan2,
                        _handlers(fail={"tts_03": "weird"})[0])
        review = ex.review_on_resume(pdir)
        results.append({"case_id": "resume-reviews-artifacts", "checks": [
            ("tamper_detected", victim in review["changed"]),
            ("pending_listed", len(review["pending_confirmations"]) > 0),
            ("open_questions_listed",
             any(q["task_id"] == task2["task_id"]
                 for q in review["open_questions"])),
        ]})

    # 12. 阶段验收场景：多轮修改 + 质量失败 + 进程中断 → 按最新约束完成 + 可解释
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "p12"
        _mk_project(pdir)
        CTX["pdir"] = pdir
        # 第 1 轮：新建（全链跑通）
        task, plan = _task_plan(pdir, kind="create", budget=99)
        ex.execute_task(pdir, task, plan, _handlers()[0])
        # 第 2 轮：改第 3 镜画面，途中一次质量失败（transient 重试成功）
        ex.apply_goal_change(pdir, task, "改第 3 镜画面", ["shots/shot_03.png"])
        plan2 = pl.plan_shot_edit("px", 3, N_SHOTS, 10)
        flaky, _ = _handlers()
        orig_video = flaky["shot_video"]
        state = {"n": 0}
        def flaky_video(pid, node):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("502 Bad Gateway")  # 质量/服务失败，transient
            orig_video(pid, node)
        flaky["shot_video"] = flaky_video
        rep2 = ex.execute_task(pdir, task, plan2, flaky)
        # 第 3 轮：再改第 2 镜旁白，执行中进程被杀一次，恢复后完成
        ex.apply_goal_change(pdir, task, "改第 2 镜旁白", ["audio/shot_02.mp3"])
        plan3 = pl.plan_narration_edit("px", 2, N_SHOTS, 5)
        crashing, ran3a = _handlers(crash_on={"edl": None})
        try:
            ex.execute_task(pdir, task, plan3, crashing)
        except KeyboardInterrupt:
            pass
        rep3 = ex.execute_task(pdir, task, plan3, _handlers()[0])
        # 终态：确认全部产物后可交付；解释包含复用与重做理由
        _write_edl(pdir)  # 替身 handler 覆盖了 edl.json，重写为合法内容
        for rel in ex.load_manifest(pdir):
            if not ex.load_manifest(pdir)[rel].get("stale"):
                ex.confirm(pdir, rel)
        d = ex.deliverable(pdir, vl_fn=lambda p: {"ok": True, "evidence": "ok"})
        explain = ex.explain_task(pdir, task["task_id"], report=rep3)
        results.append({"case_id": "acceptance-scenario", "checks": [
            ("round2_recovered_from_quality_failure",
             rep2["failed"] == [] and state["n"] == 2),
            ("round3_resumed_after_crash", rep3["redone"] == ["edl", "render"]
             or rep3["redone"] == ["render"]),
            ("round3_reused_unaffected",
             "tts_02" in rep3["reused"] or "tts_02" in rep3["redone"]),
            ("final_deliverable", d["deliverable"] is True),
            ("explainable", any("复用" in x for x in explain)
             and any("一次通过" in x or "尝试后成功" in x for x in explain)),
        ]})

    return results


def _reload_task_new_plan(pdir, task_id, new_plan):
    """目标变更后用新计划继续同一任务（计划版本已在 apply_goal_change 时 +1）。"""
    task = ex.load_task(pdir, task_id)
    task["plan_id"] = new_plan.plan_id
    (pdir / "tasks" / task_id / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return task, new_plan


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/execution_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (fake handlers + injected failures, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"execution_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
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
