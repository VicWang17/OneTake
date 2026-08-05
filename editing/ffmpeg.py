"""FFmpeg 封装：路径走 FFMPEG_PATH 环境变量（默认 ffmpeg-full，含 libass）。

全局 homebrew ffmpeg 是 lite 版无 libass，烧不了字幕，切勿回退到它。
"""

import os
import subprocess
from pathlib import Path

_DEFAULT_BIN = "/opt/homebrew/opt/ffmpeg-full/bin"
FFMPEG = os.environ.get("FFMPEG_PATH", f"{_DEFAULT_BIN}/ffmpeg")
FFPROBE = os.environ.get(
    "FFPROBE_PATH", str(Path(FFMPEG).with_name("ffprobe"))
)

# 归一化参数：拼接前所有镜头统一到此规格
WIDTH, HEIGHT, FPS = 854, 480, 24
SUBTITLE_FONT = "Hiragino Sans GB"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令失败: {' '.join(cmd[:3])} ...\nstderr 末尾:\n{proc.stderr[-2000:]}"
        )
    return proc


def probe_duration(path: Path | str) -> float:
    """ffprobe 取媒体真实时长（秒）。"""
    proc = _run([
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(proc.stdout.strip())


def _video_vf() -> str:
    return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1")


def make_segment(visual: Path, audio: Path, out: Path, *,
                 is_image: bool) -> float:
    """单镜头分段：画面（图/视频）+ 配音 → 统一规格的 mp4，时长以音频为准。

    返回分段时长。图片走 zoompan 缓慢推近（Ken Burns）；视频不足时长则循环补齐。
    """
    dur = probe_duration(audio)
    out.parent.mkdir(parents=True, exist_ok=True)
    if is_image:
        frames = int(dur * FPS) + FPS
        vf = (f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
              f"crop={WIDTH * 2}:{HEIGHT * 2},"
              f"zoompan=z='min(1+0.06*on/{frames},1.06)'"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1")
        cmd = [FFMPEG, "-y", "-i", str(visual), "-i", str(audio),
               "-vf", vf, "-t", f"{dur:.3f}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-af", "aresample=44100", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(out)]
    else:
        cmd = [FFMPEG, "-y", "-stream_loop", "-1", "-i", str(visual),
               "-i", str(audio), "-t", f"{dur:.3f}",
               "-vf", _video_vf(),
               "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-af", "aresample=44100", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(out)]
    _run(cmd)
    return dur


def concat_segments(segments: list[Path], out: Path) -> None:
    """按顺序拼接（各分段已归一化，直接 -c copy）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    list_file = out.with_suffix(".concat.txt")
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in segments), encoding="utf-8")
    try:
        _run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
              "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out)])
    finally:
        list_file.unlink(missing_ok=True)


def _srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries: list[tuple[float, float, str]], path: Path) -> None:
    """entries: (start_sec, end_sec, text)。"""
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def finalize(video_in: Path, srt: Path, out: Path, *,
             bgm: Path | None = None, bgm_volume: float = 0.15) -> None:
    """终合成：烧硬字幕（Hiragino Sans GB + 描边）；可选 BGM 混音（固定低音量，闪避在 P2 做）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    style = (f"FontName={SUBTITLE_FONT},FontSize=16,PrimaryColour=&HFFFFFF,"
             f"OutlineColour=&H80000000,BorderStyle=1,Outline=1,Shadow=0,"
             f"MarginV=24")
    sub_filter = f"subtitles='{srt.resolve()}':force_style='{style}'"
    cmd = [FFMPEG, "-y", "-i", str(video_in)]
    if bgm and Path(bgm).exists():
        dur = probe_duration(video_in)
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        fc = (f"[0:v]{sub_filter}[v];"
              f"[1:a]volume={bgm_volume},atrim=0:{dur:.3f}[bg];"
              f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                "-t", f"{dur:.3f}"]
    else:
        cmd += ["-vf", sub_filter, "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    _run(cmd)
