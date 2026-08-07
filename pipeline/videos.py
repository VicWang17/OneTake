"""视频批量生成（P2）：分镜图 + motion_prompt → 全部镜头视频。

设计取舍（P4 正式调度器前的简易版，够用即可）：
- 线程池并发 ≤5（火山 SDK 同步阻塞，线程是最简封装；上限防限流）
- 单镜头指数退避重试 ≤3 次（5/10/20s；每次重试 ¥0.71，上限即预算护栏）
- 幂等：已有视频跳过；motion_prompt 缓存在 script.json（重跑不重复付 LLM 费）
- 单镜头失败不拖垮全局：落库 video_failed，CLI --shots 可单独重跑
"""

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from db import dao
from gateway import adapters, core as gw
from nodes import motion as motion_node

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

MAX_WORKERS = 5
MAX_RETRIES = 3
BACKOFF = [5, 10, 20]


def _data_url(img: Path) -> str:
    b64 = base64.b64encode(img.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def _gen_one(pid: str, shot: dict, img: Path, out: Path, model: str) -> dict:
    """单镜头：base64 首帧 + motion_prompt → 视频（含重试）。"""
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = gw.call("video", {
                "prompt": shot["motion_prompt"],
                "out_path": str(out),
                "model": model,
                "seconds": 5, "resolution": "480p",
                "first_frame_url": _data_url(img),
            }, project_id=pid)
            return {"idx": shot["idx"], "ok": True, "cost": r["cost"],
                    "latency_s": round(r["latency_ms"] / 1000, 1)}
        except Exception as e:  # noqa: BLE001 —— 重试后仍失败才落库
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF[attempt])
    return {"idx": shot["idx"], "ok": False, "cost": 0.0, "error": str(last_err)[:200]}


def batch_generate_videos(pid: str, only_shots: list[int] | None = None,
                          model: str | None = None) -> dict:
    """批量生成分镜视频。only_shots 用于失败镜头单独重跑。"""
    model = model or adapters.SEEDANCE_FAST_MODEL
    pdir = PROJECTS_DIR / pid
    script_path = pdir / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    clips_dir = pdir / "clips"
    clips_dir.mkdir(exist_ok=True)

    conn = dao.get_conn()
    tasks = []
    for s in script["shots"]:
        idx = int(s["idx"])
        if only_shots and idx not in only_shots:
            continue
        out = clips_dir / f"shot_{idx:02d}_src.mp4"
        img = pdir / "shots" / f"shot_{idx:02d}.png"
        if not img.exists():
            raise FileNotFoundError(f"缺分镜图: {img}（先跑 images --pid {pid}）")
        if not s.get("motion_prompt"):  # 缓存：重跑不重复付 LLM 费
            s["motion_prompt"] = motion_node.generate_motion_prompt(s, pid)
        tasks.append((s, img, out))

    # motion_prompt 可能已更新，先落盘再开工
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if not tasks:
        conn.close()
        return {"pid": pid, "succeeded": 0, "failed": 0, "cost": 0.0, "note": "全部已存在，跳过"}

    print(f"[{pid}] 批量生成 {len(tasks)} 个镜头视频（并发 {MAX_WORKERS}，{model}）...")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_gen_one, pid, s, img, out, model): s["idx"]
                   for s, img, out in tasks}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            status = "videoed" if r["ok"] else "video_failed"
            dao.update_shot(conn, f"{pid}-s{r['idx']:02d}", status=status)
            print(f"    shot {r['idx']:02d} {'✓' if r['ok'] else '✗ ' + r.get('error', '')[:60]}")

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    if not failed:
        dao.update_project(conn, pid, status="videoed")
    conn.close()
    return {"pid": pid, "succeeded": len(ok), "failed": len(failed),
            "failed_idx": [r["idx"] for r in failed],
            "cost": round(sum(r["cost"] for r in ok), 2)}
