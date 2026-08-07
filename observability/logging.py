"""结构化日志 + trace 贯穿（P5 可观测性基石）。

- 全系统 JSON 行日志：logs/onetake.jsonl，每条带 ts/level/trace_id/node/event
- trace_id 经 contextvars 传递：一次 run 开始 set_trace(pid)，之后网关、调度器、
  节点写的日志自动带上——不用逐函数传参
- 查询：给我一个 trace_id，就能捞出一次运行的完整调用链
"""

import contextvars
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "onetake.jsonl"

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None)
node_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "node", default=None)


def set_trace(trace_id: str) -> None:
    """一次运行的入口设置（run_topic / run_graph / worker 领任务时）。"""
    trace_id_var.set(trace_id)


def set_node(node: str) -> None:
    node_var.set(node)


def log(event: str, level: str = "INFO", **fields) -> None:
    """写一条结构化日志。event 为动词短语（如 model_call / job_done / stage）。"""
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "trace_id": trace_id_var.get(),
        "node": node_var.get(),
        "event": event,
        **fields,
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def query_trace(trace_id: str) -> list[dict]:
    """按 trace_id 捞出完整调用链（时间升序）。"""
    if not LOG_FILE.exists():
        return []
    out = []
    with LOG_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("trace_id") == trace_id:
                out.append(r)
    return out
