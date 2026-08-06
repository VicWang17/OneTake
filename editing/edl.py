"""EDL（剪辑决策表）：描述最终时间轴的数据文件，与渲染分离。

原则：时间轴的唯一事实源是音频实测时长（1.5 对齐后的 shots.duration）。
粒度对齐规则（5s 出片 vs 台词 4-8s）：
- 视频长于台词：裁剪
- 视频短于台词：慢放补齐（setpts 时域变换，插画片 0.6-0.9 倍速无感）——
  零成本，不为 2-3s 缺口重生成 10s 素材（token 翻倍）
- 无视频素材的镜头：退回分镜图 Ken Burns（图卡兜底）
"""

import json
from pathlib import Path

from editing import ffmpeg

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
BGM_DIR = ROOT / "assets" / "bgm"


def build_edl(pid: str) -> dict:
    """读分镜包 → EDL JSON（落盘 projects/{pid}/final/edl.json）。"""
    pdir = PROJECTS_DIR / pid
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))

    video_track, voice_track, sub_track = [], [], []
    t = 0.0
    for s in script["shots"]:
        idx = int(s["idx"])
        audio = pdir / "audio" / f"shot_{idx:02d}.mp3"
        dur = float(s.get("duration") or ffmpeg.probe_duration(audio))

        vsrc = pdir / "clips" / f"shot_{idx:02d}_src.mp4"
        isrc = pdir / "shots" / f"shot_{idx:02d}.png"
        if vsrc.exists():
            kind, src = "video", vsrc
        elif isrc.exists():
            kind, src = "image", isrc
        else:
            raise FileNotFoundError(f"shot {idx:02d} 无视频也无分镜图")
        src_dur = ffmpeg.probe_duration(src) if kind == "video" else dur

        video_track.append({
            "idx": idx, "kind": kind, "src": str(src),
            "src_dur": round(src_dur, 3), "timeline_dur": round(dur, 3),
            "speed": round(src_dur / dur, 3),  # <1 需慢放，>1 需裁剪
        })
        voice_track.append({"idx": idx, "src": str(audio),
                            "at": round(t, 3), "dur": round(dur, 3)})
        sub_track.append({"at": round(t, 3), "end": round(t + dur, 3),
                          "text": s["narration"]})
        t += dur

    bgm = next(iter(sorted(BGM_DIR.glob("*.mp3"))), None) if BGM_DIR.exists() else None
    edl = {
        "pid": pid, "fps": ffmpeg.FPS, "width": ffmpeg.WIDTH, "height": ffmpeg.HEIGHT,
        "duration": round(t, 3),
        "tracks": {
            "video": video_track, "voice": voice_track, "subtitle": sub_track,
            "bgm": {"src": str(bgm), "ducking": True} if bgm else None,
        },
    }
    out = pdir / "final" / "edl.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")
    return edl
