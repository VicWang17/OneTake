"""线性版管线（P0–P2 主路径）：固定文案 → 分镜 → 素材 → 配音 → 合成草稿片。

刻意保持最笨的线性结构：无 LangGraph、无调度器、无缓存表（文件存在即跳过
已是最小幂等）。所有外部模型调用经 gateway.core.call()。

成本护栏：默认只为前 n_video_shots 个镜头生成真实视频（Seedance 按 token
计费较贵），其余镜头用分镜图 Ken Burns 推近填充——这是 P0 在 ≤¥30 预算内
产出 60s 草稿片的关键取舍。
"""

import json
import time
from pathlib import Path

import requests

from db import dao
from editing import ffmpeg
from gateway import core as gw

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
BGM_DIR = ROOT / "assets" / "bgm"

SPLIT_SYSTEM = """你是短视频分镜师。把用户给的解说文案切分为 8-10 个分镜，输出严格 JSON：
{"shots": [{"idx": 1, "narration": "该镜头台词", "visual_prompt": "该镜头画面描述"}]}
要求：
1. narration 拼接起来必须等于原文，一字不改，只切分；
2. visual_prompt 用中文，描述扁平插画风/物件/风景类画面，避免人物大场景；
3. 每个镜头台词 15-40 字；
4. 只输出 JSON，不要任何其他文字。"""


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out.write_bytes(r.content)


def _pick_bgm() -> Path | None:
    if not BGM_DIR.exists():
        return None
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        files = sorted(BGM_DIR.glob(ext))
        if files:
            return files[0]
    return None


def run(script_path: Path, n_video_shots: int = 1) -> dict:
    text = Path(script_path).read_text(encoding="utf-8").strip()
    pid = time.strftime("p%Y%m%d-%H%M%S")
    pdir = PROJECTS_DIR / pid
    dirs = {k: pdir / k for k in ("shots", "audio", "clips", "final")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    conn = dao.get_conn()
    dao.create_project(conn, topic=text[:50], pid=pid)

    # 1. LLM 切分镜
    print(f"[{pid}] 1/4 DeepSeek 切分镜 ...")
    r = gw.call("llm", {"system": SPLIT_SYSTEM, "user": text}, project_id=pid)
    shots = r["data"]["shots"]
    for s in shots:
        dao.create_shot(conn, project_id=pid, idx=s["idx"],
                        visual_prompt=s["visual_prompt"], narration=s["narration"])
    print(f"    {len(shots)} 个分镜，LLM 成本 ¥{r['cost']:.4f}")

    # 2. 逐镜头素材：分镜图（全部）+ 视频（前 n_video_shots 个）+ 配音
    #    P3 起幂等收敛到网关：每次调用过缓存，命中零成本，参数变化自动重生成
    segments, srt_entries, t = [], [], 0.0
    total_cost = r["cost"]
    for s in shots:
        idx = int(s["idx"])
        img = dirs["shots"] / f"shot_{idx:02d}.png"
        ri = gw.call("image", {"prompt": s["visual_prompt"], "out_path": str(img)},
                     project_id=pid)
        if not ri.get("cached"):
            _download(ri["url"], img)
            total_cost += ri["cost"]
            print(f"    shot {idx:02d} 分镜图 ¥{ri['cost']:.2f}")

        video_src = dirs["clips"] / f"shot_{idx:02d}_src.mp4"
        use_video = idx <= n_video_shots
        if use_video:
            rv = gw.call("video", {
                "prompt": s["visual_prompt"], "out_path": str(video_src),
                "seconds": 5, "resolution": "480p",
            }, project_id=pid)
            total_cost += rv["cost"]
            if not rv.get("cached"):
                print(f"    shot {idx:02d} 视频 ¥{rv['cost']:.2f}（{rv['latency_ms'] / 1000:.0f}s）")

        audio = dirs["audio"] / f"shot_{idx:02d}.mp3"
        gw.call("tts", {"text": s["narration"], "out_path": str(audio)}, project_id=pid)

        seg = dirs["clips"] / f"shot_{idx:02d}.mp4"
        visual = video_src if (use_video and video_src.exists()) else img
        dur = ffmpeg.make_segment(visual, audio, seg, is_image=(visual != video_src))
        dao.update_shot(conn, f"{pid}-s{idx:02d}", duration=dur, status="segmented")
        segments.append(seg)
        srt_entries.append((t, t + dur, s["narration"]))
        t += dur

    # 3. 拼接 + 字幕 + 可选 BGM
    print(f"[{pid}] 3/4 拼接 {len(segments)} 段（共 {t:.1f}s）...")
    concat_out = dirs["final"] / "concat.mp4"
    ffmpeg.concat_segments(segments, concat_out)
    srt = dirs["final"] / "subtitle.srt"
    ffmpeg.write_srt(srt_entries, srt)

    # 4. 终合成
    draft = dirs["final"] / "draft.mp4"
    bgm = _pick_bgm()
    print(f"[{pid}] 4/4 烧字幕{'+ BGM: ' + bgm.name if bgm else '（无 BGM 文件，跳过）'} ...")
    ffmpeg.finalize(concat_out, srt, draft, bgm=bgm)

    conn.close()
    return {"pid": pid, "draft": draft, "duration": ffmpeg.probe_duration(draft),
            "cost": total_cost, "shots": len(shots)}
