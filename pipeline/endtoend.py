"""端到端编排（P2）：选题一条命令到成片，含两处人工确认（确认式自动化）。

流程：大纲/分镜/锚点 →【确认 1：脚本】→ 分镜图 →【确认 2：分镜图】
      → 时长对齐 → 批量视频 → EDL 渲染。
--auto 跳过确认（低干预模式）。幂等：任何一步失败，重跑同命令只补未做部分。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import typer

from db import dao
from editing import edl as edl_mod
from editing import ffmpeg
from observability import logging as olog
from pipeline import storyboard as sb
from pipeline import videos as videos_mod

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


def _confirm_script(pid: str) -> None:
    """确认点 1：脚本确认。y 继续 / n 中止 / r 带意见重生成。"""
    script = json.loads((PROJECTS_DIR / pid / "script.json").read_text(encoding="utf-8"))
    o = script["outline"]
    print(f"\n===== 脚本确认（projects/{pid}）=====")
    print(f"标题：{o['title']}（{o['target_duration']}s · {len(o['structure'])} 段）")
    for s in script["shots"]:
        print(f"  [{s['idx']:02d}] {s['duration']}s {s['purpose']}｜{s['narration']}")
    while True:
        choice = typer.prompt("\n确认？[y 继续 / n 中止 / r 带意见重生成]",
                              default="y").strip().lower()
        if choice == "y":
            return
        if choice == "n":
            raise typer.Abort()
        if choice == "r":
            fb = typer.prompt("修改意见")
            result = sb.create_storyboard(pid=pid, feedback=fb)
            script = json.loads(
                (PROJECTS_DIR / pid / "script.json").read_text(encoding="utf-8"))
            o = script["outline"]
            print(f"\n已重生成：{o['title']}")
            for s in result["shots"]:
                print(f"  [{s['idx']:02d}] {s['duration']}s {s['purpose']}｜{s['narration']}")


def _confirm_images(pid: str) -> None:
    """确认点 2：分镜图确认。y 全通过 / 镜头号列表打回重画（可带意见）。"""
    shots_dir = PROJECTS_DIR / pid / "shots"
    print(f"\n===== 分镜图确认（{shots_dir}）=====")
    if sys.platform == "darwin":
        subprocess.run(["open", str(shots_dir)], check=False)
    while True:
        choice = typer.prompt("已打开图片目录。全部通过？[y / 镜头号如 3,7 / n 中止]",
                              default="y").strip().lower()
        if choice == "y":
            return
        if choice == "n":
            raise typer.Abort()
        try:
            indices = [int(x) for x in choice.split(",")]
        except ValueError:
            print("输入无效，请输入 y 或逗号分隔的镜头号")
            continue
        fb_map = {}
        for idx in indices:
            fb_map[idx] = typer.prompt(f"shot {idx:02d} 修改意见（留空原样重画）",
                                       default="", show_default=False)
        sb.regenerate_images(pid, fb_map)
        print("重画完成，请重新查看目录中的图片")


def run_topic(topic: str | None = None, auto: bool = False,
              pid: str | None = None, skill: str | None = None) -> dict:
    """选题 → 成片全流程。pid 用于断点续跑（跳过已完成的阶段）。
    skill：强制指定 Skill；未指定且新运行时由选择器按选题自动匹配。"""
    t0 = time.time()

    if pid:
        print(f"[{pid}] 续跑模式：复用已有大纲与分镜")
    else:
        # P6：Skill 选择（--skill 指定优先，否则 LLM 选择器按选题匹配）
        if not skill:
            from skills import selector
            choice = selector.choose_skill(topic)
            skill = choice["skill"]
            print(f"  Skill 选择：{skill or '无匹配（LLM 自决风格）'}——{choice['reason']}")
        r = sb.create_storyboard(topic, skill_name=skill)
        pid = r["pid"]
        print(f"[{pid}] 1/6 大纲+分镜+锚点完成（{len(r['shots'])} 镜"
              f"{'，Skill：' + skill if skill else ''}）")
        if not auto:
            _confirm_script(pid)
    olog.set_trace(pid)
    olog.set_node("endtoend")
    olog.log("run_start", mode="resume" if pid else "new")

    r = sb.create_images(pid)
    print(f"[{pid}] 2/6 分镜图完成（新 {r['made']} 跳 {r['skipped']}，¥{r['cost']:.2f}）")
    if not auto:
        _confirm_images(pid)

    r = sb.align_audio(pid)
    print(f"[{pid}] 3/6 时长对齐完成（合格 {r['ok']} 改写 {r['rewritten']} 音频为准 {r['align_audio']}）")

    r = videos_mod.batch_generate_videos(pid)
    print(f"[{pid}] 4/6 视频生成完成（成功 {r['succeeded']} 失败 {r['failed']}，¥{r['cost']}）")
    if r["failed"]:
        raise RuntimeError(f"镜头视频失败 {r['failed_idx']}，修复后重跑同命令即可（幂等续跑）")

    edl = edl_mod.build_edl(pid)
    out = PROJECTS_DIR / pid / "final" / "draft.mp4"
    ffmpeg.render_edl(edl, out)
    print(f"[{pid}] 5/6 渲染完成（{edl['duration']:.1f}s）")

    conn = dao.get_conn()
    cost = sum(float(g["cost"]) for g in dao.list_generations(conn, project_id=pid))
    conn.close()
    elapsed = (time.time() - t0) / 60
    print(f"[{pid}] 6/6 成片：{out}\n    总耗时 {elapsed:.1f} 分钟 · 总成本 ¥{cost:.2f}")
    return {"pid": pid, "draft": out, "duration": edl["duration"],
            "cost": cost, "minutes": elapsed}
