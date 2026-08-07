"""stats 仪表盘（P5）：一屏看全系统状态。数据源：generations / jobs / 注册表。

告警阈值（CLI 标 ⚠️）：今日花费 >80% 预算 / 失败率 >20% / 有死信 / 队列积压 >5。
"""

from collections import defaultdict

from db import dao
from gateway.core import DAILY_BUDGET_LIMIT
from scheduler import queue


def collect() -> dict:
    conn = dao.get_conn()
    rows = conn.execute("SELECT * FROM generations").fetchall()
    real = [r for r in rows if r["status"] != "cache_hit"]
    hits = [r for r in rows if r["status"] == "cache_hit"]
    today = [r for r in real
             if r["created_at"].startswith(_today())]

    # 按模型聚合
    per_model: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "failed": 0, "cost": 0.0, "latencies": []})
    for r in real:
        m = per_model[r["model"]]
        m["calls"] += 1
        m["cost"] += float(r["cost"])
        if r["status"] == "failed":
            m["failed"] += 1
        if r["latency_ms"]:
            m["latencies"].append(r["latency_ms"])

    job_stats = queue.stats(conn)
    conn.close()

    total_cost = sum(float(r["cost"]) for r in real)
    today_cost = sum(float(r["cost"]) for r in today)
    failed_today = sum(1 for r in today if r["status"] == "failed")
    return {
        "total_cost": total_cost, "today_cost": today_cost,
        "calls": len(real), "hits": len(hits),
        "today_calls": len(today), "today_failed": failed_today,
        "per_model": per_model, "jobs": job_stats,
        "budget": DAILY_BUDGET_LIMIT,
    }


def _today() -> str:
    import time
    return time.strftime("%Y-%m-%d")


def render(d: dict) -> str:
    lines = ["== OneTake 系统仪表盘 =="]
    lines.append(f"累计成本 ¥{d['total_cost']:.2f} · 今日 ¥{d['today_cost']:.2f}"
                 f" / 日熔断 ¥{d['budget']:.0f}"
                 f"（{d['today_cost'] / d['budget'] * 100:.0f}%）")
    total_calls = d["calls"] + d["hits"]
    rate = d["hits"] / total_calls * 100 if total_calls else 0
    lines.append(f"调用 {d['calls']} 次（今日 {d['today_calls']}）· 缓存命中 {d['hits']} 次"
                 f"（{rate:.1f}%）")

    lines.append("\n-- 模型表现 --")
    lines.append(f"{'模型':<32}{'调用':>4} {'失败':>4} {'成本':>8} {'均时延':>7}")
    for name, m in sorted(d["per_model"].items(), key=lambda x: -x[1]["cost"]):
        avg_lat = sum(m["latencies"]) / len(m["latencies"]) / 1000 if m["latencies"] else 0
        lines.append(f"{name:<32}{m['calls']:>4} {m['failed']:>4} "
                     f"¥{m['cost']:>7.2f} {avg_lat:>6.1f}s")

    j = d["jobs"]
    lines.append(f"\n-- 任务队列 -- pending {j.get('pending', 0)} · running {j.get('running', 0)}"
                 f" · succeeded {j.get('succeeded', 0)} · dead {j.get('dead', 0)}")

    # 告警
    alerts = []
    if d["today_cost"] > d["budget"] * 0.8:
        alerts.append(f"今日花费已达预算 {d['today_cost'] / d['budget'] * 100:.0f}%")
    if d["today_calls"] and d["today_failed"] / d["today_calls"] > 0.2:
        alerts.append(f"今日失败率 {d['today_failed'] / d['today_calls'] * 100:.0f}% 超 20%")
    if j.get("dead", 0) > 0:
        alerts.append(f"有 {j['dead']} 个死信任务待处理（jobs retry）")
    if j.get("pending", 0) > 5:
        alerts.append(f"队列积压 {j['pending']} 个任务")
    lines.append("\n-- 告警 --")
    lines += [f"⚠️ {a}" for a in alerts] if alerts else ["✅ 无告警"]
    return "\n".join(lines)
