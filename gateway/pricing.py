"""单价换算——计费逻辑唯一入口。

P4 起价格数据迁移到 `serving/registry.yaml`（模型治理唯一事实源），
本文件的 PRICING 从注册表派生，杜绝双写漂移。calc_cost 计费逻辑不变。
数据时点：2026-08-06 实测核实（见 registry.yaml 各模型 note）。
"""

from serving import registry

PRICING = registry.pricing_table()

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
