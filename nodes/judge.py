"""VLM 质检节点（P7）：镜头视频抽帧 + 分镜意图 → 双维度评分（VLM-as-Judge）。

维度：semantic（语义一致性：画面是否表达分镜意图）/ quality（画面质量：
崩坏/变形/文字乱码）。任一 <3 分判不合格，触发重生成（≤2 次，第二次带失败原因）。
"""

import base64
import subprocess
from pathlib import Path

from editing import ffmpeg
from gateway import core as gw

JUDGE_PROMPT = """你是视频质量评审。以下是某分镜视频的 3 帧画面（第 1/3/5 秒）和它的创作意图。

分镜画面意图：{visual}
台词：{narration}

请评分，输出严格 JSON：
{{"semantic": 1-5 的整数, "quality": 1-5 的整数, "issues": "主要问题（无则空字符串）"}}
- semantic：画面是否表达了分镜意图（5=完全吻合，1=完全无关）
- quality：画面质量（崩坏/变形/文字乱码/严重水印遮挡；5=无问题）
只输出 JSON。"""

PASS_SCORE = 3


def extract_frames(video: Path, out_dir: Path) -> list[Path]:
    """抽第 1/3/5 秒三帧（开头/中间/结尾，覆盖"开头好后面崩"）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for sec in (1, 3, 5):
        f = out_dir / f"{video.stem}_f{sec}.jpg"
        subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(sec), "-i", str(video), "-frames:v", "1",
                        "-q:v", "3", str(f)], check=True)
        frames.append(f)
    return frames


def judge_shot(video: Path, visual_prompt: str, narration: str,
               project_id: str, prev_issue: str | None = None) -> dict:
    """单镜头质检。prev_issue：上次失败原因（第二次重生成时的上下文）。"""
    frames = extract_frames(video, video.parent.parent / "frames")
    images_b64 = [f"data:image/jpeg;base64,{base64.b64encode(f.read_bytes()).decode()}"
                  for f in frames]
    prompt = JUDGE_PROMPT.format(visual=visual_prompt, narration=narration)
    if prev_issue:
        prompt += f"\n\n注意：上一版的问题是「{prev_issue}」，请重点检查是否已修复。"
    r = gw.call("vl", {"images_b64": images_b64, "prompt": prompt},
                project_id=project_id)
    data = r["data"]
    sem = int(data.get("semantic", 0))
    qua = int(data.get("quality", 0))
    return {"semantic": sem, "quality": qua,
            "issues": data.get("issues", ""),
            "passed": sem >= PASS_SCORE and qua >= PASS_SCORE}
