"""模型注册表加载器（P4）：YAML + mtime 热更新 + 权重路由。

注册表是模型治理的唯一事实源：上下架（status）、灰度权重（weight）、
备胎（fallback）、并发上限（concurrency）、价格（price）全部配置化。
pricing.py 的 PRICING 与本模块共用本文件，杜绝双写漂移。
"""

import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "serving" / "registry.yaml"

_cache: dict = {"mtime": 0.0, "models": []}


def load_registry() -> list[dict]:
    """读注册表；文件 mtime 变化才重新解析（热更新）。"""
    mtime = REGISTRY_PATH.stat().st_mtime
    if mtime != _cache["mtime"]:
        import yaml as _yaml  # 局部导入防循环
        data = _yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
        _cache["models"] = data["models"]
        _cache["mtime"] = mtime
    return _cache["models"]


def get_model(name: str) -> dict | None:
    for m in load_registry():
        if m["name"] == name:
            return m
    return None


def get_fallback(name: str) -> str | None:
    m = get_model(name)
    return m.get("fallback") if m else None


def list_active(capability: str, tier: str) -> list[dict]:
    """某能力 + 档位的可用模型（status=active 且声明该档位）。"""
    return [m for m in load_registry()
            if capability in m.get("capabilities", [])
            and tier in m.get("tiers", [])
            and m.get("status") == "active"]


def route(capability: str, tier: str) -> dict:
    """按权重灰度路由：weight 为分流百分比，weight=0 表示只作备胎不主动分流。

    例：video/draft 下 fast:90 + 2.0:10 → 90% 请求去 fast，10% 去 2.0。
    """
    candidates = [m for m in list_active(capability, tier) if m.get("weight", 0) > 0]
    if not candidates:  # 无权重候选时退化为任一 active（容错）
        candidates = list_active(capability, tier)
    if not candidates:
        raise LookupError(f"注册表中无可用模型: {capability}/{tier}")
    weights = [m["weight"] for m in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def pricing_table() -> dict:
    """从注册表派生计费表（供 gateway/pricing.py 使用）。"""
    return {m["name"]: dict(m["price"]) for m in load_registry() if m.get("price")}
