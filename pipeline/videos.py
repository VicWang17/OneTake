"""视频批量生成（P4 调度器版）：分镜图 + motion_prompt → 全部镜头视频。

P4 起生产者-消费者分离：本模块只负责把任务登记进 jobs 表（enqueue），
执行由 scheduler/worker 的 worker 池完成（按 provider 限流、重试、死信、崩溃回收）。
重试语义上移到调度器（jobs.retry_count），单任务函数不再自带重试。
"""

import base64
import json
import time
from pathlib import Path

from db import dao
from editing import ffmpeg
from gateway import adapters
from gateway import core as gw
from nodes import motion as motion_node
from scheduler import handlers, queue, worker  # noqa: F401（handlers 注册用）

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


def _clip_valid(conn, out: Path, payload: dict, model: str) -> bool:
    """复用判定（P0 失效检查）：存在且非空 + ffprobe 完好 + 输入指纹有成功生成记录。

    三者缺一即失效重做：缺失/空文件、截断损坏（fr-04）、上游输入变化
    （分镜图重画/motion_prompt 变更 → 指纹变化 → 无匹配记录）。
    """
    if not out.exists() or out.stat().st_size == 0:
        return False
    try:
        ffmpeg.probe_duration(out)  # 截断/损坏文件在此现形
    except Exception:
        return False
    key = gw.idem_key_for("video", model, payload)
    return dao.find_generation_by_idem(conn, key) is not None


def _data_url(img: Path) -> str:
    b64 = base64.b64encode(img.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def batch_generate_videos(pid: str, only_shots: list[int] | None = None,
                          model: str | None = None) -> dict:
    """批量生成分镜视频（经任务调度器）。only_shots 用于失败镜头单独重跑。"""
    model = model or adapters.SEEDANCE_FAST_MODEL
    pdir = PROJECTS_DIR / pid
    script_path = pdir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    clips_dir = pdir / "clips"
    clips_dir.mkdir(exist_ok=True)

    worker.register_handler("video_gen", handlers.handle_video_gen)

    conn = dao.get_conn()
    job_shot: dict[str, int] = {}
    for s in script["shots"]:
        idx = int(s["idx"])
        if only_shots and idx not in only_shots:
            continue
        out = clips_dir / f"shot_{idx:02d}_src.mp4"
        img = pdir / "shots" / f"shot_{idx:02d}.png"
        if not img.exists():
            raise FileNotFoundError(f"缺分镜图: {img}（先跑 images --pid {pid}）")
        if not s.get("motion_prompt"):  # 缓存：重跑不重复付 LLM 费
            s["motion_prompt"] = motion_node.generate_motion_prompt(s, pid)
        payload = {
            "pid": pid, "idx": idx,
            "motion_prompt": s["motion_prompt"],
            "out_path": str(out),
            "first_frame_url": _data_url(img),
            "model": model, "seconds": 5, "resolution": "480p",
        }
        video_payload = {  # 与 handler 调网关时同形的语义参数（pid/idx 不参与）
            "prompt": payload["motion_prompt"], "out_path": payload["out_path"],
            "model": model, "seconds": 5, "resolution": "480p",
            "first_frame_url": payload["first_frame_url"],
        }
        if _clip_valid(conn, out, video_payload, model):  # 产物有效才跳过（P0 失效判断）
            continue
        if out.exists():
            out.unlink()  # 失效产物让位，防 handler 写文件时与旧内容混淆
        job_id = queue.enqueue(conn, "video_gen", payload)
        job_shot[job_id] = idx

    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if not job_shot:
        conn.close()
        return {"pid": pid, "succeeded": 0, "failed": 0, "cost": 0.0,
                "note": "全部已存在，跳过"}

    print(f"[{pid}] 提交 {len(job_shot)} 个视频任务到调度器（{model}）...")
    cost_before = sum(float(g["cost"]) for g in
                      dao.list_generations(conn, project_id=pid)
                      if g["task_type"] == "video" and g["status"] == "succeeded")
    conn.close()
    worker.run_workers(stop_when_empty=True)  # worker 池执行（含孤儿回收/限流/重试）

    # 汇总本轮任务结果
    conn = dao.get_conn()
    ok, failed = [], []
    for job_id, idx in job_shot.items():
        row = [j for j in queue.list_jobs(conn) if j["id"] == job_id][0]
        (ok if row["status"] == "succeeded" else failed).append((idx, row["status"]))
        dao.update_shot(conn, f"{pid}-s{idx:02d}",
                        status="videoed" if row["status"] == "succeeded" else "video_failed")
    if not failed:
        dao.update_project(conn, pid, status="videoed")
    cost_after = sum(float(g["cost"]) for g in dao.list_generations(conn, project_id=pid)
                     if g["task_type"] == "video" and g["status"] == "succeeded")
    conn.close()
    return {"pid": pid, "succeeded": len(ok), "failed": len(failed),
            "failed_idx": [i for i, _ in failed], "cost": round(cost_after - cost_before, 2)}
