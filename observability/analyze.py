"""analyze 报告（P5）：把运行数据变成决策依据。

三个部分：① 模型质量-成本对比（灰度/选型的持续报表）；② 失败模式聚类
（经验记忆的原料）；③ 缓存收益趋势。数据源：generations + model_perf_daily。
"""

import re
from collections import defaultdict

from db import dao


def _classify_error(error: str) -> str:
    """失败原因归类（错误文本 → 模式标签）。"""
    if not error:
        return "unknown"
    e = error.lower()
    if "modelnotopen" in e or "invalidendpointormodel" in e:
        return "模型未开通/不存在"
    if "预算熔断" in error or "budget" in e:
        return "日预算熔断"
    if "connection" in e or "connect" in e:
        return "网络连接失败"
    if "no audio" in e or "noaudioreceived" in e:
        return "edge-tts 瞬断"
    if "timeout" in e or "超时" in error:
        return "超时"
    if "模拟" in error:
        return "演练注入故障"
    return error[:30]


def collect() -> dict:
    conn = dao.get_conn()
    rows = conn.execute("SELECT * FROM generations WHERE status != 'cache_hit'").fetchall()
    perf = conn.execute("SELECT * FROM model_perf_daily ORDER BY date").fetchall()
    conn.close()

    # 模型对比（按模型聚合全周期）
    models: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "cost": 0.0, "lat": [], "failed": 0})
    for r in rows:
        m = models[r["model"]]
        m["calls"] += 1
        m["cost"] += float(r["cost"])
        if r["status"] == "failed":
            m["failed"] += 1
        if r["latency_ms"]:
            m["lat"].append(r["latency_ms"])

    # 失败模式聚类
    failures = defaultdict(int)
    for r in rows:
        if r["status"] == "failed":
            failures[_classify_error(r["error"] or "")] += 1

    # 缓存收益（按天）
    conn = dao.get_conn()
    cache_rows = conn.execute(
        "SELECT date(created_at) AS d, status, COUNT(*) AS n FROM generations"
        " GROUP BY d, status").fetchall()
    conn.close()
    by_day: dict[str, dict] = defaultdict(lambda: {"real": 0, "hit": 0})
    for r in cache_rows:
        by_day[r["d"]]["hit" if r["status"] == "cache_hit" else "real"] += r["n"]

    return {"models": models, "failures": failures, "by_day": by_day,
            "perf_days": len(perf)}


def render(d: dict) -> str:
    lines = ["== OneTake 数据分析报告 =="]

    lines.append("\n-- 模型对比（全周期） --")
    lines.append(f"{'模型':<32}{'调用':>4} {'成功率':>7} {'均成本':>8} {'均时延':>7}")
    for name, m in sorted(d["models"].items(), key=lambda x: -x[1]["cost"]):
        sr = (m["calls"] - m["failed"]) / m["calls"] * 100 if m["calls"] else 0
        avg_c = m["cost"] / m["calls"] if m["calls"] else 0
        avg_l = sum(m["lat"]) / len(m["lat"]) / 1000 if m["lat"] else 0
        lines.append(f"{name:<32}{m['calls']:>4} {sr:>6.1f}% ¥{avg_c:>7.3f} {avg_l:>6.1f}s")

    # 数据驱动结论（fast vs 标准档对比自动结论）
    fast = d["models"].get("doubao-seedance-2-0-fast-260128")
    std = d["models"].get("doubao-seedance-2-0-260128")
    if fast and std and fast["calls"] >= 3 and std["calls"] >= 1:
        fast_lat = sum(fast["lat"]) / len(fast["lat"]) / 1000 if fast["lat"] else 0
        std_lat = sum(std["lat"]) / len(std["lat"]) / 1000 if std["lat"] else 0
        fast_c = fast["cost"] / fast["calls"]
        std_c = std["cost"] / std["calls"]
        lines.append(f"\n数据结论：fast vs 标准档——成本低 {(1 - fast_c / std_c) * 100:.0f}%、"
                     f"时延低 {(1 - fast_lat / std_lat) * 100:.0f}%，草稿档选型成立 ✅"
                     if fast_c < std_c and fast_lat < std_lat else
                     "\n数据结论：fast 与标准档优势不明显，需人工复核选型")

    lines.append("\n-- 失败模式分布 --")
    if d["failures"]:
        for pattern, n in sorted(d["failures"].items(), key=lambda x: -x[1]):
            lines.append(f"  {pattern}: {n} 次")
    else:
        lines.append("  无失败记录")

    lines.append("\n-- 缓存收益趋势 --")
    for day, c in sorted(d["by_day"].items()):
        total = c["real"] + c["hit"]
        rate = c["hit"] / total * 100 if total else 0
        lines.append(f"  {day}: 调用 {total} · 命中 {c['hit']}（{rate:.0f}%）")
    return "\n".join(lines)
