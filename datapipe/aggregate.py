"""每日聚合（P5 数据链路）：generations 流水 → model_perf_daily 特征表。

作为调度器的 aggregate 任务类型运行（调度器不只服务视频生成——系统任务也走队列）。
幂等：同日同模型同档位覆盖写（INSERT OR REPLACE），可重复执行。
"""

import time
from collections import defaultdict

from db import dao


def aggregate_daily(date: str | None = None) -> dict:
    """聚合指定日（默认今天）的模型表现。返回聚合行数。"""
    date = date or time.strftime("%Y-%m-%d")
    conn = dao.get_conn()
    rows = conn.execute(
        "SELECT model, tier, status, cost, latency_ms FROM generations"
        " WHERE date(created_at) = ? AND status != 'cache_hit'", (date,)).fetchall()

    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["tier"])].append(r)

    n = 0
    for (model, tier), rs in groups.items():
        calls = len(rs)
        succ = sum(1 for r in rs if r["status"] == "succeeded")
        lats = [r["latency_ms"] for r in rs if r["latency_ms"]]
        conn.execute(
            "INSERT OR REPLACE INTO model_perf_daily"
            " (date, model, tier, calls, success_rate, avg_latency, total_cost)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, model, tier, calls,
             round(succ / calls, 4) if calls else 0,
             round(sum(lats) / len(lats), 1) if lats else None,
             round(sum(float(r["cost"]) for r in rs), 4)))
        n += 1
    conn.commit()
    conn.close()
    return {"date": date, "groups": n}


def backfill_all() -> list[dict]:
    """对 generations 里出现过的所有日期补做聚合（首次上线用）。"""
    conn = dao.get_conn()
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date(created_at) FROM generations ORDER BY 1").fetchall()]
    conn.close()
    return [aggregate_daily(d) for d in dates]


def handle_aggregate(conn, job_id: str, payload: dict) -> None:
    """调度器任务处理器：aggregate 类型。"""
    aggregate_daily(payload.get("date"))
