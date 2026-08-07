"""OneTake CLI 入口（typer）。

用法：
  uv run python main.py run --script examples/demo.txt   # 固定文案 → 草稿片
  uv run python main.py report [--project PID]           # 成本报表
"""

from pathlib import Path

import typer

from db import dao
from editing import edl as edl_mod
from editing import ffmpeg
from pipeline import endtoend, linear
from pipeline import graph as graph_mod
from pipeline import storyboard as storyboard_mod
from pipeline import videos as videos_mod

ROOT = Path(__file__).resolve().parent

app = typer.Typer(help="OneTake · 端到端 AI 视频创作 Agent（P0 最小管线）")


@app.command()
def run(
    script: Path = typer.Option(None, "--script", exists=True, help="固定文案路径（P0 线性管线）"),
    topic: str = typer.Option(None, "--topic", help="选题（P2 端到端管线）"),
    pid: str = typer.Option(None, "--pid", help="断点续跑已有项目"),
    auto: bool = typer.Option(False, "--auto", help="跳过两处人工确认，全自动"),
    graph: bool = typer.Option(False, "--graph", help="P3 图版编排（LangGraph + checkpointer）"),
    video_shots: int = typer.Option(0, "--video-shots",
                                    help="P0 管线专用：前 N 个镜头用真实视频生成"),
):
    """一条命令出片：--topic 选题端到端（推荐）；--script 固定文案（P0 路径）；--pid 续跑。"""
    if topic or pid:
        if graph:
            result = graph_mod.run_graph(topic=topic, pid=pid, auto=auto,
                                         on_interrupt=_cli_interrupt)
            if result.get("aborted"):
                typer.echo("已中止。")
                return
            if result.get("error"):
                typer.echo(f"中断：{result['error']}")
                raise typer.Exit(code=1)
            typer.echo(f"\n完成（图版）：{result['draft']}\n"
                       f"  时长 {result['duration']:.1f}s · "
                       f"耗时 {result['minutes']:.1f} 分钟 · 成本 ¥{result['cost']:.2f}")
            return
        result = endtoend.run_topic(topic, auto=auto, pid=pid)
        typer.echo(f"\n完成：{result['draft']}\n"
                   f"  时长 {result['duration']:.1f}s · 耗时 {result['minutes']:.1f} 分钟 · "
                   f"成本 ¥{result['cost']:.2f}")
        return
    if script:
        result = linear.run(script, n_video_shots=video_shots)
        typer.echo(
            f"\n完成：{result['draft']}\n"
            f"  时长 {result['duration']:.1f}s · {result['shots']} 个分镜 · "
            f"本次生成成本约 ¥{result['cost']:.2f}（明细见 report）"
        )
        return
    raise typer.BadParameter("请提供 --topic、--pid 或 --script")


def _cli_interrupt(payload: dict):
    """图版人工确认的 CLI 交互（interrupt 的 resume 值生产者）。"""
    import json as _json
    import subprocess as _sp
    import sys as _sys

    pid = payload["pid"]
    if payload["kind"] == "confirm_script":
        s = _json.loads((ROOT / "projects" / pid / "script.json").read_text(encoding="utf-8"))
        o = s["outline"]
        typer.echo(f"\n===== [图] 脚本确认（{pid}）=====")
        typer.echo(f"标题：{o['title']}（{o['target_duration']}s · {len(o['structure'])} 段）")
        for sh in s["shots"]:
            typer.echo(f"  [{sh['idx']:02d}] {sh['duration']}s {sh['purpose']}｜{sh['narration']}")
        while True:
            c = typer.prompt("确认？[y 继续 / n 中止 / r 带意见重生成]", default="y").strip().lower()
            if c == "y":
                return "y"
            if c == "n":
                return "n"
            if c == "r":
                return {"feedback": typer.prompt("修改意见")}
    if payload["kind"] == "confirm_images":
        shots_dir = ROOT / "projects" / pid / "shots"
        typer.echo(f"\n===== [图] 分镜图确认（{shots_dir}）=====")
        if _sys.platform == "darwin":
            _sp.run(["open", str(shots_dir)], check=False)
        while True:
            c = typer.prompt("全部通过？[y / 镜头号如 3,7 / n 中止]", default="y").strip().lower()
            if c == "y":
                return "y"
            if c == "n":
                return "n"
            try:
                indices = [int(x) for x in c.split(",")]
            except ValueError:
                typer.echo("输入无效，请输入 y 或逗号分隔的镜头号")
                continue
            redo = {}
            for idx in indices:
                redo[idx] = typer.prompt(f"shot {idx:02d} 修改意见（留空原样重画）",
                                         default="", show_default=False)
            return {"redo": redo}
    return "y"


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
    hits = 0
    for r in rows:
        if r["status"] == "cache_hit":
            hits += 1
            continue
        by_type.setdefault((r["task_type"], r["model"]), []).append(r)
    total = 0.0
    for (tt, model), rs in sorted(by_type.items()):
        cost = sum(float(r["cost"]) for r in rs)
        total += cost
        typer.echo(f"{tt:<8}{model:<34}{len(rs):>4} {cost:>10.4f}")
    typer.echo(f"{'合计':<44}{len(rows) - hits:>4} {total:>10.4f}")
    if rows:
        typer.echo(f"缓存命中 {hits} 次 / 总调用 {len(rows)} 次，命中率 {hits / len(rows) * 100:.1f}%")

    spent = dao.today_spend(conn)
    typer.echo(f"\n今日已花费 ¥{spent:.2f} / 日熔断上限 ¥{gw_limit():.2f}")
    conn.close()


def gw_limit() -> float:
    from gateway.core import DAILY_BUDGET_LIMIT
    return DAILY_BUDGET_LIMIT


@app.command()
def outline(topic: str = typer.Option(..., "--topic", help="选题（一句话）")):
    """P1：选题 → 大纲（含 LLM 自选风格）→ projects/{pid}/script.json。"""
    result = storyboard_mod.create_outline(topic)
    o = result["outline"]
    typer.echo(f"\n大纲已生成：projects/{result['pid']}/script.json")
    typer.echo(f"  标题：{o['title']}（{o['target_duration']}s · {len(o['structure'])} 段）")
    typer.echo(f"  风格：{o['style'].get('tone', '')[:40]}")
    typer.echo(f"        {o['style'].get('visual', '')[:40]}")


@app.command()
def storyboard(
    topic: str | None = typer.Option(None, "--topic", help="选题（从大纲开始跑）"),
    pid: str | None = typer.Option(None, "--pid", help="复用已有大纲的项目 ID"),
):
    """P1：大纲 → 分镜表（JSON Schema 校验 + 错误回灌）→ script.json 追加 shots。"""
    if not topic and not pid:
        raise typer.BadParameter("--topic 与 --pid 至少提供一个")
    result = storyboard_mod.create_storyboard(topic=topic, pid=pid)
    shots = result["shots"]
    total = sum(s["duration"] for s in shots)
    typer.echo(f"\n分镜表已生成：projects/{result['pid']}/script.json")
    typer.echo(f"  {len(shots)} 个镜头 · 总时长 {total}s")
    if sheet := result.get("character_sheet"):
        typer.echo(f"  角色锚点：{sheet[:50]}…")
    for s in shots:
        typer.echo(f"  [{s['idx']:02d}] {s['duration']}s {s['purpose']}（{s['camera']}）{s['narration'][:20]}…")


@app.command()
def images(pid: str = typer.Option(..., "--pid", help="项目 ID")):
    """P1：分镜图批量产出（角色锚点注入 + 参考图链，已产出的图自动跳过）。"""
    result = storyboard_mod.create_images(pid)
    typer.echo(f"\n分镜图完成：projects/{pid}/shots/ · 新生成 {result['made']} 张 · "
               f"跳过 {result['skipped']} 张 · 成本 ¥{result['cost']:.2f}")


@app.command()
def align(pid: str = typer.Option(..., "--pid", help="项目 ID")):
    """P1：台词时长对齐（TTS 实测回写，超 ±20% 自动改写 ≤2 次）。"""
    result = storyboard_mod.align_audio(pid)
    typer.echo(f"\n时长对齐完成：projects/{pid} · 直接合格 {result['ok']} · "
               f"触发改写 {result['rewritten']} · 以音频为准 {result['align_audio']}")


@app.command()
def videos(
    pid: str = typer.Option(..., "--pid", help="项目 ID"),
    shots: str | None = typer.Option(None, "--shots", help="只重跑指定镜头，如 3,7"),
):
    """P2：批量生成分镜视频（并发 ≤5，重试 ≤3，已有跳过，失败可单独重跑）。"""
    only = [int(x) for x in shots.split(",")] if shots else None
    result = videos_mod.batch_generate_videos(pid, only_shots=only)
    typer.echo(f"\n视频批量完成：projects/{pid}/clips/ · 成功 {result['succeeded']} · "
               f"失败 {result['failed']} · 成本 ¥{result['cost']}")
    if result.get("failed_idx"):
        typer.echo(f"  失败镜头：{result['failed_idx']}，可用 --shots 单独重跑")


@app.command()
def render(pid: str = typer.Option(..., "--pid", help="项目 ID")):
    """P2：EDL 时间线生成 + 渲染成片（粒度对齐 + 硬字幕 + BGM 人声闪避）。"""
    edl = edl_mod.build_edl(pid)
    out = ROOT / "projects" / pid / "final" / "draft.mp4"
    ffmpeg.render_edl(edl, out)
    typer.echo(f"\n成片完成：{out}")
    typer.echo(f"  时长 {edl['duration']:.1f}s · {len(edl['tracks']['video'])} 个镜头 · "
               f"BGM {'有（闪避）' if edl['tracks'].get('bgm') else '无'}")


if __name__ == "__main__":
    app()
