"""模型网关统一入口（P0 内部库形态，P4 服务化为 HTTP API）。

两条铁律的承载点之一：管线节点所有外部模型调用必须经 call()，
不得直接 import 厂商 SDK。本层负责：日预算熔断、计费日志（generations 表）、
用量→成本换算（pricing.py 为单价唯一事实源）。
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from db import dao
from gateway import adapters, pricing

load_dotenv()

DAILY_BUDGET_LIMIT = float(os.environ.get("DAILY_BUDGET_LIMIT", "15"))


class BudgetExceededError(RuntimeError):
    """当日累计成本达到 DAILY_BUDGET_LIMIT 时抛出（硬熔断）。"""


def _check_budget(conn) -> None:
    spent = dao.today_spend(conn)
    if spent >= DAILY_BUDGET_LIMIT:
        raise BudgetExceededError(
            f"日预算熔断：今日已花费 ¥{spent:.2f} ≥ 上限 ¥{DAILY_BUDGET_LIMIT:.2f}"
        )


def call(task_type: str, payload: dict[str, Any], tier: str = "draft",
         project_id: str | None = None) -> dict[str, Any]:
    """统一入口。task_type: llm / image / video / tts。

    payload 按 task_type 约定：
    - llm:   {system, user}                                     → {data, usage, cost}
    - image: {prompt, size?}                                    → {url, usage, cost}
    - video: {prompt, out_path, model?, seconds?, resolution?}  → {file_path, usage, cost}
    - tts:   {text, out_path}                                   → {file_path, cost}
    所有返回 dict 都带 cost（人民币元）与 model。
    """
    conn = dao.get_conn()
    try:
        _check_budget(conn)
        if task_type == "llm":
            return _call_llm(conn, payload, tier, project_id)
        if task_type == "image":
            return _call_image(conn, payload, tier, project_id)
        if task_type == "video":
            return _call_video(conn, payload, tier, project_id)
        if task_type == "tts":
            return _call_tts(conn, payload, tier, project_id)
        raise ValueError(f"未知 task_type: {task_type}")
    finally:
        conn.close()


def _call_llm(conn, payload, tier, project_id):
    model = adapters.DEEPSEEK_MODEL
    try:
        if "messages" in payload:  # 多轮模式（错误回灌等），返回原始文本
            text, usage, latency = adapters.deepseek_messages(payload["messages"])
            cost = pricing.calc_cost(model, usage)
            dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                               prompt=json.dumps(payload["messages"], ensure_ascii=False),
                               usage=usage, cost=cost, latency_ms=latency,
                               project_id=project_id)
            return {"text": text, "usage": usage, "cost": cost, "model": model}
        data, usage, latency = adapters.deepseek_json(payload["system"], payload["user"])
        cost = pricing.calc_cost(model, usage)
        dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                           prompt=payload["user"], usage=usage, cost=cost,
                           latency_ms=latency, project_id=project_id)
        return {"data": data, "usage": usage, "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                           prompt=payload.get("user") or json.dumps(payload.get("messages", []), ensure_ascii=False)[:500],
                           status="failed", error=str(e), project_id=project_id)
        raise


def _call_image(conn, payload, tier, project_id):
    model = adapters.SEEDREAM_MODEL
    try:
        url, usage, latency = adapters.seedream_image(
            payload["prompt"], payload.get("size", "1280x720"),
            reference_url=payload.get("reference_url"))
        cost = pricing.calc_cost(model, usage, n_images=1)
        dao.log_generation(conn, task_type="image", model=model, tier=tier,
                           prompt=payload["prompt"],
                           params={"reference": bool(payload.get("reference_url"))},
                           usage=usage,
                           unit_price=pricing.PRICING[model].get("per_image", 0),
                           cost=cost, latency_ms=latency, project_id=project_id)
        return {"url": url, "usage": usage, "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="image", model=model, tier=tier,
                           prompt=payload.get("prompt"), status="failed",
                           error=str(e), project_id=project_id)
        raise


def _call_video(conn, payload, tier, project_id):
    model = payload.get("model", adapters.SEEDANCE_MODEL)
    seconds = int(payload.get("seconds", 5))
    resolution = payload.get("resolution", "480p")
    try:
        info, latency = adapters.seedance_video(
            payload["prompt"], Path(payload["out_path"]), model=model,
            seconds=seconds, resolution=resolution,
            first_frame_url=payload.get("first_frame_url"))
        cost = pricing.calc_cost(model, info.get("usage"),
                                 seconds=seconds, resolution=resolution)
        dao.log_generation(conn, task_type="video", model=model, tier=tier,
                           prompt=payload["prompt"], params={"seconds": seconds,
                                                             "resolution": resolution},
                           usage=info.get("usage"), cost=cost, latency_ms=latency,
                           file_path=str(payload["out_path"]), project_id=project_id)
        return {"file_path": str(payload["out_path"]), "usage": info.get("usage"),
                "cost": cost, "model": model, "latency_ms": latency}
    except Exception as e:
        dao.log_generation(conn, task_type="video", model=model, tier=tier,
                           prompt=payload.get("prompt"), status="failed",
                           error=str(e), project_id=project_id)
        raise


def _call_tts(conn, payload, tier, project_id):
    model = "edge-tts"
    try:
        adapters.edge_tts_speak(payload["text"], Path(payload["out_path"]))
        cost = 0.0
        dao.log_generation(conn, task_type="tts", model=model, tier=tier,
                           prompt=payload["text"], cost=cost,
                           file_path=str(payload["out_path"]), project_id=project_id)
        return {"file_path": str(payload["out_path"]), "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="tts", model=model, tier=tier,
                           prompt=payload.get("text"), status="failed",
                           error=str(e), project_id=project_id)
        raise
