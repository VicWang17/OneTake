"""分镜包管线（P1）：选题 → 大纲 → 分镜表 → 分镜图。

与 linear.py（P0 固定文案管线）并存；P2 端到端时两者汇合：
storyboard 产出的分镜包（script.json + shots/*.png）就是 linear 的输入源。
"""

import json
import time
from pathlib import Path

from db import dao
from nodes import outline as outline_node
from nodes import storyboard as storyboard_node

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


def create_storyboard(topic: str | None = None, pid: str | None = None) -> dict:
    """1.2 分镜表：读大纲（已有 pid 或现场生成）→ LLM 分镜表（校验回灌）→ 落盘 + 入库。"""
    conn = dao.get_conn()
    if pid:
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        outline_data = script["outline"]
    else:
        result = create_outline(topic)
        pid, outline_data = result["pid"], result["outline"]
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))

    shots = storyboard_node.generate_storyboard(outline_data, pid)
    for s in shots:
        dao.create_shot(conn, project_id=pid, idx=s["idx"], duration=s["duration"],
                        visual_prompt=s["visual_prompt"], narration=s["narration"],
                        status="storyboarded")
    script["shots"] = shots
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    dao.update_project(conn, pid, status="storyboarded")
    conn.close()
    return {"pid": pid, "shots": shots}
