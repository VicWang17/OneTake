"""brief 注入链路用例（P1）：约束块进大纲 prompt、禁止内容进出图 prompt、CLI 约束落 brief.json。

零 API 成本：monkeypatch outline_node.generate_outline / dao.get_conn / memory inject。
用法：uv run python evals/runners/inject_cases.py
产出：evals/reports/inject_<sha8>_<date>.json
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
from nodes import outline as outline_node  # noqa: E402
from pipeline import brief as b  # noqa: E402
from pipeline import storyboard as sb  # noqa: E402

_REAL_GET_CONN = dao.get_conn
_REAL_GEN_OUTLINE = outline_node.generate_outline


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def _make_brief() -> dict:
    br = b.new_brief()
    b.set_constraint(br, "duration", 45, "user_current")
    b.set_constraint(br, "style", "扁平插画，冷色调", "user_current")
    b.add_list_constraint(br, "forbidden", "出现人物", "user_current")
    return br


def run_cases() -> list[dict]:
    results = []

    # 1. prompt_block 内容齐全
    block = b.prompt_block(_make_brief())
    results.append({"case_id": "inject-prompt-block", "checks": [
        ("has_duration", "45 秒" in block),
        ("has_style", "扁平插画" in block),
        ("has_forbidden", "出现人物" in block),
        ("empty_brief_empty_block", b.prompt_block(None) == "" and b.prompt_block(b.new_brief()) == ""),
    ]})

    # 2. 出图 prompt 携带负面约束
    script = {"style_anchor": "扁平插画风格", "character_anchor": "月牙吉祥物"}
    shot = {"idx": 1, "visual_prompt": "微波炉特写", "has_character": False}
    prompt = sb.build_image_prompt(script, shot, _make_brief())
    prompt_no_brief = sb.build_image_prompt(script, shot)
    results.append({"case_id": "inject-image-negative", "checks": [
        ("negative_appended", "画面严格避免：出现人物" in prompt),
        ("anchors_preserved", "扁平插画风格" in prompt and "微波炉特写" in prompt),
        ("no_brief_no_negative", "严格避免" not in prompt_no_brief),
    ]})

    # 3. 大纲生成穿线：create_outline 把 brief_block 传给 generate_outline
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        old_root_sb, old_root_b = sb.PROJECTS_DIR, b.PROJECTS_DIR
        sb.PROJECTS_DIR = b.PROJECTS_DIR = tmp
        captured = {}

        def fake_gen(topic, project_id, feedback=None, skill=None,
                     memory_block="", brief_block=""):
            captured["brief_block"] = brief_block
            return {"title": "t", "logline": "l", "audience": "a",
                    "target_duration": 45, "structure": [{"part": "钩子", "summary": "s"}],
                    "style": {"tone": "t", "visual": "v", "voice": "v"}}
        conn = _REAL_GET_CONN(tmp / "test.db")
        # 每次调用给新连接：被调方（memory inject 等）会自行 close，共享单连接会被关死
        dao.get_conn = lambda *a, **kw: _REAL_GET_CONN(tmp / "test.db")
        outline_node.generate_outline = fake_gen
        try:
            sb.create_outline("测试选题", pid="pinject", brief=_make_brief())
        finally:
            sb.PROJECTS_DIR, b.PROJECTS_DIR = old_root_sb, old_root_b
            dao.get_conn = _REAL_GET_CONN
            outline_node.generate_outline = _REAL_GEN_OUTLINE
    results.append({"case_id": "inject-outline-threading", "checks": [
        ("brief_block_reaches_outline", "45 秒" in captured.get("brief_block", "")),
        ("constraint_position_first",
         captured.get("brief_block", "").startswith("\n\n创作约束")),
    ]})

    # 4. create_storyboard 自动读 brief.json（多轮约束保持，无需显式传参）
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        old_root_sb, old_root_b = sb.PROJECTS_DIR, b.PROJECTS_DIR
        sb.PROJECTS_DIR = b.PROJECTS_DIR = tmp
        pdir = tmp / "pmulti"
        pdir.mkdir()
        (pdir / "script.json").write_text(json.dumps(
            {"topic": "t", "outline": {"title": "x"}}, ensure_ascii=False), encoding="utf-8")
        b.save_brief("pmulti", _make_brief())
        captured = {}

        def fake_gen2(topic, project_id, feedback=None, skill=None,
                      memory_block="", brief_block=""):
            captured["brief_block"] = brief_block
            return {"title": "t", "logline": "l", "audience": "a", "target_duration": 45,
                    "structure": [{"part": "钩子", "summary": "s"}],
                    "style": {"tone": "t", "visual": "v", "voice": "v"}}
        conn = _REAL_GET_CONN(tmp / "test.db")
        dao.get_conn = lambda *a, **kw: _REAL_GET_CONN(tmp / "test.db")  # 同上：每次新连接
        outline_node.generate_outline = fake_gen2
        # 分镜与角色节点也 stub 掉（本用例只验证 brief 自动加载）
        from nodes import storyboard as sb_node, character as char_node
        real_sb, real_char = sb_node.generate_storyboard, char_node.generate_character_sheet
        sb_node.generate_storyboard = lambda *a, **kw: []
        char_node.generate_character_sheet = lambda *a, **kw: {}
        try:
            sb.create_storyboard(pid="pmulti", feedback="重写")
        finally:
            sb.PROJECTS_DIR, b.PROJECTS_DIR = old_root_sb, old_root_b
            dao.get_conn = _REAL_GET_CONN
            outline_node.generate_outline = _REAL_GEN_OUTLINE
            sb_node.generate_storyboard = real_sb
            char_node.generate_character_sheet = real_char
    results.append({"case_id": "inject-brief-auto-load", "checks": [
        ("brief_json_auto_loaded", "出现人物" in captured.get("brief_block", "")),
    ]})

    return results


def main() -> None:
    results = [
        {**r, "verdict": "pass" if all(ok for _, ok in r["checks"]) else "fail"}
        for r in run_cases()
    ]
    sha = _git_sha()
    report = {
        "runner": "evals/runners/inject_cases.py",
        "git_sha": sha,
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layer": "deterministic (monkeypatched outline/dao, no api)",
        "results": [
            {**r, "checks": [{"check": c, "ok": ok} for c, ok in r["checks"]]}
            for r in results
        ],
    }
    out = ROOT / "evals/reports" / f"inject_{sha[:8]}_{time.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        failed = [c for c, ok in r["checks"] if not ok]
        mark = "✓" if r["verdict"] == "pass" else "✗"
        print(f"{mark} {r['case_id']}: {r['verdict']}"
              + (f"  未过检查: {failed}" if failed else ""))
    print(f"\n报告: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
