"""OneTake 数据层：SQLite 连接与最小 CRUD。

P0 只用 projects / shots / generations 三张表的写入与查询，
其余表（jobs/events/model_perf_daily/skills/memories）schema 已建好，P4+ 启用。
"""

import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "projects" / "onetake.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    """返回 SQLite 连接（row_factory=Row）。库不存在时按 schema.sql 自动建表。"""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not path.exists() or path.stat().st_size == 0 or not _has_tables(conn):
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：老库缺列时 ALTER 补齐（SQLite 加列低成本）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "style_json" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN style_json TEXT")
        conn.commit()
    gcols = {r["name"] for r in conn.execute("PRAGMA table_info(generations)")}
    if "result_json" not in gcols:
        conn.execute("ALTER TABLE generations ADD COLUMN result_json TEXT")
        conn.commit()


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
    ).fetchone()
    return row is not None


def _dump(v) -> str | None:
    return json.dumps(v, ensure_ascii=False) if v is not None else None


# ---------- projects ----------

def create_project(conn: sqlite3.Connection, topic: str, pid: str | None = None,
                   skill_id: str | None = None) -> str:
    pid = pid or f"p{_new_id()}"
    conn.execute(
        "INSERT INTO projects (id, topic, skill_id) VALUES (?, ?, ?)",
        (pid, topic, skill_id),
    )
    conn.commit()
    return pid


def get_project(conn: sqlite3.Connection, pid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()


def update_project(conn: sqlite3.Connection, pid: str, **fields) -> None:
    """更新 projects 行（如 style_json、status）。"""
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), pid))
    conn.commit()


# ---------- shots ----------

def create_shot(conn: sqlite3.Connection, project_id: str, idx: int,
                duration: float | None = None, visual_prompt: str | None = None,
                narration: str | None = None, status: str = "created",
                shot_id: str | None = None) -> str:
    shot_id = shot_id or f"{project_id}-s{idx:02d}"
    conn.execute(
        "INSERT INTO shots (id, project_id, idx, duration, visual_prompt, narration, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (shot_id, project_id, idx, duration, visual_prompt, narration, status),
    )
    conn.commit()
    return shot_id


def update_shot(conn: sqlite3.Connection, shot_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE shots SET {cols} WHERE id = ?", (*fields.values(), shot_id))
    conn.commit()


def list_shots(conn: sqlite3.Connection, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM shots WHERE project_id = ? ORDER BY idx", (project_id,)
    ).fetchall()


def delete_shots(conn: sqlite3.Connection, project_id: str) -> None:
    """删除项目全部分镜（脚本打回重生成时用）。"""
    conn.execute("DELETE FROM shots WHERE project_id = ?", (project_id,))
    conn.commit()


# ---------- generations（计费日志） ----------

def log_generation(conn: sqlite3.Connection, *, task_type: str, model: str,
                   tier: str = "draft", prompt: str | None = None,
                   params: dict | None = None, usage: dict | None = None,
                   unit_price: float = 0.0, cost: float = 0.0,
                   latency_ms: int | None = None, status: str = "succeeded",
                   error: str | None = None, file_path: str | None = None,
                   project_id: str | None = None, idem_key: str | None = None,
                   result_json: str | None = None) -> str:
    gid = _new_id()
    conn.execute(
        "INSERT INTO generations"
        " (id, idem_key, project_id, task_type, model, tier, prompt, params,"
        "  usage_json, unit_price, cost, latency_ms, status, error, file_path, result_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (gid, idem_key, project_id, task_type, model, tier, prompt,
         _dump(params), _dump(usage), unit_price, cost, latency_ms, status,
         error, file_path, result_json),
    )
    conn.commit()
    return gid


def find_generation_by_idem(conn: sqlite3.Connection, idem_key: str) -> sqlite3.Row | None:
    """按幂等键查成功调用记录（缓存命中依据）。"""
    return conn.execute(
        "SELECT * FROM generations WHERE idem_key = ? AND status = 'succeeded'",
        (idem_key,),
    ).fetchone()


def today_spend(conn: sqlite3.Connection) -> float:
    """当日累计成本（人民币元），日预算熔断依据。"""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM generations"
        " WHERE date(created_at) = date('now', 'localtime')"
    ).fetchone()
    return float(row["total"])


def list_generations(conn: sqlite3.Connection,
                     project_id: str | None = None) -> list[sqlite3.Row]:
    if project_id:
        return conn.execute(
            "SELECT * FROM generations WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return conn.execute("SELECT * FROM generations ORDER BY created_at").fetchall()
