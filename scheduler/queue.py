"""任务队列层（P4）：jobs 表状态机。所有状态迁移收敛在本模块。

状态机：pending → running → succeeded
                  running →（失败，retry_count+1）→ pending（run_at 退避）
                  running →（超过 max_retries）→ dead（死信，可人工 retry）
worker 崩溃恢复：启动时 recover_orphans 把"running 但 worker 已死"的任务回滚 pending。
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


def complete(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET status = 'succeeded', finished_at = ? WHERE id = ?",
                 (_now(), job_id))
    conn.commit()


def fail(conn: sqlite3.Connection, job_id: str, error: str) -> str:
    """失败处理：未超限 → pending + 退避；超限 → dead。返回新状态。"""
    row = conn.execute("SELECT retry_count, max_retries FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
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
    """worker 启动时调用：把不属于本进程的 running 任务回滚为 pending（崩溃回收）。"""
    cur = conn.execute(
        "UPDATE jobs SET status = 'pending', worker_id = NULL"
        " WHERE status = 'running' AND worker_id != ?", (live_worker_id,))
    conn.commit()
    return cur.rowcount


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
