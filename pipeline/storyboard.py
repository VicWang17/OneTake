"""分镜包管线（P1）：选题 → 大纲 → 分镜表 → 分镜图。

与 linear.py（P0 固定文案管线）并存；P2 端到端时两者汇合：
storyboard 产出的分镜包（script.json + shots/*.png）就是 linear 的输入源。
"""

import json
import time
from pathlib import Path

from db import dao
from nodes import outline as outline_node

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


def create_outline(topic: str) -> dict:
    """1.1 大纲生成：建项目 → LLM 大纲 → script.json + 风格入库。"""
    pid = time.strftime("p%Y%m%d-%H%M%S")
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)

    conn = dao.get_conn()
    dao.create_project(conn, topic=topic, pid=pid)

    data = outline_node.generate_outline(topic, pid)
    (pdir / "script.json").write_text(
        json.dumps({"topic": topic, "outline": data}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    dao.update_project(conn, pid, style_json=json.dumps(data["style"], ensure_ascii=False),
                       status="outlined")
    conn.close()
    return {"pid": pid, "outline": data}
