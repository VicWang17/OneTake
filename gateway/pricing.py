"""单价表（人民币）——全项目计费唯一事实源。

数据时点：2026-08-04 开工前联网核实（见 TODO.md 开工前核实清单与 prd.md 3.1）。
价格为该时点快照，官方调价后需同步更新本文件与 prd.md 3.6。
"""

PRICING = {
    # LLM：DeepSeek（美元/百万 token，调用时按 USD_RATE 折算；高峰时段 9-12/14-18 点拟 2 倍）
    "deepseek-v4-flash": {
        "kind": "llm",
        "input_per_mtok": 0.14,   # USD / 1M tokens
        "output_per_mtok": 0.28,
        "currency": "USD",
    },
    # LLM 降级：百炼 qwen3.7-flash（人民币/百万 token；每模型 100 万 token 免费额度内计 0）
    "qwen3.7-flash": {
        "kind": "llm",
        "input_per_mtok": 0.2,    # CNY / 1M tokens
        "output_per_mtok": 0.8,
        "currency": "CNY",
    },
    # VLM 质检：百炼
    "qwen3-vl-flash": {
        "kind": "llm",
        "input_per_mtok": 0.15,
        "output_per_mtok": 1.5,
        "currency": "CNY",
    },
    # 文生图：Seedream 4.0 按张计费
    "doubao-seedream-4-0-250828": {"kind": "image", "per_image": 0.20, "currency": "CNY"},
    "doubao-seedream-4-5-251128": {"kind": "image", "per_image": 0.25, "currency": "CNY",
                                   "note": "4.0 的降级备胎（跨版本）"},
    "doubao-seedream-5-0-pro-260628": {"kind": "image", "per_image": 0.30, "currency": "CNY"},
    # 视频：Seedance 按 token 计费。实测发现：同分辨率时长下各档位 token 数相同
    # （5s 480p 均为 50638），成本差异完全来自每 token 单价（2026-08-06 实测）
    "doubao-seedance-2-0-260128": {
        "kind": "video",
        "per_mtok": 46.0,          # 元/百万 token（480p/720p 不含视频输入；实测吻合 ¥2.31/5s）
        "per_mtok_with_video_input": 28.0,  # 含视频输入（图生视频/参考视频）
        "currency": "CNY",
    },
    "doubao-seedance-2-0-fast-260128": {
        "kind": "video", "per_mtok": 14.0, "currency": "CNY",
        "note": "官方 AI Hub 价（2026-08-06）；实测 5s 480p = ¥0.71，草稿档主力",
    },
    "doubao-seedance-2-0-mini-260615": {
        "kind": "video", "per_mtok": 14.0, "currency": "CNY",
        "note": "⚠️ 占位：官方单价未查到（聚合站参考 ¥0.25/s），待控制台账单核实后修正",
    },
    # edge-tts：本地免费
    "edge-tts": {"kind": "tts", "per_char": 0.0, "currency": "CNY"},
}

USD_RATE = 7.2  # 2026-08-04 汇率快照，仅用于 DeepSeek 美元价折算展示


def calc_cost(model: str, usage: dict | None = None, *, n_images: int = 1,
              seconds: float = 0.0, resolution: str = "480p",
              n_chars: int = 0) -> float:
    """按单价表计算一次调用的成本（人民币元）。usage 为厂商返回的用量字典。"""
    p = PRICING.get(model)
    if p is None:
        return 0.0
    rate = USD_RATE if p.get("currency") == "USD" else 1.0
    if p["kind"] == "llm":
        usage = usage or {}
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        return (in_tok * p["input_per_mtok"] + out_tok * p["output_per_mtok"]) / 1e6 * rate
    if p["kind"] == "image":
        return p["per_image"] * n_images * rate
    if p["kind"] == "video":
        # 优先按厂商返回的 token 用量精确计费；无 usage 时退化为按秒估算
        usage = usage or {}
        tokens = usage.get("completion_tokens")
        if tokens and p.get("per_mtok"):
            return tokens * p["per_mtok"] / 1e6 * rate
        key = "per_second_720p" if resolution.startswith("720") else "per_second_480p"
        return p.get(key, p.get("per_second_480p", 0.0)) * seconds * rate
    if p["kind"] == "tts":
        return p.get("per_char", 0.0) * n_chars * rate
    return 0.0
