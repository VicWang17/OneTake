"""事件日志（P5 数据链路原料层）：append-only，全带 trace_id。

kind：generation（模型调用）/ job（任务状态变化）/ eval（质检，P7 接入）。
与结构化日志的分工：olog 管"系统现在在干什么"（排障），events 管"发生了什么
事实"（聚合分析的原料）——events 进 SQLite 可 SQL 聚合，日志是 JSONL 文件。
"""

import json
import uuid

from db import dao
from observability import logging as olog


def emit(kind: str, ref_id: str | None = None, **data) -> None:
    conn = dao.get_conn()
    conn.execute(
        "INSERT INTO events (id, trace_id, kind, ref_id, data_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:12], olog.trace_id_var.get(), kind, ref_id,
         json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()


def list_by_kind(kind: str) -> list[dict]:
    conn = dao.get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE kind = ? ORDER BY ts", (kind,)).fetchall()
    conn.close()
    return rows
