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
    project: str | None = typer.Option(None, "--project", "-p", help="单项目成本明细"),
):
    """成本报表（3.3 完整版）：默认项目总览 + 全局环节/档位切片；-p 单条成本明细。"""
    conn = dao.get_conn()
    rows = dao.list_generations(conn, project_id=project)
    real = [r for r in rows if r["status"] != "cache_hit"]
    hits = [r for r in rows if r["status"] == "cache_hit"]

    if project:
        _report_project(conn, project, real, hits)
    else:
        _report_global(conn, real, hits)

    spent = dao.today_spend(conn)
    typer.echo(f"\n今日已花费 ¥{spent:.2f} / 日熔断上限 ¥{gw_limit():.2f}")
    conn.close()


def _savings_estimate(real: list, hits: list) -> float:
    """缓存节省估算：命中次数 × 同（环节×模型）真实调用的平均成本。"""
    avg: dict[tuple[str, str], float] = {}
    groups: dict[tuple[str, str], list] = {}
    for r in real:
        groups.setdefault((r["task_type"], r["model"]), []).append(float(r["cost"]))
    for k, v in groups.items():
        avg[k] = sum(v) / len(v)
    return sum(avg.get((h["task_type"], h["model"]), 0.0) for h in hits)


def _report_project(conn, pid: str, real: list, hits: list) -> None:
    """单项目明细：按环节成本与占比 + 单条成本对标 + 命中率。"""
    proj = dao.get_project(conn, pid)
    n_shots = len(dao.list_shots(conn, pid))
    total = sum(float(r["cost"]) for r in real)
    typer.echo(f"== 单条成本报表 · 项目 {pid} ==")
    if proj:
        typer.echo(f"选题：{proj['topic'][:40]} · 状态：{proj['status']} · {n_shots} 个镜头")
    by_type: dict[str, list] = {}
    for r in real:
        by_type.setdefault(r["task_type"], []).append(r)
    typer.echo(f"\n{'环节':<8}{'次数':>4} {'成本(¥)':>10} {'占比':>8}")
    for tt, rs in sorted(by_type.items(), key=lambda x: -sum(float(r['cost']) for r in x[1])):
        cost = sum(float(r["cost"]) for r in rs)
        pct = cost / total * 100 if total else 0
        typer.echo(f"{tt:<8}{len(rs):>4} {cost:>10.4f} {pct:>7.1f}%")
    typer.echo(f"{'合计':<12}{len(real):>4} {total:>10.4f}")
    if n_shots:
        typer.echo(f"\n单条成片成本：¥{total:.2f}（{n_shots} 镜，单镜 ¥{total / n_shots:.2f}）"
                   f"（草稿档目标 ≤¥8：{'✅' if total <= 8 else '❌'}）")
    if hits:
        typer.echo(f"缓存命中 {len(hits)} 次，等效节省约 ¥{_savings_estimate(real, hits):.2f}（估算）")


def _report_global(conn, real: list, hits: list) -> None:
    """全局：项目总览 + 环节×模型 + 档位拆分 + 命中率。"""
    typer.echo("== OneTake 成本报表 · 项目总览 ==")
    typer.echo(f"{'项目':<20}{'镜头':>4} {'总成本':>9} {'单镜':>7}  状态")
    for p in dao.list_projects(conn):
        prows = [r for r in real if r["project_id"] == p["id"]]
        cost = sum(float(r["cost"]) for r in prows)
        n = len(dao.list_shots(conn, p["id"]))
        per = cost / n if n else 0
        typer.echo(f"{p['id']:<20}{n:>4} {cost:>9.2f} {per:>7.2f}  {p['status']}")

    typer.echo("\n== 按环节 × 模型 ==")
    typer.echo(f"{'环节':<8}{'模型':<34}{'次数':>4} {'成本(¥)':>10}")
    by_type: dict[tuple[str, str], list] = {}
    for r in real:
        by_type.setdefault((r["task_type"], r["model"]), []).append(r)
    total = 0.0
    for (tt, model), rs in sorted(by_type.items()):
        cost = sum(float(r["cost"]) for r in rs)
        total += cost
        typer.echo(f"{tt:<8}{model:<34}{len(rs):>4} {cost:>10.4f}")
    typer.echo(f"{'合计':<44}{len(real):>4} {total:>10.4f}")

    typer.echo("\n== 按档位 ==")
    by_tier: dict[str, list] = {}
    for r in real:
        by_tier.setdefault(r["tier"], []).append(r)
    for tier, rs in sorted(by_tier.items()):
        cost = sum(float(r["cost"]) for r in rs)
        typer.echo(f"{tier:<10}{len(rs):>4} 次  ¥{cost:.2f}")

    if real or hits:
        rate = len(hits) / (len(real) + len(hits)) * 100
        typer.echo(f"\n缓存命中 {len(hits)} 次 / 总调用 {len(real) + len(hits)} 次，命中率 {rate:.1f}%"
                   f"（等效节省约 ¥{_savings_estimate(real, hits):.2f}）")


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


@app.command()
def jobs(
    action: str = typer.Argument("list", help="list / stats / retry <job_id>"),
    job_id: str | None = typer.Argument(None),
):
    """P4 任务调度器运维：jobs list [status] / jobs stats / jobs retry <id>（死信重放）。"""
    from scheduler import queue as q
    conn = dao.get_conn()
    if action == "stats":
        s = q.stats(conn)
        typer.echo(f"任务统计：{s}")
        total = sum(s.values())
        done = s.get("succeeded", 0)
        if total:
            typer.echo(f"成功率（含重试）：{done / total * 100:.1f}%")
    elif action == "retry":
        if not job_id:
            raise typer.BadParameter("retry 需要 job_id")
        typer.echo("重放成功" if q.retry_job(conn, job_id) else "未找到 dead 状态的任务")
    else:
        rows = q.list_jobs(conn, status=job_id)  # job_id 位置复用为 status 过滤
        typer.echo(f"{'id':<14}{'type':<12}{'status':<10}{'retry':>5}  created_at")
        for j in rows[-20:]:
            typer.echo(f"{j['id']:<14}{j['type']:<12}{j['status']:<10}"
                       f"{j['retry_count']:>5}  {j['created_at']}")
    conn.close()


@app.command()
def stats():
    """P5 系统仪表盘：成本/调用/模型表现/队列/告警，一屏看全。"""
    from observability import stats as stats_mod
    typer.echo(stats_mod.render(stats_mod.collect()))


if __name__ == "__main__":
    app()
