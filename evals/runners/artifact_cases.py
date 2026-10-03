"""产物失效判定用例（P0）：fr-04 截断文件、输入变更失效、正常复用、缺失重做。

零 API 成本：临时 SQLite + 本地 ffmpeg 生成的占位 mp4，直接测
pipeline/videos.py 的 _clip_valid 复用判定。
用法：uv run python evals/runners/artifact_cases.py
产出：evals/reports/artifact_<sha8>_<date>.json
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from db import dao  # noqa: E402
from editing.ffmpeg import FFMPEG  # noqa: E402
from gateway import core as gw  # noqa: E402
from pipeline import videos  # noqa: E402

MODEL = "doubao-seedance-2-0-fast-260128"


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _make_mp4(path: Path) -> None:
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=854x480:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, check=True)


def _payload(prompt: str, out: Path) -> dict:
    return {"prompt": prompt, "out_path": str(out), "model": MODEL,
            "seconds": 5, "resolution": "480p", "first_frame_url": "data:image/png;base64,AAAA"}


def _record_success(conn, payload: dict, out: Path) -> None:
    dao.log_generation(conn, task_type="video", model=MODEL, cost=0.71,
                       status="succeeded", file_path=str(out),
                       idem_key=gw.idem_key_for("video", MODEL, payload))


def run_cases() -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        conn = dao.get_conn(tmp / "test.db")
        out = tmp / "shot_01_src.mp4"
        payload = _payload("镜头推近", out)

        # 1. 正常复用：产物完好 + 输入指纹有成功记录 → 有效
        _make_mp4(out)
        _record_success(conn, payload, out)
        ok = videos._clip_valid(conn, out, payload, MODEL)
        results.append({"case_id": "fr-artifact-reuse", "checks": [("valid_reuse", ok)],
                        "verdict": "pass" if ok else "fail"})

        # 2. 输入变更：文件没动但 motion_prompt 变了 → 指纹无匹配记录 → 失效
        changed = {**payload, "prompt": "镜头拉远"}
        ok = not videos._clip_valid(conn, out, changed, MODEL)
        results.append({"case_id": "fr-artifact-stale-input",
                        "checks": [("stale_input_invalidated", ok)],
                        "verdict": "pass" if ok else "fail"})

        # 3. fr-04 截断文件：记录还在、文件还在但已损坏 → 失效
        data = out.read_bytes()
        out.write_bytes(data[: len(data) // 2])
        ok = not videos._clip_valid(conn, out, payload, MODEL)
        results.append({"case_id": "fr-04", "checks": [("corrupted_detected", ok)],
                        "verdict": "pass" if ok else "fail"})

        # 4. 缺失：文件不存在 → 失效
        out.unlink()
        ok = not videos._clip_valid(conn, out, payload, MODEL)
        results.append({"case_id": "fr-artifact-missing",
                        "checks": [("missing_invalid", ok)],
                        "verdict": "pass" if ok else "fail"})
        conn.close()
    return results


def main() -> None:
    results = run_cases()
    sha = _git_sha()
    report = {
        "runner": "evals/runners/artifact_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (temp sqlite + local ffmpeg, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"artifact_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}")
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
