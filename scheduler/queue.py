"""任务队列层（P4）：jobs 表状态机。所有状态迁移收敛在本模块。

状态机：pending → running → succeeded
                  running →（失败，retry_count+1）→ pending（run_at 退避）
                  running →（超过 max_retries）→ dead（死信，可人工 retry）
worker 崩溃恢复：启动时 recover_orphans 回收"running 但 worker 已死"的任务——
已提交供应商（payload 有 task_id）→ unknown（结果未知），由 reconcile_unknown
对账后回 pending，handler 凭 task_id 先查供应商状态再决定续查/重提（不重复扣费）；
未提交 → 直接回滚 pending。
已知限制：worker 崩溃发生在「供应商已受理但 task_id 回调未落库」的窗口内时，
任务无 task_id 会被当作未提交重跑，存在重复提交风险——该窗口无法在本层消除。
"""

import json
import sqlite3
import time
import uuid

BACKOFF_SECONDS = [5, 10, 20]  # 第 n 次失败后的退避


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def enqueue(conn: sqlite3.Connection, type_: str, payload: dict, *,
            priority: int = 100, max_retries: int = 3,
            idem_key: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO jobs (id, type, payload_json, priority, status, max_retries,"
        " run_at, idem_key, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (job_id, type_, json.dumps(payload, ensure_ascii=False), priority,
         max_retries, _now(), idem_key, _now()))
    conn.commit()
    return job_id


def claim(conn: sqlite3.Connection, worker_id: str) -> sqlite3.Row | None:
    """领取一个任务：到期 pending 中优先级最高（数值最小）、最早创建的。"""
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'pending' AND run_at <= ?"
        " ORDER BY priority, created_at LIMIT 1", (_now(),)).fetchone()
    if not row:
        return None
    cur = conn.execute(
        "UPDATE jobs SET status = 'running', worker_id = ?"
        " WHERE id = ? AND status = 'pending'",  # 条件更新防并发双领
        (worker_id, row["id"]))
    conn.commit()
    return row if cur.rowcount else None


def complete(conn: sqlite3.Connection, job_id: str, worker_id: str) -> bool:
    """完成回写：仅当任务仍 running 且归属本 worker 才生效（防过期 Attempt 覆盖新状态）。"""
    cur = conn.execute(
        "UPDATE jobs SET status = 'succeeded', finished_at = ?"
        " WHERE id = ? AND status = 'running' AND worker_id = ?",
        (_now(), job_id, worker_id))
    conn.commit()
    return cur.rowcount > 0


def fail(conn: sqlite3.Connection, job_id: str, error: str, worker_id: str) -> str | None:
    """失败处理：未超限 → pending + 退避；超限 → dead。返回新状态。
    仅当任务仍 running 且归属本 worker 才生效（否则返回 None，过期回写丢弃）。"""
    row = conn.execute(
        "SELECT retry_count, max_retries FROM jobs"
        " WHERE id = ? AND status = 'running' AND worker_id = ?",
        (job_id, worker_id)).fetchone()
    if not row:
        return None
    rc = row["retry_count"] + 1
    if rc >= row["max_retries"]:
        conn.execute("UPDATE jobs SET status = 'dead', retry_count = ?,"
                     " finished_at = ? WHERE id = ?", (rc, _now(), job_id))
        new_status = "dead"
    else:
        delay = BACKOFF_SECONDS[min(rc - 1, len(BACKOFF_SECONDS) - 1)]
        run_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + delay))
        conn.execute("UPDATE jobs SET status = 'pending', retry_count = ?,"
                     " run_at = ?, worker_id = NULL WHERE id = ?",
                     (rc, run_at, job_id))
        new_status = "pending"
    conn.commit()
    return new_status


def update_payload(conn: sqlite3.Connection, job_id: str, patch: dict) -> None:
    """合并更新任务负载（如视频任务提交后回写 vendor task_id，供崩溃续查）。"""
    row = conn.execute("SELECT payload_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    payload = json.loads(row["payload_json"])
    payload.update(patch)
    conn.execute("UPDATE jobs SET payload_json = ? WHERE id = ?",
                 (json.dumps(payload, ensure_ascii=False), job_id))
    conn.commit()


def recover_orphans(conn: sqlite3.Connection, live_worker_id: str) -> int:
    """worker 启动时调用：回收不属于本进程的 running 任务。

    已提交供应商（payload 有 task_id）→ unknown（结果未知，待对账）；
    未提交 → 回滚 pending 直接重跑。返回回收任务数。
    """
    rows = conn.execute(
        "SELECT id, payload_json FROM jobs WHERE status = 'running' AND worker_id != ?",
        (live_worker_id,)).fetchall()
    for r in rows:
        task_id = json.loads(r["payload_json"]).get("task_id")
        new_status = "unknown" if task_id else "pending"
        conn.execute("UPDATE jobs SET status = ?, worker_id = NULL WHERE id = ?",
                     (new_status, r["id"]))
    conn.commit()
    return len(rows)


def reconcile_unknown(conn: sqlite3.Connection) -> list[str]:
    """对账（P0）：unknown 任务凭 task_id 回 pending——handler 恢复时先查供应商
    状态再决定续查下载还是重新提交（resume_task_id 路径，不重复扣费）。
    返回对账的任务 id 列表。"""
    rows = conn.execute("SELECT id FROM jobs WHERE status = 'unknown'").fetchall()
    for r in rows:
        conn.execute("UPDATE jobs SET status = 'pending' WHERE id = ?", (r["id"],))
    conn.commit()
    return [r["id"] for r in rows]


def list_jobs(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at",
                            (status,)).fetchall()
    return conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def retry_job(conn: sqlite3.Connection, job_id: str) -> bool:
    """死信重放：dead → pending（retry_count 清零）。"""
    cur = conn.execute(
        "UPDATE jobs SET status = 'pending', retry_count = 0, run_at = ?,"
        " finished_at = NULL WHERE id = ? AND status = 'dead'", (_now(), job_id))
    conn.commit()
    return cur.rowcount > 0
