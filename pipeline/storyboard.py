"""分镜包管线（P1）：选题 → 大纲 → 分镜表 → 分镜图。

与 linear.py（P0 固定文案管线）并存；P2 端到端时两者汇合：
storyboard 产出的分镜包（script.json + shots/*.png）就是 linear 的输入源。
"""

import json
import time
from pathlib import Path

import requests

from db import dao
from editing import ffmpeg
from gateway import core as gw
from nodes import character as character_node
from nodes import outline as outline_node
from nodes import storyboard as storyboard_node

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out.write_bytes(r.content)


def create_outline(topic: str, feedback: str | None = None) -> dict:
    """1.1 大纲生成：建项目 → LLM 大纲 → script.json + 风格入库。"""
    pid = time.strftime("p%Y%m%d-%H%M%S")
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)

    conn = dao.get_conn()
    dao.create_project(conn, topic=topic, pid=pid)

    data = outline_node.generate_outline(topic, pid, feedback=feedback)
    (pdir / "script.json").write_text(
        json.dumps({"topic": topic, "outline": data}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    dao.update_project(conn, pid, style_json=json.dumps(data["style"], ensure_ascii=False),
                       status="outlined")
    conn.close()
    return {"pid": pid, "outline": data}


def create_storyboard(topic: str | None = None, pid: str | None = None,
                      feedback: str | None = None) -> dict:
    """1.2 分镜表：读大纲（已有 pid 或现场生成）→ LLM 分镜表（校验回灌）→ 落盘 + 入库。
    pid + feedback：脚本确认打回——删除旧分镜，带意见全量重生成（大纲/分镜/锚点）。"""
    conn = dao.get_conn()
    if pid and feedback:
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        topic = script["topic"]
        dao.delete_shots(conn, pid)
        outline_data = outline_node.generate_outline(topic, pid, feedback=feedback)
        script = {"topic": topic, "outline": outline_data}
    elif pid:
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        outline_data = script["outline"]
    else:
        result = create_outline(topic, feedback=feedback)
        pid, outline_data = result["pid"], result["outline"]
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))

    shots = storyboard_node.generate_storyboard(outline_data, pid)
    for s in shots:
        dao.create_shot(conn, project_id=pid, idx=s["idx"], duration=s["duration"],
                        visual_prompt=s["visual_prompt"], narration=s["narration"],
                        status="storyboarded")

    # 1.3 角色设定表：项目级文本锚点，1.4 出图时拼入每个 visual_prompt
    sheet = character_node.generate_character_sheet(outline_data, shots, pid)
    script["character_sheet"] = sheet

    script["shots"] = shots
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    dao.update_project(conn, pid, status="storyboarded")
    conn.close()
    return {"pid": pid, "shots": shots, "character_sheet": sheet}


def create_images(pid: str) -> dict:
    """1.4 分镜图批量产出：角色锚点拼入 prompt + 参考图链（首镜图 → 后续镜头）。

    幂等：已存在的图跳过。注意参考图 URL 有时效（约 24h），若首镜图为存量
    （非本次新生成），参考图链不可用，后续镜头退化为仅文本锚点。
    """
    pdir = PROJECTS_DIR / pid
    script_path = pdir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    sheet = script.get("character_sheet", "")
    shots_dir = pdir / "shots"
    shots_dir.mkdir(exist_ok=True)

    conn = dao.get_conn()
    ref_url: str | None = None
    made, skipped, cost = 0, 0, 0.0
    for s in script["shots"]:
        idx = int(s["idx"])
        img = shots_dir / f"shot_{idx:02d}.png"
        prompt = f"{sheet}，{s['visual_prompt']}" if sheet else s["visual_prompt"]
        if img.exists():
            skipped += 1
        else:
            r = gw.call("image", {"prompt": prompt, "reference_url": ref_url},
                        project_id=pid)
            _download(r["url"], img)
            cost += r["cost"]
            made += 1
            if idx == 1:
                ref_url = r["url"]  # 参考图链：首镜新图作为后续镜头的图像锚点
            print(f"    shot {idx:02d} 分镜图 ¥{r['cost']:.2f}"
                  f"{'（含参考图）' if ref_url and idx > 1 else ''}")
        dao.update_shot(conn, f"{pid}-s{idx:02d}", status="imaged")
    dao.update_project(conn, pid, status="imaged")
    conn.close()
    return {"pid": pid, "made": made, "skipped": skipped, "cost": cost}


def regenerate_images(pid: str, feedback_map: dict[int, str]) -> dict:
    """分镜图确认打回：按镜头号重画（可带修改意见），同时作废对应旧视频。"""
    pdir = PROJECTS_DIR / pid
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    sheet = script.get("character_sheet", "")
    shots = {int(s["idx"]): s for s in script["shots"]}

    conn = dao.get_conn()
    cost = 0.0
    for idx, fb in sorted(feedback_map.items()):
        s = shots[idx]
        prompt = f"{sheet}，{s['visual_prompt']}"
        if fb:
            prompt += f"。修改意见（请采纳）：{fb}"
        r = gw.call("image", {"prompt": prompt}, project_id=pid)
        _download(r["url"], pdir / "shots" / f"shot_{idx:02d}.png")
        cost += r["cost"]
        stale = pdir / "clips" / f"shot_{idx:02d}_src.mp4"  # 旧视频作废，批量时重生成
        stale.unlink(missing_ok=True)
        dao.update_shot(conn, f"{pid}-s{idx:02d}", status="imaged")
        print(f"    shot {idx:02d} 重画 ¥{r['cost']:.2f}{'（带意见）' if fb else ''}")
    conn.close()
    return {"pid": pid, "regenerated": len(feedback_map), "cost": cost}


TOLERANCE = 0.2  # 台词时长容忍带 ±20%，以内由画面侧吸收，超出才动台词


def align_audio(pid: str) -> dict:
    """1.5 台词时长对齐：TTS 真实合成 → 实测时长回写 → 超 ±20% 改写（≤2 次）。

    原则：时间轴的唯一事实源是音频。shots.duration 从 LLM 预估值覆写为
    TTS 实测值；改写 ≤2 次仍越界的，标记 align=audio，P2 EDL 按音频排轴。
    """
    pdir = PROJECTS_DIR / pid
    script_path = pdir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    audio_dir = pdir / "audio"
    audio_dir.mkdir(exist_ok=True)

    conn = dao.get_conn()
    rewritten, aligned_audio, ok = 0, 0, 0
    for s in script["shots"]:
        idx = int(s["idx"])
        target = float(s["duration"])
        lo, hi = target * (1 - TOLERANCE), target * (1 + TOLERANCE)
        text, audio = s["narration"], audio_dir / f"shot_{idx:02d}.mp3"

        for attempt in range(3):  # 原始 + 至多 2 次改写
            if attempt > 0 or not audio.exists():
                gw.call("tts", {"text": text, "out_path": audio}, project_id=pid)
            actual = ffmpeg.probe_duration(audio)
            if lo <= actual <= hi:
                align = "ok"
                ok += 1
                break
            if attempt < 2:
                text = storyboard_node.rewrite_narration(text, actual, lo, hi, pid)
                rewritten += 1
        else:
            align = "audio"  # 改写仍越界：以音频为准
            aligned_audio += 1

        if text != s["narration"]:
            print(f"    shot {idx:02d} 台词改写：{s['narration'][:15]}… → {text[:15]}…")
        s["narration"], s["duration"], s["align"] = text, actual, align
        dao.update_shot(conn, f"{pid}-s{idx:02d}", duration=actual,
                        narration=text, status="aligned")

    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    dao.update_project(conn, pid, status="aligned")
    conn.close()
    return {"pid": pid, "ok": ok, "rewritten": rewritten, "align_audio": aligned_audio}
