"""LangGraph 共享状态定义（P3 图版编排）。

每个节点执行完，checkpointer 把本状态快照存 SQLite（projects/checkpoints.db），
进程死后用同一 thread_id（= pid）从最后完成的节点恢复。
"""

from typing import Any, TypedDict


class PipelineState(TypedDict, total=False):
    """端到端管线的共享状态。total=False：节点只写自己负责的键。"""

    # 输入
    topic: str                  # 选题（新运行）
    pid: str                    # 项目 ID = 图恢复的 thread_id
    auto: bool                  # 跳过人确认

    # 各节点产出
    outline: dict               # 大纲（含 style）
    shots: list[dict]           # 分镜表
    character_sheet: str        # 角色锚点
    images_summary: dict        # 分镜图批量结果
    align_summary: dict         # 时长对齐结果
    videos_summary: dict        # 视频批量结果
    draft: str                  # 成片路径
    duration: float             # 成片时长
    cost: float                 # 项目累计成本
    minutes: float              # 总耗时

    # 控制
    aborted: bool               # 人工中止标记（条件边路由到 END）
    error: str | None
