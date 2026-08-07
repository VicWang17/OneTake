"""任务处理器（P4）：每种 job 类型的执行逻辑。

video_gen：图生视频单镜头。支持崩溃续查——提交后把 vendor task_id 回写 jobs.payload，
worker 崩溃重跑时凭 task_id 直接续查下载，不重复提交（不重复扣费）；
若续查发现 vendor 任务失败，清除 task_id 重新提交。
"""

from pathlib import Path

from gateway import core as gw
from scheduler import queue


def handle_video_gen(conn, job_id: str, payload: dict) -> None:
    def save_task_id(tid: str) -> None:
        queue.update_payload(conn, job_id, {"task_id": tid})

    try:
        r = gw.call("video", {
            "prompt": payload["motion_prompt"],
            "out_path": payload["out_path"],
            "model": payload.get("model"),
            "seconds": payload.get("seconds", 5),
            "resolution": payload.get("resolution", "480p"),
            "first_frame_url": payload.get("first_frame_url"),
            "resume_task_id": payload.get("task_id"),      # 崩溃续查
            "on_task_created": save_task_id,               # 提交即回写（崩溃可恢复）
        })
    except RuntimeError as e:
        if payload.get("task_id") and "失败" in str(e):
            # vendor 侧任务已失败：清除旧 task_id，下次重试重新提交
            queue.update_payload(conn, job_id, {"task_id": None})
        raise
