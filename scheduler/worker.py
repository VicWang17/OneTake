"""worker 池（P4）：asyncio 消费 jobs 表，按 provider 信号量限流。

设计：生产者（管线）只 enqueue，执行由本模块的 worker 完成——提交/执行/监控解耦。
worker 崩溃恢复：run_workers 启动时先 recover_orphans 回收上任遗留的 running 任务。
同步厂商 SDK 经 asyncio.to_thread 执行，不阻塞事件循环。
"""

import asyncio
import json
import uuid

from db import dao
from scheduler import queue
from observability import logging as olog
from serving import registry

# 任务类型 → 处理器（在 handlers.py 注册）
_HANDLERS: dict[str, callable] = {}


def register_handler(type_: str, fn) -> None:
    _HANDLERS[type_] = fn


def _provider_of(payload: dict) -> str:
    model = payload.get("model", "")
    m = registry.get_model(model)
    return m["provider"] if m else "default"


def _has_pending(conn) -> bool:
    """是否还有 pending 任务（含退避未到期的）——stop_when_empty 的退出判据。"""
    row = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'pending'").fetchone()
    return row["n"] > 0


async def _worker(worker_id: str, sems: dict[str, asyncio.Semaphore],
                  stop_when_empty: bool) -> None:
    conn = dao.get_conn()
    while True:
        job = queue.claim(conn, worker_id)
        if not job:
            # 无可领任务 ≠ 队列空：退避中的任务 run_at 在未来，需等待而非退出
            if stop_when_empty and not _has_pending(conn):
                break
            await asyncio.sleep(0.5)
            continue
        payload = json.loads(job["payload_json"])
        provider = _provider_of(payload)
        sem = sems.setdefault(provider, asyncio.Semaphore(
            _concurrency_of(provider)))
        handler = _HANDLERS[job["type"]]
        olog.set_trace(payload.get("pid") or job["id"])
        olog.set_node(f"job:{job['type']}")
        olog.log("job_start", job_id=job["id"], type=job["type"],
                 retry=job["retry_count"])
        async with sem:
            try:
                # 同步 SDK 放线程里跑，事件循环不被阻塞
                await asyncio.to_thread(handler, conn, job["id"], payload)
                queue.complete(conn, job["id"])
                olog.log("job_done", job_id=job["id"], type=job["type"])
                from datapipe import events
                events.emit("job", ref_id=job["id"], type=job["type"],
                            outcome="succeeded", retry=job["retry_count"])
                print(f"    [worker] job {job['id']} ({job['type']}) ✓")
            except Exception as e:  # noqa: BLE001
                new_status = queue.fail(conn, job["id"], str(e)[:200])
                olog.log("job_fail", level="ERROR", job_id=job["id"],
                         type=job["type"], new_status=new_status,
                         error=str(e)[:200])
                from datapipe import events
                events.emit("job", ref_id=job["id"], type=job["type"],
                            outcome=new_status, error=str(e)[:200])
                mark = "→ dead" if new_status == "dead" else f"→ 重试（第 {job['retry_count'] + 1} 次）"
                print(f"    [worker] job {job['id']} ({job['type']}) ✗ {mark}: {str(e)[:60]}")


def _concurrency_of(provider: str) -> int:
    """provider 并发上限：取注册表中该 provider 各模型的最小并发（最保守）。"""
    limits = [m.get("concurrency", 5) for m in registry.load_registry()
              if m["provider"] == provider]
    return min(limits) if limits else 5


def run_workers(stop_when_empty: bool = True) -> None:
    """启动 worker 池（含孤儿任务回收）。stop_when_empty：队列清空即退出（CLI 批处理模式）。"""
    worker_id = f"w-{uuid.uuid4().hex[:8]}"
    conn = dao.get_conn()
    n_orphans = queue.recover_orphans(conn, worker_id)
    if n_orphans:
        print(f"    [worker] 回收上任遗留 running 任务 {n_orphans} 个（回滚为 pending）")
    conn.close()

    async def _main():
        workers = [asyncio.create_task(_worker(f"{worker_id}-{i}", {}, stop_when_empty))
                   for i in range(3)]
        await asyncio.gather(*workers)

    asyncio.run(_main())


def handler_for(type_: str):
    return _HANDLERS[type_]
