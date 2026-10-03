"""调度器层确定性用例运行器：fr-05（崩溃孤儿回收）、fr-08（重复恢复幂等 + 过期 Attempt）。

零 API 成本：独立临时 SQLite + 直接驱动 scheduler/queue 状态机，不起 worker、不调厂商。
用法：uv run python evals/runners/scheduler_cases.py
产出：evals/reports/scheduler_baseline_<date>.json
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
from scheduler import queue  # noqa: E402


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _job(conn, job_id: str):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def run_fr05(conn) -> dict:
    """fr-05：worker 崩溃后孤儿任务被回收重跑，无丢失、状态一致。"""
    checks = []
    jid = queue.enqueue(conn, "sleep", {"seconds": 0})

    # worker A 领到任务后"崩溃"（不再出现，任务卡 running）
    job = queue.claim(conn, "w-dead")
    checks.append(("claimed_by_dead_worker", job is not None and job["id"] == jid))
    checks.append(("stuck_running", _job(conn, jid)["status"] == "running"))

    # worker B 启动：recover_orphans 回收不属于自己（A 已死）的 running 任务
    n = queue.recover_orphans(conn, "w-live")
    checks.append(("orphans_recovered", n == 1))
    row = _job(conn, jid)
    checks.append(("rolled_back_pending",
                   row["status"] == "pending" and row["worker_id"] is None))

    # worker B 重领并完成
    job = queue.claim(conn, "w-live")
    checks.append(("reclaimed", job is not None and job["id"] == jid))
    queue.complete(conn, jid)
    checks.append(("succeeded", _job(conn, jid)["status"] == "succeeded"))
    checks.append(("no_running_left", queue.stats(conn).get("running", 0) == 0))

    return {"case_id": "fr-05", "checks": checks,
            "verdict": "pass" if all(ok for _, ok in checks) else "fail"}


def run_fr08(conn) -> dict:
    """fr-08：重复恢复幂等；过期 Attempt（旧 worker 的迟到回写）不得覆盖新状态。"""
    checks = []
    jid = queue.enqueue(conn, "sleep", {"seconds": 0})
    queue.claim(conn, "w-dead")  # 旧 worker 领到后"崩溃"

    # 第一部分：同一 job 恢复两次，第二次必须是空操作
    n1 = queue.recover_orphans(conn, "w-live")
    n2 = queue.recover_orphans(conn, "w-live")
    checks.append(("first_recovery_rollback", n1 == 1))
    checks.append(("double_recovery_idempotent", n2 == 0))

    # 第二部分：过期 Attempt 覆盖测试
    # 新 worker 领走任务并失败一次（→ pending 退避中），此时旧 worker 的"迟到 complete"到达
    queue.claim(conn, "w-live")
    queue.fail(conn, jid, "transient error")
    stale_overwrote = None
    row_before = _job(conn, jid)
    if row_before["status"] == "pending":
        queue.complete(conn, jid)  # 模拟旧 Attempt 迟到的成功回写（complete 无状态/归属守卫）
        stale_overwrote = _job(conn, jid)["status"] == "succeeded"
    checks.append(("stale_attempt_not_applied", stale_overwrote is False))

    return {"case_id": "fr-08", "checks": checks,
            "verdict": "pass" if all(ok for _, ok in checks) else "fail",
            "note": "stale_attempt_not_applied 失败 = 当前 complete() 无状态守卫的已知缺口"
                    if stale_overwrote else None}


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = dao.get_conn(Path(tmp) / "test.db")
        for case_fn in (run_fr05, run_fr08):
            conn.execute("DELETE FROM jobs")
            conn.commit()
            results.append(case_fn(conn))
        conn.close()

    report = {
        "runner": "evals/runners/scheduler_cases.py",
        "git_sha": _git_sha(),
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (temp sqlite, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"scheduler_baseline_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
        if r.get("note"):
            print(f"    注: {r['note']}")
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
