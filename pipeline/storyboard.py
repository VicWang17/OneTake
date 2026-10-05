"""分镜包管线（P1）：选题 → 大纲 → 分镜表 → 分镜图。

与 linear.py（P0 固定文案管线）并存；P2 端到端时两者汇合：
storyboard 产出的分镜包（script.json + shots/*.png）就是 linear 的输入源。
"""

import json
import time
import uuid
from pathlib import Path

import requests

from db import dao
from editing import ffmpeg
from gateway import core as gw
from nodes import character as character_node
from nodes import outline as outline_node
from nodes import storyboard as storyboard_node
from pipeline import brief as brief_mod
from pipeline import context as context_mod
from skills import loader

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out.write_bytes(r.content)


def create_outline(topic: str, feedback: str | None = None,
                   pid: str | None = None, skill_name: str | None = None,
                   brief: dict | None = None) -> dict:
    """1.1 大纲生成：建项目 → LLM 大纲 → script.json + 风格入库。
    skill_name 指定 Skill（P6）；None 时由调用方决定是否走选择器。
    brief：P1 CreativeBrief——硬约束注入大纲 prompt（最高优先级段）。"""
    pid = pid or time.strftime("p%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:4]}"
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)

    skill = loader.get_skill(skill_name) if skill_name else None

    # P6 记忆注入：选题相关 Top-K 记忆进大纲 prompt（无记忆时零变化）
    from memory import inject
    memory_block = inject.format_for_prompt(inject.get_relevant(topic, project_id=pid))

    conn = dao.get_conn()
    dao.create_project(conn, topic=topic, pid=pid,
                       skill_id=skill["id"] if skill else None)

    # P1 Context Builder：组装策划阶段上下文（分节带来源/版本/Token 估算），落盘可审计
    pack = context_mod.build_outline(topic, pid, brief=brief, skill=skill,
                                     memory_block=memory_block, feedback=feedback)
    context_mod.save_pack(pid, "outline", pack)

    data = outline_node.generate_outline(topic, pid, skill=skill,
                                         user_override=pack["user"])
    (pdir / "script.json").write_text(
        json.dumps({"topic": topic, "outline": data,
                    "skill": skill_name}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    dao.update_project(conn, pid, style_json=json.dumps(data["style"], ensure_ascii=False),
                       status="outlined")
    conn.close()
    return {"pid": pid, "outline": data}
    (pdir / "script.json").write_text(
        json.dumps({"topic": topic, "outline": data}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    dao.update_project(conn, pid, style_json=json.dumps(data["style"], ensure_ascii=False),
                       status="outlined")
    conn.close()
    return {"pid": pid, "outline": data}


def create_storyboard(topic: str | None = None, pid: str | None = None,
                      feedback: str | None = None,
                      skill_name: str | None = None,
                      brief: dict | None = None) -> dict:
    """1.2 分镜表：读大纲（已有 pid 或现场生成）→ LLM 分镜表（校验回灌）→ 落盘 + 入库。
    pid + feedback：脚本确认打回——删除旧分镜，带意见全量重生成（大纲/分镜/锚点）。
    skill_name：P6 指定 Skill（None 时由端到端编排层先走选择器）。
    brief：P1 CreativeBrief；None 且已有项目时自动读 brief.json（多轮约束保持）。"""
    conn = dao.get_conn()
    if brief is None and pid:
        brief = brief_mod.load_brief(pid)
    if pid and feedback:
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        topic = script["topic"]
        dao.delete_shots(conn, pid)
        # 打回重生成同样走 Context Builder（feedback 作为「当前请求」分节入包）
        pack = context_mod.build_outline(topic, pid, brief=brief, feedback=feedback)
        context_mod.save_pack(pid, "outline", pack)
        outline_data = outline_node.generate_outline(topic, pid,
                                                     user_override=pack["user"])
        script = {"topic": topic, "outline": outline_data}
    elif pid:
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        outline_data = script["outline"]
    else:
        result = create_outline(topic, feedback=feedback, pid=pid,
                                skill_name=skill_name, brief=brief)
        pid, outline_data = result["pid"], result["outline"]
        script_path = PROJECTS_DIR / pid / "script.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))

    # 分镜阶段同样走 Context Builder（brief 时长/禁止约束进分镜 prompt）
    pack = context_mod.build_storyboard(outline_data, pid, brief=brief)
    context_mod.save_pack(pid, "storyboard", pack)
    shots = storyboard_node.generate_storyboard(outline_data, pid,
                                                user_override=pack["user"])
    for s in shots:
        dao.create_shot(conn, project_id=pid, idx=s["idx"], duration=s["duration"],
                        visual_prompt=s["visual_prompt"], narration=s["narration"],
                        status="storyboarded")

    # 1.3 角色设定表：双段锚点（角色/风格分离，1.4 按 has_character 条件注入）
    anchors = character_node.generate_character_sheet(outline_data, shots, pid)
    script.update(anchors)

    script["shots"] = shots
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    dao.update_project(conn, pid, status="storyboarded")
    conn.close()
    return {"pid": pid, "shots": shots, **anchors}


def build_image_prompt(script: dict, shot: dict, brief: dict | None = None) -> str:
    """出图 prompt 构造（唯一注入点，DEVLOG 016 修复）。

    规则：风格锚恒注入；角色锚仅在 shot.has_character=True 时注入。
    老包兼容：无拆分锚点字段时，旧 character_sheet 整体恒注入（行为同修复前）。
    brief：P1 禁止内容以负面约束追加（「画面严格避免：…」）。
    """
    style = script.get("style_anchor", "")
    char = script.get("character_anchor", "")
    parts = []
    if style:
        parts.append(style)
    if char and shot.get("has_character"):
        parts.append(char)
    if not style and not char and script.get("character_sheet"):
        parts.append(script["character_sheet"])
    parts.append(shot["visual_prompt"])
    forbidden = brief_mod.hard_constraints(brief)["forbidden"] if brief else []
    if forbidden:
        parts.append("画面严格避免：" + "、".join(forbidden))
    return "，".join(parts)


def create_images(pid: str) -> dict:
    """1.4 分镜图批量产出：角色锚点拼入 prompt + 参考图链（首镜图 → 后续镜头）。

    P3 起幂等收敛到网关：不再按文件存在跳过，每次调用都过网关缓存——
    内容相同 → cache_hit 零成本；prompt 变化 → 新 idem_key 自动重生成。
    参考图改用本地首图的 base64（内容稳定、不受 URL 过期影响），保证 key 跨运行一致。
    """
    pdir = PROJECTS_DIR / pid
    script_path = pdir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    shots_dir = pdir / "shots"
    shots_dir.mkdir(exist_ok=True)

    conn = dao.get_conn()
    brief = brief_mod.load_brief(pid)  # P1：禁止内容等约束进出图 prompt
    first_img = shots_dir / "shot_01.png"
    made, hits, cost = 0, 0, 0.0
    for s in script["shots"]:
        idx = int(s["idx"])
        img = shots_dir / f"shot_{idx:02d}.png"
        prompt = build_image_prompt(script, s, brief)
        # 单镜生成阶段：出图 prompt 组成审计（这张图为什么长这样）
        context_mod.save_pack(pid, "shot_image",
                              context_mod.build_shot_image(script, s, brief, prompt, pid),
                              name=f"shot{idx:02d}")
        payload: dict = {"prompt": prompt, "out_path": str(img)}
        if idx > 1 and first_img.exists():  # 参考图链：本地首图 base64（内容寻址稳定）
            payload["reference_url"] = _data_url(first_img)
        r = gw.call("image", payload, project_id=pid)
        if r.get("cached"):
            hits += 1
        else:
            _download(r["url"], img)
            made += 1
            cost += r["cost"]
            print(f"    shot {idx:02d} 分镜图 ¥{r['cost']:.2f}"
                  f"{'（含参考图）' if 'reference_url' in payload else ''}")
        dao.update_shot(conn, f"{pid}-s{idx:02d}", status="imaged")
    dao.update_project(conn, pid, status="imaged")
    conn.close()
    return {"pid": pid, "made": made, "skipped": hits, "cost": cost}


def _data_url(img: Path) -> str:
    import base64
    b64 = base64.b64encode(img.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def regenerate_images(pid: str, feedback_map: dict[int, str]) -> dict:
    """分镜图确认打回：按镜头号重画（可带修改意见），同时作废对应旧视频。"""
    pdir = PROJECTS_DIR / pid
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    shots = {int(s["idx"]): s for s in script["shots"]}

    conn = dao.get_conn()
    brief = brief_mod.load_brief(pid)
    cost = 0.0
    for idx, fb in sorted(feedback_map.items()):
        s = shots[idx]
        prompt = build_image_prompt(script, s, brief)
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
            # P3 起每次过网关缓存：文本未变 → cache_hit 零成本；改写后新文本 → 真实合成
            gw.call("tts", {"text": text, "out_path": str(audio)}, project_id=pid)
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
