"""OneTake CLI 入口（typer）。

用法：
  uv run python main.py run --script examples/demo.txt   # 固定文案 → 草稿片
  uv run python main.py report [--project PID]           # 成本报表
"""

from pathlib import Path

import typer

from db import dao
from pipeline import linear

app = typer.Typer(help="OneTake · 端到端 AI 视频创作 Agent（P0 最小管线）")


@app.command()
def run(
    script: Path = typer.Option(..., "--script", exists=True, help="固定文案路径"),
    video_shots: int = typer.Option(1, "--video-shots",
                                    help="前 N 个镜头用真实视频生成，其余图片填充（控成本）"),
):
    """一条命令：固定文案 → projects/{pid}/final/draft.mp4。"""
    result = linear.run(script, n_video_shots=video_shots)
    typer.echo(
        f"\n完成：{result['draft']}\n"
        f"  时长 {result['duration']:.1f}s · {result['shots']} 个分镜 · "
        f"本次生成成本约 ¥{result['cost']:.2f}（明细见 report）"
    )


@app.command()
def report(
    project: str | None = typer.Option(None, "--project", "-p", help="只看某个项目"),
):
    """成本报表：按环节（task_type）汇总次数与花费，含当日预算水位。"""
    conn = dao.get_conn()
    rows = dao.list_generations(conn, project_id=project)
    title = f"项目 {project}" if project else "全部调用"
    typer.echo(f"== OneTake 成本报表（{title}） ==")
    typer.echo(f"{'环节':<8}{'模型':<34}{'次数':>4} {'成本(¥)':>10}")
    by_type: dict[tuple[str, str], list] = {}
    for r in rows:
        by_type.setdefault((r["task_type"], r["model"]), []).append(r)
    total = 0.0
    for (tt, model), rs in sorted(by_type.items()):
        cost = sum(float(r["cost"]) for r in rs)
        total += cost
        typer.echo(f"{tt:<8}{model:<34}{len(rs):>4} {cost:>10.4f}")
    typer.echo(f"{'合计':<44}{len(rows):>4} {total:>10.4f}")

    spent = dao.today_spend(conn)
    typer.echo(f"\n今日已花费 ¥{spent:.2f} / 日熔断上限 ¥{gw_limit():.2f}")
    conn.close()


def gw_limit() -> float:
    from gateway.core import DAILY_BUDGET_LIMIT
    return DAILY_BUDGET_LIMIT


if __name__ == "__main__":
    app()
