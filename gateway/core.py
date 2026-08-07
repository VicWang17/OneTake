"""模型网关统一入口（P0 内部库形态，P4 服务化为 HTTP API）。

两条铁律的承载点之一：管线节点所有外部模型调用必须经 call()，
不得直接 import 厂商 SDK。本层负责：日预算熔断、计费日志（generations 表）、
用量→成本换算（pricing.py 为单价唯一事实源）、幂等缓存（P3）。

幂等缓存（3.1）：idem_key = sha256(task_type + model + tier + 语义参数)。
内容寻址——同内容必然同 key 直接复用；任何参数变化 → 新 key → 自动重新生成。
命中双校验：记录存在 + 产物（文件/result_json）存在，互为验证。
命中记 status=cache_hit、cost=0（命中率统计依据；不影响日预算）。
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from db import dao
from gateway import adapters, pricing
from serving import registry

load_dotenv()

DAILY_BUDGET_LIMIT = float(os.environ.get("DAILY_BUDGET_LIMIT", "15"))


class BudgetExceededError(RuntimeError):
    """当日累计成本达到 DAILY_BUDGET_LIMIT 时抛出（硬熔断）。"""


# 失败降级链（3.4）：主模型抛异常 → 自动切备胎重试一次。备胎关系 P4 起由注册表驱动
# （serving/registry.yaml 各模型的 fallback 字段，热更新可调）。
# 降级结果不写 idem_key——不进缓存，下次运行仍优先试主力（防降级污染缓存）。


def _check_budget(conn) -> None:
    spent = dao.today_spend(conn)
    if spent >= DAILY_BUDGET_LIMIT:
        raise BudgetExceededError(
            f"日预算熔断：今日已花费 ¥{spent:.2f} ≥ 上限 ¥{DAILY_BUDGET_LIMIT:.2f}"
        )


def _idem_key(task_type: str, model: str, payload: dict, tier: str) -> str:
    """内容指纹：语义参数参与哈希，out_path 等项目特定字段剔除（跨项目可命中）。"""
    semantic = {k: v for k, v in payload.items() if k != "out_path"}
    raw = json.dumps([task_type, model, tier, semantic],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _try_cache(conn, idem_key: str, out_path: str | None, project_id: str | None):
    """命中且产物完好则返回缓存结果；否则 None。命中留痕 cache_hit（cost=0）。"""
    row = dao.find_generation_by_idem(conn, idem_key)
    if not row:
        return None
    cached_file = row["file_path"]
    if cached_file and Path(cached_file).exists():
        # 双校验通过：文件在。目标位置不同则复制（跨项目命中）
        if out_path and Path(cached_file) != Path(out_path):
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cached_file, out_path)
        result = {"cached": True, "file_path": out_path or cached_file,
                  "cost": 0.0, "model": row["model"], "latency_ms": 0}
    elif row["result_json"]:
        result = {"cached": True, "cost": 0.0, "model": row["model"],
                  "latency_ms": 0}
        result.update(json.loads(row["result_json"]))
    else:
        return None  # 记录在但产物丢失 → 视为未命中
    dao.log_generation(conn, task_type=row["task_type"], model=row["model"],
                       tier=row["tier"], prompt=row["prompt"], cost=0.0,
                       status="cache_hit", project_id=project_id)
    return result


def call(task_type: str, payload: dict[str, Any], tier: str = "draft",
         project_id: str | None = None) -> dict[str, Any]:
    """统一入口。task_type: llm / image / video / tts。

    payload 按 task_type 约定：
    - llm:   {system, user} 或 {messages}                       → {data|text, usage, cost}
    - image: {prompt, size?, reference_url?, out_path?}         → {url|file_path, cost}
    - video: {prompt, out_path, model?, seconds?, resolution?}  → {file_path, usage, cost}
    - tts:   {text, out_path}                                   → {file_path, cost}
    所有返回 dict 都带 cost（人民币元）与 model；缓存命中带 cached=True。
    """
    conn = dao.get_conn()
    try:
        model = payload.get("model") or {
            "llm": adapters.DEEPSEEK_MODEL, "image": adapters.SEEDREAM_MODEL,
            "video": adapters.SEEDANCE_MODEL, "tts": "edge-tts",
        }.get(task_type, task_type)
        idem_key = _idem_key(task_type, model, payload, tier)
        cached = _try_cache(conn, idem_key, payload.get("out_path"), project_id)
        if cached:
            return cached

        _check_budget(conn)
        if task_type == "llm":
            return _call_llm(conn, payload, tier, project_id, idem_key)
        if task_type == "image":
            return _call_image(conn, payload, tier, project_id, idem_key)
        if task_type == "video":
            return _call_video(conn, payload, tier, project_id, idem_key)
        if task_type == "tts":
            return _call_tts(conn, payload, tier, project_id, idem_key)
        raise ValueError(f"未知 task_type: {task_type}")
    finally:
        conn.close()


def _call_llm(conn, payload, tier, project_id, idem_key):
    model = adapters.DEEPSEEK_MODEL
    try:
        if "messages" in payload:  # 多轮模式（错误回灌等），返回原始文本
            text, usage, latency = adapters.deepseek_messages(payload["messages"])
            cost = pricing.calc_cost(model, usage)
            dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                               prompt=json.dumps(payload["messages"], ensure_ascii=False)[:2000],
                               usage=usage, cost=cost, latency_ms=latency,
                               project_id=project_id, idem_key=idem_key,
                               result_json=json.dumps({"text": text}, ensure_ascii=False))
            return {"text": text, "usage": usage, "cost": cost, "model": model}
        data, usage, latency = adapters.deepseek_json(payload["system"], payload["user"])
        cost = pricing.calc_cost(model, usage)
        dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                           prompt=payload["user"], usage=usage, cost=cost,
                           latency_ms=latency, project_id=project_id,
                           idem_key=idem_key,
                           result_json=json.dumps({"data": data}, ensure_ascii=False))
        return {"data": data, "usage": usage, "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="llm", model=model, tier=tier,
                           prompt=payload.get("user") or json.dumps(payload.get("messages", []), ensure_ascii=False)[:500],
                           status="failed", error=str(e), project_id=project_id)
        fb = registry.get_fallback(model)
        if not fb:
            raise
        # 降级：DeepSeek → Qwen（跨供应商；不写 idem_key，不污染缓存）
        msgs = payload.get("messages") or [
            {"role": "system", "content": payload["system"]},
            {"role": "user", "content": payload["user"]},
        ]
        text, usage, latency = adapters.qwen_messages(msgs)
        cost = pricing.calc_cost(fb, usage)
        dao.log_generation(conn, task_type="llm", model=fb, tier=tier,
                           prompt=json.dumps(msgs, ensure_ascii=False)[:2000],
                           params={"degraded_from": model}, usage=usage, cost=cost,
                           latency_ms=latency, project_id=project_id,
                           result_json=json.dumps({"text": text}, ensure_ascii=False))
        if "messages" in payload:
            return {"text": text, "usage": usage, "cost": cost, "model": fb,
                    "degraded_from": model}
        return {"data": json.loads(text), "usage": usage, "cost": cost,
                "model": fb, "degraded_from": model}


def _call_image(conn, payload, tier, project_id, idem_key):
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
                           cost=cost, latency_ms=latency, project_id=project_id,
                           idem_key=idem_key,
                           file_path=payload.get("out_path"),
                           result_json=json.dumps({"url": url}))
        return {"url": url, "usage": usage, "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="image", model=model, tier=tier,
                           prompt=payload.get("prompt"), status="failed",
                           error=str(e), project_id=project_id)
        fb = registry.get_fallback(model)
        if not fb:
            raise
        # 降级：Seedream 4.0 → 4.5（跨版本；不写 idem_key，不污染缓存）
        url, usage, latency = adapters.seedream_image(
            payload["prompt"], payload.get("size", "1280x720"),
            reference_url=payload.get("reference_url"), model=fb)
        cost = pricing.calc_cost(fb, usage, n_images=1)
        dao.log_generation(conn, task_type="image", model=fb, tier=tier,
                           prompt=payload["prompt"],
                           params={"degraded_from": model},
                           usage=usage,
                           unit_price=pricing.PRICING[fb].get("per_image", 0),
                           cost=cost, latency_ms=latency, project_id=project_id,
                           file_path=payload.get("out_path"),
                           result_json=json.dumps({"url": url}))
        return {"url": url, "usage": usage, "cost": cost, "model": fb,
                "degraded_from": model}


def _call_video(conn, payload, tier, project_id, idem_key):
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
                           file_path=str(payload["out_path"]), project_id=project_id,
                           idem_key=idem_key)
        return {"file_path": str(payload["out_path"]), "usage": info.get("usage"),
                "cost": cost, "model": model, "latency_ms": latency}
    except Exception as e:
        dao.log_generation(conn, task_type="video", model=model, tier=tier,
                           prompt=payload.get("prompt"), status="failed",
                           error=str(e), project_id=project_id)
        fb = registry.get_fallback(model)
        if not fb:
            raise
        # 降级：Seedance 跨档互备（不写 idem_key，不污染缓存）
        info, latency = adapters.seedance_video(
            payload["prompt"], Path(payload["out_path"]), model=fb,
            seconds=seconds, resolution=resolution,
            first_frame_url=payload.get("first_frame_url"))
        cost = pricing.calc_cost(fb, info.get("usage"),
                                 seconds=seconds, resolution=resolution)
        dao.log_generation(conn, task_type="video", model=fb, tier=tier,
                           prompt=payload["prompt"],
                           params={"seconds": seconds, "resolution": resolution,
                                   "degraded_from": model},
                           usage=info.get("usage"), cost=cost, latency_ms=latency,
                           file_path=str(payload["out_path"]), project_id=project_id)
        return {"file_path": str(payload["out_path"]), "usage": info.get("usage"),
                "cost": cost, "model": fb, "latency_ms": latency,
                "degraded_from": model}


def _call_tts(conn, payload, tier, project_id, idem_key):
    model = "edge-tts"
    try:
        adapters.edge_tts_speak(payload["text"], Path(payload["out_path"]))
        cost = 0.0
        dao.log_generation(conn, task_type="tts", model=model, tier=tier,
                           prompt=payload["text"], cost=cost,
                           file_path=str(payload["out_path"]), project_id=project_id,
                           idem_key=idem_key)
        return {"file_path": str(payload["out_path"]), "cost": cost, "model": model}
    except Exception as e:
        dao.log_generation(conn, task_type="tts", model=model, tier=tier,
                           prompt=payload.get("text"), status="failed",
                           error=str(e), project_id=project_id)
        raise
