"""LangGraph 图版编排（P3）：与 endtoend.py（线性版）并行，回归验证后转正。

结构：一条链七个节点 + 两个 interrupt 人工确认点。
- checkpointer（SQLite）：每节点后快照状态，进程死后同 pid 恢复——管"流程位置"
- 幂等缓存（3.1，网关层）：恢复重放的调用全部命中——管"成本不重花"
- 节点 = 现有能力函数的薄包装，能力层零改动（控制回归风险）
"""

import json
import sqlite3
import time
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from db import dao
from editing import edl as edl_mod
from editing import ffmpeg
from observability import logging as olog
from pipeline import storyboard as sb
from pipeline import videos as videos_mod
from pipeline.state import PipelineState

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
CHECKPOINT_DB = PROJECTS_DIR / "checkpoints.db"


# ---------- 节点（薄包装：取 state → 调能力 → 写回 state） ----------

def _load_script(pid: str) -> dict:
    return json.loads((PROJECTS_DIR / pid / "script.json").read_text(encoding="utf-8"))


def n_storyboard(state: PipelineState) -> dict:
    """大纲 + 分镜表 + 角色锚点（已有 script.json 则直接装载——续跑入口）。"""
    pid = state["pid"]
    script_path = PROJECTS_DIR / pid / "script.json"
    if script_path.exists():
        s = _load_script(pid)
    else:
        r = sb.create_storyboard(topic=state["topic"], pid=pid)
        s = _load_script(pid)
    return {"outline": s["outline"], "shots": s["shots"],
            "character_sheet": s.get("character_sheet", "")}


def n_confirm_script(state: PipelineState) -> dict:
    """确认点 1：脚本确认。auto 直通；否则 interrupt 等待人工决定。"""
    if state.get("auto"):
        return {}
    decision = interrupt({"kind": "confirm_script", "pid": state["pid"]})
    if decision == "n":
        return {"aborted": True}
    if isinstance(decision, dict) and decision.get("feedback"):  # r：带意见重生成
        sb.create_storyboard(pid=state["pid"], feedback=decision["feedback"])
        s = _load_script(state["pid"])
        return {"outline": s["outline"], "shots": s["shots"],
                "character_sheet": s.get("character_sheet", "")}
    return {}


def n_images(state: PipelineState) -> dict:
    return {"images_summary": sb.create_images(state["pid"])}


def n_confirm_images(state: PipelineState) -> dict:
    """确认点 2：分镜图确认。resume 值 {redo: {idx: 意见}} 时打回重画。"""
    if state.get("auto"):
        return {}
    decision = interrupt({"kind": "confirm_images", "pid": state["pid"]})
    if decision == "n":
        return {"aborted": True}
    if isinstance(decision, dict) and decision.get("redo"):
        sb.regenerate_images(state["pid"], {int(k): v for k, v in
                                            decision["redo"].items()})
    return {}


def n_align(state: PipelineState) -> dict:
    return {"align_summary": sb.align_audio(state["pid"])}


def n_videos(state: PipelineState) -> dict:
    r = videos_mod.batch_generate_videos(state["pid"])
    if r["failed"]:
        return {"videos_summary": r,
                "error": f"镜头视频失败 {r['failed_idx']}，修复后同 pid 续跑"}
    return {"videos_summary": r}


def n_render(state: PipelineState) -> dict:
    pid = state["pid"]
    edl = edl_mod.build_edl(pid)
    out = PROJECTS_DIR / pid / "final" / "draft.mp4"
    ffmpeg.render_edl(edl, out)
    conn = dao.get_conn()
    cost = sum(float(g["cost"]) for g in dao.list_generations(conn, project_id=pid))
    conn.close()
    return {"draft": str(out), "duration": edl["duration"], "cost": cost}


# ---------- 图结构 ----------

def _route_after_confirm(state: PipelineState) -> str:
    return END if state.get("aborted") else "continue"


def _route_after_videos(state: PipelineState) -> str:
    return END if state.get("error") else "continue"


def build_graph(saver) -> object:
    g = StateGraph(PipelineState)
    g.add_node("storyboard", n_storyboard)
    g.add_node("confirm_script", n_confirm_script)
    g.add_node("images", n_images)
    g.add_node("confirm_images", n_confirm_images)
    g.add_node("align", n_align)
    g.add_node("videos", n_videos)
    g.add_node("render", n_render)

    g.add_edge(START, "storyboard")
    g.add_edge("storyboard", "confirm_script")
    g.add_conditional_edges("confirm_script", _route_after_confirm,
                            {"continue": "images", END: END})
    g.add_edge("images", "confirm_images")
    g.add_conditional_edges("confirm_images", _route_after_confirm,
                            {"continue": "align", END: END})
    g.add_edge("align", "videos")
    g.add_conditional_edges("videos", _route_after_videos,
                            {"continue": "render", END: END})
    g.add_edge("render", END)
    return g.compile(checkpointer=saver)


# ---------- 驱动（CLI 调用） ----------

def run_graph(topic: str | None = None, pid: str | None = None,
              auto: bool = False, on_interrupt=None) -> dict:
    """图版端到端。on_interrupt(payload) -> resume 值，由 CLI 负责人工交互。"""
    PROJECTS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(saver)

    pid = pid or time.strftime("p%Y%m%d-%H%M%S")
    olog.set_trace(pid)
    olog.set_node("graph")
    olog.log("run_start", mode="resume" if pid else "new", engine="graph")
    config = {"configurable": {"thread_id": pid}}
    has_checkpoint = saver.get_tuple(config) is not None

    inputs = None if has_checkpoint else {"topic": topic, "pid": pid, "auto": auto}
    t0 = time.time()
    while True:
        result = graph.invoke(inputs, config)
        inputs = None
        st = graph.get_state(config)
        if not st.next:  # 图执行完毕
            break
        intr = None
        for task in st.tasks:
            if task.interrupts:
                intr = task.interrupts[0].value
                break
        if intr is None:
            break
        resume = on_interrupt(intr) if on_interrupt else "y"
        inputs = Command(resume=resume)

    final = graph.get_state(config).values
    final["minutes"] = (time.time() - t0) / 60
    return final
