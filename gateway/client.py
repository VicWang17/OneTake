"""serving 的 HTTP 客户端（P4）：ONETAKE_SERVING_URL 设置后，网关 call() 经此转发。

管线零改动切换：gw.call 签名不变，传输从函数调用变成 localhost HTTP。
"""

import requests

_ENDPOINTS = {
    "llm": "/v1/chat/completions",
    "image": "/v1/images/generations",
    "video": "/v1/videos/generations",
    "tts": "/v1/audio/speech",
}

_TIMEOUTS = {"llm": 120, "image": 120, "video": 660, "tts": 60}


def call(task_type: str, payload: dict, tier: str, base_url: str) -> dict:
    url = base_url.rstrip("/") + _ENDPOINTS[task_type]
    body = dict(payload)
    body["tier"] = tier
    r = requests.post(url, json=body, timeout=_TIMEOUTS[task_type])
    if r.status_code != 200:
        raise RuntimeError(f"serving {task_type} 失败 {r.status_code}: {r.text[:300]}")
    return r.json()
