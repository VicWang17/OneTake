"""Skill 加载器（P6）：YAML → 校验 → 注册进 skills 表。

原则：配置文件也是不可信输入——先校验再使用（同分镜表校验思想）。
同名重复注册 = 更新版本（upsert）。
"""

from pathlib import Path

import yaml

from db import dao

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

REQUIRED = {"name", "version", "match", "structure", "style"}
STYLE_KEYS = {"tone", "visual", "voice"}


def validate_skill(data: dict, path: Path) -> list[str]:
    """校验 Skill 包，返回错误清单（空 = 合法）。"""
    errors = []
    if not isinstance(data, dict):
        return ["顶层必须是 YAML 对象"]
    missing = REQUIRED - set(data)
    if missing:
        errors.append(f"缺必填字段: {missing}")
    if not isinstance(data.get("structure"), list) or not data.get("structure"):
        errors.append("structure 必须是非空数组")
    style = data.get("style") or {}
    if not isinstance(style, dict) or STYLE_KEYS - set(style):
        errors.append(f"style 必须包含 {STYLE_KEYS}")
    if not isinstance(data.get("match", {}).get("genres", []), list):
        errors.append("match.genres 必须是数组")
    return errors


def register_all() -> list[dict]:
    """扫描 skills/ 目录，校验并注册全部 YAML。返回注册结果。"""
    results = []
    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = validate_skill(data, path)
        if errors:
            results.append({"file": path.name, "ok": False, "errors": errors})
            continue
        conn = dao.get_conn()
        existing = conn.execute("SELECT id FROM skills WHERE name = ?",
                                (data["name"],)).fetchone()
        if existing:
            conn.execute("UPDATE skills SET version = ?, yaml_path = ? WHERE name = ?",
                         (str(data["version"]), str(path), data["name"]))
        else:
            import uuid
            conn.execute(
                "INSERT INTO skills (id, name, version, yaml_path, metrics_json)"
                " VALUES (?, ?, ?, ?, '{}')",
                (uuid.uuid4().hex[:12], data["name"], str(data["version"]), str(path)))
        conn.commit()
        conn.close()
        results.append({"file": path.name, "ok": True, "name": data["name"],
                        "version": str(data["version"])})
    return results


def list_skills() -> list[dict]:
    """已注册 Skill 清单（含 YAML 内容）。"""
    conn = dao.get_conn()
    rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
    conn.close()
    out = []
    for r in rows:
        data = yaml.safe_load(Path(r["yaml_path"]).read_text(encoding="utf-8"))
        out.append({"id": r["id"], "name": r["name"], "version": r["version"],
                    "data": data})
    return out


def get_skill(name: str) -> dict | None:
    for s in list_skills():
        if s["name"] == name:
            return s
    return None
