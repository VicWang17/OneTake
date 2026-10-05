"""记忆升级用例（P1）：来源/作用域持久化、contradict 显式替代、作用域过滤、旧库迁移。

零 API 成本：临时 SQLite + monkeypatch gw.call（LLM 合并/检索决策）。
用法：uv run python evals/runners/memory_cases.py
产出：evals/reports/memory_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from db import dao  # noqa: E402
from gateway import core as gw  # noqa: E402
from memory import inject, store  # noqa: E402

_REAL_GET_CONN = dao.get_conn
_REAL_GW_CALL = gw.call


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _use_tmp_db(tmp: Path) -> None:
    dao.get_conn = lambda *a, **kw: _REAL_GET_CONN(tmp / "test.db")


def _llm_decide(decision: dict):
    def fake_call(task_type, payload, tier="draft", project_id=None):  # noqa: ARG001
        return {"data": decision, "cost": 0.0, "model": "fake"}
    return fake_call


def run_cases() -> list[dict]:
    results = []

    # 1. scope/source 持久化
    with tempfile.TemporaryDirectory() as tmp_s:
        _use_tmp_db(Path(tmp_s))
        gw.call = _llm_decide({"action": "add"})
        try:
            r = store.add("profile", "标题必须带数字", scope="global", source="manual")
            store.add("episode", "本项目不要人物", scope="project:p123", source="confirmed")
            allm = store.list_all()
            proj = next(m for m in allm if m["id"] != r["id"])
            checks = [
                ("scope_source_saved", r["id"] and proj["scope"] == "project:p123"
                 and proj["source"] == "confirmed"),
            ]
        finally:
            gw.call = _REAL_GW_CALL
            dao.get_conn = _REAL_GET_CONN
    results.append({"case_id": "mem-scope-source", "checks": checks})

    # 2. contradict → 旧条显式 superseded_by，注入候选排除
    with tempfile.TemporaryDirectory() as tmp_s:
        _use_tmp_db(Path(tmp_s))
        try:
            gw.call = _llm_decide({"action": "add"})
            first = store.add("profile", "标题必须带数字")
            gw.call = _llm_decide({"action": "contradict", "target": first["id"],
                                   "reason": "用户改偏好"})
            second = store.add("profile", "标题不要带数字")
            visible = store.list_all(min_confidence=store.CONF_INJECT_MIN)
            all_inc = store.list_all(include_superseded=True)
            old = next(m for m in all_inc if m["id"] == first["id"])
            checks = [
                ("old_superseded_by_new", old["superseded_by"] == second["new_id"]),
                ("superseded_excluded",
                 all(m["id"] != first["id"] for m in visible)
                 and any(m["id"] == second["new_id"] for m in visible)),
                ("audit_trail_kept", len(all_inc) == 2),  # 不物理删除
            ]
        finally:
            gw.call = _REAL_GW_CALL
            dao.get_conn = _REAL_GET_CONN
    results.append({"case_id": "mem-contradict-supersede", "checks": checks})

    # 3. 作用域过滤：本项目记忆注入，他项目不注入
    with tempfile.TemporaryDirectory() as tmp_s:
        _use_tmp_db(Path(tmp_s))
        try:
            gw.call = _llm_decide({"action": "add"})
            store.add("episode", "p123 专属经验", scope="project:p123")
            store.add("episode", "p999 专属经验", scope="project:p999")
            store.add("profile", "全局偏好", scope="global")
            gw.call = _llm_decide({"pick": "all"})  # 占位，下面真实现按候选返回
            def pick_all(task_type, payload, tier="draft", project_id=None):  # noqa: ARG001
                # 从 prompt 里抠候选 id（catalog 格式 [id]），全选
                import re
                ids = re.findall(r"\[([0-9a-f]{8})\]", payload["user"])
                return {"data": {"pick": ids}, "cost": 0.0, "model": "fake"}
            gw.call = pick_all
            picked = inject.get_relevant("任意选题", k=10, project_id="p123")
            contents = [m["content"] for m in picked]
            checks = [
                ("own_project_injected", "p123 专属经验" in contents),
                ("other_project_excluded", "p999 专属经验" not in contents),
                ("global_injected", "全局偏好" in contents),
            ]
        finally:
            gw.call = _REAL_GW_CALL
            dao.get_conn = _REAL_GET_CONN
    results.append({"case_id": "mem-scope-filter", "checks": checks})

    # 4. 旧库迁移：无新列的 memories 表打开后自动补列且默认值正确
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        import sqlite3
        raw = sqlite3.connect(tmp / "old.db")
        raw.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, topic TEXT)")
        raw.execute("CREATE TABLE generations (id TEXT PRIMARY KEY)")
        raw.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, type TEXT NOT NULL,"
                    " content TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.5,"
                    " updated_at TEXT NOT NULL DEFAULT '')")
        raw.execute("INSERT INTO memories (id, type, content) VALUES ('m1', 'profile', '旧记忆')")
        raw.commit()
        raw.close()
        conn = _REAL_GET_CONN(tmp / "old.db")  # get_conn 触发 _migrate
        row = conn.execute("SELECT scope, source, superseded_by FROM memories"
                           " WHERE id = 'm1'").fetchone()
        checks = [
            ("migrated_defaults", row["scope"] == "global" and row["source"] == "manual"
             and row["superseded_by"] is None),
        ]
        conn.close()
    results.append({"case_id": "mem-migration", "checks": checks})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/memory_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (temp sqlite + monkeypatched llm, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"memory_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
