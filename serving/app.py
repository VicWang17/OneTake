"""模型服务层 HTTP API（P4）：OpenAI 兼容内部服务，管线与厂商 SDK 的唯一边界。

分层职责：本层做路由（注册表权重灰度）；网关 core 做缓存/计费/熔断/降级。
管线经 HTTP 调用本服务（ONETAKE_SERVING_URL 开启），编排与部署解耦——
本服务可原样迁到独立机器。

运行：uv run uvicorn serving.app:app --port 8300
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db import dao
from gateway import core as gw
from serving import registry

app = FastAPI(title="OneTake Model Serving", version="0.4.0")

TASK_ENDPOINT = {"llm", "image", "video", "tts"}


def _gw(task_type: str, payload: dict, tier: str):
    """路由（注册表权重）→ 网关（缓存/计费/熔断/降级）。

    注意：tier 不并入 payload（它是独立的 key 维度），payload 字段缺省保持缺省
    （不填默认值）——保证 wire 归一化后 idem_key 与本地直连一致。
    """
    payload = dict(payload)
    if not payload.get("model"):
        payload["model"] = registry.route(
            {"llm": "llm", "image": "image", "video": "video", "tts": "tts"}[task_type],
            tier)["name"]
    try:
        return gw.call(task_type, payload, tier=tier)
    except gw.BudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


class ChatReq(BaseModel):
    messages: list[dict] | None = None
    system: str | None = None
    user: str | None = None
    tier: str = "draft"


@app.post("/v1/chat/completions")
def chat(req: ChatReq):
    payload = {"messages": req.messages} if req.messages else {
        "system": req.system, "user": req.user}
    return _gw("llm", payload, req.tier)


def _dump(req: BaseModel) -> dict:
    return req.model_dump(exclude_none=True, exclude={"tier"})


class ImageReq(BaseModel):
    prompt: str
    size: str | None = None
    reference_url: str | None = None
    out_path: str | None = None
    tier: str = "draft"


@app.post("/v1/images/generations")
def images(req: ImageReq):
    return _gw("image", _dump(req), req.tier)


class VideoReq(BaseModel):
    prompt: str
    out_path: str
    seconds: int | None = None
    resolution: str | None = None
    first_frame_url: str | None = None
    tier: str = "draft"


@app.post("/v1/videos/generations")
def videos(req: VideoReq):
    return _gw("video", _dump(req), req.tier)


class TtsReq(BaseModel):
    text: str
    out_path: str
    tier: str = "draft"


@app.post("/v1/audio/speech")
def speech(req: TtsReq):
    return _gw("tts", req.model_dump(exclude_none=True), req.tier)


@app.get("/v1/models")
def list_models():
    """注册表模型清单（含状态/权重/档位，热更新后立即可见）。"""
    return {"data": [{
        "name": m["name"], "provider": m["provider"],
        "capabilities": m["capabilities"], "tiers": m.get("tiers"),
        "status": m["status"], "weight": m.get("weight", 0),
        "fallback": m.get("fallback"),
    } for m in registry.load_registry()]}


@app.get("/health")
def health():
    conn = dao.get_conn()
    spent = dao.today_spend(conn)
    conn.close()
    return {
        "status": "ok",
        "models_active": sum(1 for m in registry.load_registry()
                             if m["status"] == "active"),
        "today_spend": spent,
        "daily_budget_limit": gw.DAILY_BUDGET_LIMIT,
    }
