"""厂商适配器：DeepSeek / 火山方舟（Seedream/Seedance）/ edge-tts。

P0 内部库形态，P4 迁入 serving/adapters/。管线节点不 import 本模块，
一律走 gateway.core.call()。
"""

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

# 百炼（LLM 跨供应商降级备胎；OpenAI 兼容协议）
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = "qwen3.7-flash"


def qwen_messages(messages: list[dict], max_retries: int = 3) -> tuple[str, dict, int]:
    """百炼 Qwen 多轮调用（降级路径）。注意 qwen3.7-flash 是思考型模型，
    响应带 reasoning_content，content 字段仍是最终答案，直接用即可。"""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["DASHSCOPE_API_KEY"],
                    base_url=DASHSCOPE_BASE_URL)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=QWEN_MODEL, messages=messages,
                response_format={"type": "json_object"},
            )
            latency_ms = int((time.time() - t0) * 1000)
            usage = resp.usage.model_dump() if resp.usage else {}
            return resp.choices[0].message.content, usage, latency_ms
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Qwen 调用失败（{max_retries} 次）: {last_err}")

SEEDREAM_MODEL = "doubao-seedream-4-0-250828"
SEEDANCE_MODEL = "doubao-seedance-2-0-260128"
SEEDANCE_FAST_MODEL = "doubao-seedance-2-0-fast-260128"
SEEDANCE_MINI_MODEL = "doubao-seedance-2-0-mini-260615"

EDGE_TTS_VOICE = "zh-CN-YunxiNeural"


# ---------- DeepSeek（OpenAI 兼容） ----------

def deepseek_messages(messages: list[dict], max_retries: int = 3) -> tuple[str, dict, int]:
    """多轮对话调用（原始文本返回）。用于错误回灌等需要对话上下文的场景。"""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=DEEPSEEK_BASE_URL)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            resp = client.chat.completions.create(model=DEEPSEEK_MODEL, messages=messages)
            latency_ms = int((time.time() - t0) * 1000)
            usage = resp.usage.model_dump() if resp.usage else {}
            return resp.choices[0].message.content, usage, latency_ms
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"DeepSeek 调用失败（{max_retries} 次）: {last_err}")


def deepseek_json(system: str, user: str, max_retries: int = 3) -> tuple[dict, dict, int]:
    """结构化输出调用。返回 (parsed_json, usage, latency_ms)。"""
    import json

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=DEEPSEEK_BASE_URL)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
            latency_ms = int((time.time() - t0) * 1000)
            content = resp.choices[0].message.content
            usage = resp.usage.model_dump() if resp.usage else {}
            return json.loads(content), usage, latency_ms
        except Exception as e:  # 含 JSON 解析失败，错误回灌重试在 P1 做，P0 直接重试
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"DeepSeek 调用失败（{max_retries} 次）: {last_err}")


# ---------- 火山方舟 ----------

def _ark_client():
    from volcenginesdkarkruntime import Ark

    return Ark(api_key=os.environ["ARK_API_KEY"])


def seedream_image(prompt: str, size: str = "1280x720",
                   reference_url: str | None = None,
                   model: str | None = None) -> tuple[str, dict, int]:
    """文生图。支持参考图（图像锚点，角色一致性手段）。返回 (image_url, usage, latency_ms)。"""
    t0 = time.time()
    kwargs: dict = {"model": model or SEEDREAM_MODEL, "prompt": prompt,
                    "size": size, "response_format": "url"}
    if reference_url:
        kwargs["image"] = reference_url
    resp = _ark_client().images.generate(**kwargs)
    latency_ms = int((time.time() - t0) * 1000)
    url = resp.data[0].url
    usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
    return url, usage, latency_ms


def seedance_video(prompt: str, out_path: Path, *, model: str = SEEDANCE_MODEL,
                   seconds: int = 5, resolution: str = "480p",
                   poll_interval: int = 5, timeout: int = 600,
                   first_frame_url: str | None = None,
                   resume_task_id: str | None = None,
                   on_task_created=None) -> tuple[dict, int]:
    """视频生成全流程：创建任务 → 轮询 → 下载。返回 (task_info含usage, latency_ms)。

    崩溃恢复（P4 调度器）：resume_task_id 存在则跳过创建直接续查；
    on_task_created 回调在任务创建后立即拿到 task_id（用于持久化，崩溃不丢单）。
    """
    ark = _ark_client()
    t0 = time.time()
    if resume_task_id:
        task_id = resume_task_id
    else:
        full_prompt = f"{prompt} --rs {resolution} --dur {seconds}"
        kwargs: dict = {"model": model,
                        "content": [{"type": "text", "text": full_prompt}]}
        if first_frame_url:
            kwargs["content"].append({"type": "image_url",
                                      "image_url": {"url": first_frame_url}})
        task = ark.content_generation.tasks.create(**kwargs)
        task_id = task.id
        if on_task_created:
            on_task_created(task_id)

    while True:
        if time.time() - t0 > timeout:
            raise TimeoutError(f"Seedance 任务 {task_id} 超时（{timeout}s）")
        info = ark.content_generation.tasks.get(task_id=task_id)
        if info.status == "succeeded":
            break
        if info.status == "failed":
            raise RuntimeError(f"Seedance 任务失败: {getattr(info, 'error', info)}")
        time.sleep(poll_interval)
    latency_ms = int((time.time() - t0) * 1000)

    video_url = info.content.video_url
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(video_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    usage = info.usage.model_dump() if getattr(info, "usage", None) else {}
    return {"task_id": task_id, "usage": usage, "model": model}, latency_ms


# ---------- edge-tts（本地免费，瞬断需重试） ----------

def edge_tts_speak(text: str, out_path: Path, max_retries: int = 3) -> None:
    """中文配音合成 mp3。edge-tts 有瞬断（NoAudioReceived），必须重试 ≤3 次。"""
    import asyncio

    import edge_tts

    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async def _run():
                communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
                await communicate.save(str(out_path))

            asyncio.run(_run())
            if out_path.exists() and out_path.stat().st_size > 0:
                return
            raise RuntimeError("edge-tts 产出空文件")
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"edge-tts 失败（{max_retries} 次）: {last_err}")
