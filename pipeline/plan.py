"""P2 结构化任务规划：在既有能力集合上生成可校验、可执行的计划。

设计要点：
- 计划（Plan）= 节点（PlanNode）DAG。节点带 ID/能力/依赖/输入引用/输出契约/
  成功标准/验证器/超时/重试，全部可序列化落盘（projects/{pid}/plans/）。
- 构建器（plan_create / plan_shot_edit / plan_narration_edit / plan_duration_adjust）
  把四类任务编译成节点图；局部修改只含受影响链路 + 共享下游，其余产物进
  protected 列表——验证器拒绝任何越界覆盖。
- validate_plan 执行前静态检查：未知能力 / 缺失输入 / 循环依赖 / 超预算 / 受保护产物。
- topo_levels 按真实依赖分层（Kahn），同层可并行；参考首图的镜头在构建期就
  挂上对 shot_image_01 的依赖，排序自然等待。
- execute_plan 逐层执行：handler 跑完 → 产物持久化检查 → 验证器通过，三者齐备
  才标记 succeeded 并释放下游；失败按 max_retries 重试，耗尽则下游整支 skipped。
"""

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

# ---------- 能力注册表（既有能力的元数据，不是新实现） ----------

CAPABILITIES: dict[str, dict] = {
    "storyboard": {"cost_est": 0.05, "desc": "大纲+分镜表生成/修订"},
    "shot_image": {"cost_est": 0.10, "desc": "单镜分镜图生成"},
    "tts":        {"cost_est": 0.02, "desc": "单镜旁白合成"},
    "align":      {"cost_est": 0.05, "desc": "台词时长对齐（未变文本走缓存零成本）"},
    "shot_video": {"cost_est": 1.50, "desc": "单镜视频生成（草稿档）"},
    "edl":        {"cost_est": 0.0,  "desc": "EDL 时间线重建"},
    "render":     {"cost_est": 0.0,  "desc": "成片渲染"},
}

# ---------- 产物验证器注册表（确定性检查，(project_dir, node) -> (ok, evidence)） ----------

def _v_exists(project_dir: Path, node: "PlanNode") -> tuple[bool, str]:
    missing = [o for o in node.outputs
               if not (project_dir / o).exists() or (project_dir / o).stat().st_size == 0]
    return (not missing), ("全部产物已落盘" if not missing else f"缺失/空产物: {missing}")


def _v_ffprobe(project_dir: Path, node: "PlanNode") -> tuple[bool, str]:
    ok, ev = _v_exists(project_dir, node)
    if not ok:
        return ok, ev
    from editing import ffmpeg
    for o in node.outputs:
        if o.endswith((".mp4", ".mp3")):
            try:
                if ffmpeg.probe_duration(project_dir / o) <= 0:
                    return False, f"{o} 时长为 0"
            except Exception as e:  # ffprobe 失败 = 文件损坏
                return False, f"{o} ffprobe 失败: {e}"
    return True, "产物完好（ffprobe）"


VALIDATORS = {"exists": _v_exists, "ffprobe": _v_ffprobe, "none": lambda d, n: (True, "")}


# ---------- 数据结构 ----------

@dataclass
class PlanNode:
    id: str                      # 如 "shot_image_03"
    capability: str              # CAPABILITIES 键
    depends_on: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)   # 输入引用：{"ref": "相对路径", ...}
    outputs: list[str] = field(default_factory=list)  # 输出契约：相对项目目录路径
    success_criteria: str = ""
    validator: str = "exists"
    timeout_s: int = 600
    max_retries: int = 2
    cost_est: float = 0.0
    status: str = "pending"      # pending/running/succeeded/failed/skipped
    evidence: str = ""


@dataclass
class Plan:
    plan_id: str
    pid: str
    kind: str                    # create / shot_edit / narration_edit / duration_adjust
    goal: str
    nodes: list[PlanNode]
    budget_cny: float
    protected: list[str] = field(default_factory=list)  # 受保护产物（已确认，禁止覆盖）
    meta: dict = field(default_factory=dict)            # 构建期决策依据（如删镜清单）
    version: int = 1
    created_at: str = ""

    def node(self, node_id: str) -> PlanNode:
        return next(n for n in self.nodes if n.id == node_id)


# ---------- 构建器 ----------

def _mk(kind: str, pid: str, goal: str, nodes: list[PlanNode],
        budget: float, protected=None, meta=None) -> Plan:
    return Plan(plan_id=f"plan-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
                pid=pid, kind=kind,
                goal=goal, nodes=nodes, budget_cny=budget,
                protected=protected or [], meta=meta or {},
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def _node(nid: str, cap: str, deps: list[str], inputs: dict, outputs: list[str],
          criteria: str, validator: str = "exists", timeout: int = 600,
          retries: int = 2) -> PlanNode:
    return PlanNode(id=nid, capability=cap, depends_on=deps, inputs=inputs,
                    outputs=outputs, success_criteria=criteria, validator=validator,
                    timeout_s=timeout, max_retries=retries,
                    cost_est=CAPABILITIES[cap]["cost_est"])


def _img(p): return f"shots/shot_{p:02d}.png"
def _aud(p): return f"audio/shot_{p:02d}.mp3"
def _clip(p): return f"clips/shot_{p:02d}_src.mp4"


def plan_create(pid: str, n_shots: int, budget_cny: float,
                ref_first_shots: list[int] | None = None) -> Plan:
    """新建全链计划。ref_first_shots：画面参考首图（首尾帧一致性）的镜头，
    其图像节点依赖 shot_image_01——参考图不出，这些镜头不能开工。"""
    ref_first = set(ref_first_shots or [])
    nodes = [_node("storyboard", "storyboard", [], {}, ["script.json"],
                   "script.json 通过 Schema 校验")]
    for i in range(1, n_shots + 1):
        deps = ["storyboard"] + (["shot_image_01"] if i in ref_first and i != 1 else [])
        nodes.append(_node(f"shot_image_{i:02d}", "shot_image", deps,
                           {"script": "script.json"}, [_img(i)],
                           "分镜图落盘且非空"))
        nodes.append(_node(f"tts_{i:02d}", "tts", ["storyboard"],
                           {"script": "script.json"}, [_aud(i)],
                           "音频落盘且时长>0", validator="ffprobe", timeout=120))
    nodes.append(_node("align", "align",
                       [f"tts_{i:02d}" for i in range(1, n_shots + 1)],
                       {"script": "script.json"}, ["script.json"],
                       "全部镜头时长回写为音频实测值"))
    for i in range(1, n_shots + 1):
        nodes.append(_node(f"shot_video_{i:02d}", "shot_video",
                           [f"shot_image_{i:02d}", "align"],
                           {"image": _img(i)}, [_clip(i)],
                           "视频落盘且 ffprobe 完好", validator="ffprobe",
                           timeout=1800, retries=3))
    nodes += [
        _node("edl", "edl", [f"shot_video_{i:02d}" for i in range(1, n_shots + 1)],
              {"script": "script.json"}, ["final/edl.json"], "EDL 覆盖全部镜头"),
        _node("render", "render", ["edl"], {"edl": "final/edl.json"},
              ["final/draft.mp4"], "成片落盘且时长>0", validator="ffprobe",
              timeout=1800),
    ]
    return _mk("create", pid, f"新建 {n_shots} 镜项目", nodes, budget_cny)


def _shared_downstream(nid_prefix: str, last_deps: list[str]) -> list[PlanNode]:
    """局部修改的共享下游：对齐 → EDL → 渲染。"""
    return [
        _node("align", "align", last_deps, {"script": "script.json"},
              ["script.json"], "时长按音频实测值回写"),
        _node("edl", "edl", ["align"], {"script": "script.json"},
              ["final/edl.json"], "EDL 时间轴正确平移"),
        _node("render", "render", ["edl"], {"edl": "final/edl.json"},
              ["final/draft.mp4"], "成片更新且时长>0", validator="ffprobe",
              timeout=1800),
    ]


def _protected_others(n_shots: int, except_shots: set[int]) -> list[str]:
    out = []
    for i in range(1, n_shots + 1):
        if i not in except_shots:
            out += [_img(i), _aud(i), _clip(i)]
    return out


def plan_shot_edit(pid: str, shot_idx: int, n_shots: int, budget_cny: float,
                   feedback: str = "") -> Plan:
    """单镜画面修改（le-01/05）：只重做该镜 图→视频 链 + 共享下游，
    其余镜头产物全部进 protected。"""
    nodes = [
        _node(f"shot_image_{shot_idx:02d}", "shot_image", [],
              {"script": "script.json", "feedback": feedback}, [_img(shot_idx)],
              "新分镜图落盘且非空"),
        _node(f"shot_video_{shot_idx:02d}", "shot_video",
              [f"shot_image_{shot_idx:02d}"], {"image": _img(shot_idx)},
              [_clip(shot_idx)], "新视频落盘且 ffprobe 完好", validator="ffprobe",
              timeout=1800, retries=3),
    ]
    nodes += _shared_downstream(f"shot_{shot_idx:02d}",
                                [f"shot_video_{shot_idx:02d}"])
    return _mk("shot_edit", pid, f"修改第 {shot_idx} 镜画面", nodes, budget_cny,
               protected=_protected_others(n_shots, {shot_idx}),
               meta={"shots": [shot_idx], "fields": ["visual_prompt"]})


def plan_narration_edit(pid: str, shot_idx: int, n_shots: int, budget_cny: float,
                        new_text: str = "") -> Plan:
    """旁白修改（le-02）：只重做该镜 TTS + 对齐/EDL/渲染；
    画面产物（图/视频）不动，全部进 protected。"""
    nodes = [
        _node(f"tts_{shot_idx:02d}", "tts", [],
              {"script": "script.json", "new_text": new_text}, [_aud(shot_idx)],
              "新音频落盘且时长>0", validator="ffprobe", timeout=120),
    ]
    nodes += _shared_downstream(f"shot_{shot_idx:02d}", [f"tts_{shot_idx:02d}"])
    protected = _protected_others(n_shots, {shot_idx}) + [_img(shot_idx),
                                                          _clip(shot_idx)]
    return _mk("narration_edit", pid, f"修改第 {shot_idx} 镜旁白", nodes, budget_cny,
               protected=protected,
               meta={"shots": [shot_idx], "fields": ["narration"]})


def plan_duration_adjust(pid: str, target_s: float, budget_cny: float,
                         project_dir: Path | None = None) -> Plan:
    """总时长调整（le-03）：执行前产出删镜/复用决策（meta.dropped/kept），
    被删镜头的下游产物作废，保留镜头零付费重做——只需重建 EDL + 渲染。

    决策规则（确定性）：保留顺序不变，从尾部低优先级镜头开始删，直到
    保留时长 ≤ target_s。优先级：purpose 含「结尾/总结/cta」的先删。"""
    pdir = project_dir or (PROJECTS_DIR / pid)
    script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
    shots = script["shots"]
    total = sum(float(s["duration"]) for s in shots)
    low = [int(s["idx"]) for s in shots
           if any(k in s.get("purpose", "") for k in ("结尾", "总结", "cta", "CTA"))]
    # 低优先级删完仍超标，继续从尾部删普通镜头（保底策略）
    candidates = sorted(low, reverse=True) + sorted(
        (int(s["idx"]) for s in shots if int(s["idx"]) not in low), reverse=True)
    kept = {int(s["idx"]) for s in shots}
    dropped, cur = [], total
    for idx in candidates:
        if cur <= target_s or len(kept) <= 1:
            break
        dur = next(float(s["duration"]) for s in shots if int(s["idx"]) == idx)
        dropped.append(idx)
        kept.discard(idx)
        cur -= dur
    # 旁白不变无需 align：edl 直接读盘上既有产物重建时间轴
    nodes = [
        _node("edl", "edl", [], {"script": "script.json"},
              ["final/edl.json"], "EDL 只含保留镜头，总时长 ≤ 目标"),
        _node("render", "render", ["edl"], {"edl": "final/edl.json"},
              ["final/draft.mp4"], "成片更新且时长>0", validator="ffprobe",
              timeout=1800),
    ]
    meta = {"target_s": target_s, "dropped_shots": sorted(dropped),
            "kept_shots": sorted(kept), "est_duration": round(cur, 1),
            "reuse": "保留镜头图/音/视频全部复用，零付费调用"}
    return _mk("duration_adjust", pid, f"总时长压缩到 ≤{target_s}s",
               nodes, budget_cny, meta=meta)


# ---------- 自然语言修改意图解析（多轮修改场景的入口，规则版） ----------

_SHOT_RE = re.compile(r"第\s*(\d+)\s*[镜个]")
_DURATION_RE = re.compile(r"(\d+)\s*[秒s]")


def parse_edit_request(text: str) -> dict | None:
    """把「第 3 镜画面改成…」这类自然语言解析成结构化修改意图。

    规则版覆盖计划首批四类任务中的修改三类；解析不出返回 None（交人工澄清）。
    组合修改（le-06）逐句解析后由调用方取并集。
    """
    intents = []
    for seg in re.split(r"[，,；;]", text):
        seg = seg.strip()
        if not seg:
            continue
        m = _SHOT_RE.search(seg)
        if m:
            idx = int(m.group(1))
            if any(k in seg for k in ("旁白", "台词", "解说", "文案")):
                intents.append({"kind": "narration_edit", "shot": idx, "text": seg})
            elif any(k in seg for k in ("画面", "图", "视觉", "场景", "视角", "镜头")):
                intents.append({"kind": "shot_edit", "shot": idx, "feedback": seg})
        elif any(k in seg for k in ("时长", "压缩", "缩短", "控制在")):
            d = _DURATION_RE.search(seg)
            if d:
                intents.append({"kind": "duration_adjust",
                                "target_s": float(d.group(1))})
    if not intents:
        return None
    return {"intents": intents}


# ---------- 静态校验 ----------

def validate_plan(plan: Plan, project_dir: Path) -> list[str]:
    """执行前检查，返回问题列表（空 = 通过）。非法计划在工具执行前被拒绝。"""
    issues: list[str] = []
    ids = {n.id for n in plan.nodes}

    for n in plan.nodes:
        if n.capability not in CAPABILITIES:
            issues.append(f"未知能力: {n.capability}（节点 {n.id}）")
        for d in n.depends_on:
            if d not in ids:
                issues.append(f"节点 {n.id} 依赖不存在的节点 {d}")
        for o in n.outputs:
            if o in plan.protected:
                issues.append(f"节点 {n.id} 覆盖受保护产物 {o}")

    # 缺失输入：输入引用的文件必须由本计划某节点产出，或已在盘上存在
    produced = {o for n in plan.nodes for o in n.outputs}
    for n in plan.nodes:
        for key, ref in n.inputs.items():
            if not isinstance(ref, str) or not ref or "/" not in ref and "." not in ref:
                continue  # 内联值（feedback/new_text），非文件引用
            if ref not in produced and not (project_dir / ref).exists():
                issues.append(f"节点 {n.id} 输入缺失: {key}={ref}（计划不产出且盘上不存在）")

    try:
        topo_levels(plan)
    except ValueError as e:
        issues.append(str(e))

    est = sum(n.cost_est for n in plan.nodes)
    if est > plan.budget_cny:
        issues.append(f"预算超限: 估算 ¥{est:.2f} > 预算 ¥{plan.budget_cny:.2f}")
    return issues


def topo_levels(plan: Plan) -> list[list[str]]:
    """Kahn 分层拓扑排序：同层节点互相无依赖可并行。环则 ValueError。"""
    indeg = {n.id: len(n.depends_on) for n in plan.nodes}
    dependents: dict[str, list[str]] = {}
    for n in plan.nodes:
        for d in n.depends_on:
            dependents.setdefault(d, []).append(n.id)
    levels, ready = [], sorted(i for i, d in indeg.items() if d == 0)
    done = 0
    while ready:
        levels.append(ready)
        nxt = []
        for nid in ready:
            done += 1
            for dep in dependents.get(nid, []):
                indeg[dep] -= 1
                if indeg[dep] == 0:
                    nxt.append(dep)
        ready = sorted(nxt)
    if done != len(plan.nodes):
        raise ValueError("循环依赖：拓扑排序无法完成")
    return levels


# ---------- 持久化 ----------

def save_plan(plan: Plan, project_dir: Path) -> Path:
    out = project_dir / "plans" / f"{plan.plan_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


def load_plan(path: Path) -> Plan:
    d = json.loads(path.read_text(encoding="utf-8"))
    d["nodes"] = [PlanNode(**n) for n in d["nodes"]]
    return Plan(**d)


# ---------- 执行器 ----------

def execute_plan(plan: Plan, handlers: dict, project_dir: Path,
                 on_event=None) -> dict:
    """逐层执行。门槛：handler 返回 → 产物落盘 → 验证器通过，三者齐备才
    释放下游。每节点结束即落盘计划状态（崩溃可续、证据可查）。

    handlers: {capability: callable(pid, node)}，真实能力或测试替身。
    返回 {succeeded, failed, skipped, evidence[]}。
    """
    def emit(kind: str, **kw):
        if on_event:
            on_event({"event": kind, "plan_id": plan.plan_id, **kw})

    issues = validate_plan(plan, project_dir)
    if issues:
        emit("rejected", issues=issues)
        return {"rejected": True, "issues": issues,
                "succeeded": [], "failed": [], "skipped": []}

    report = {"rejected": False, "succeeded": [], "failed": [], "skipped": [],
              "evidence": []}
    for level in topo_levels(plan):
        for nid in level:
            node = plan.node(nid)
            if node.status == "skipped":
                continue
            ok = False
            for attempt in range(node.max_retries + 1):
                node.status = "running"
                emit("node_start", node=nid, attempt=attempt)
                try:
                    handlers[node.capability](plan.pid, node)
                except Exception as e:
                    node.evidence = f"handler 异常: {e}"
                else:
                    vok, ev = VALIDATORS[node.validator](project_dir, node)
                    node.evidence = ev
                    if vok:
                        ok = True
                        break
                emit("node_retry", node=nid, attempt=attempt,
                     evidence=node.evidence)
            if ok:
                node.status = "succeeded"
                report["succeeded"].append(nid)
                emit("node_ok", node=nid, evidence=node.evidence)
            else:
                node.status = "failed"
                report["failed"].append(nid)
                report["evidence"].append({"node": nid, "evidence": node.evidence})
                emit("node_failed", node=nid, evidence=node.evidence)
                _skip_descendants(plan, nid, report)
            save_plan(plan, project_dir)  # 每节点后持久化状态
    return report


def _skip_descendants(plan: Plan, nid: str, report: dict) -> None:
    """失败节点的下游整支跳过（产物未验证通过，不得释放下游）。"""
    changed = True
    while changed:
        changed = False
        for n in plan.nodes:
            if n.status == "pending" and any(
                    plan.node(d).status in ("failed", "skipped", "escalated",
                                            "budget_stopped")
                    for d in n.depends_on):
                n.status = "skipped"
                report["skipped"].append(n.id)
                changed = True


# ---------- 真实能力 handler（CLI / 图编排执行计划时用） ----------

def _shot_idx(node: PlanNode) -> int:
    return int(node.id.rsplit("_", 1)[1])


def default_handlers() -> dict:
    """把能力名映射到既有能力函数。懒加载避免与能力层产生导入环。"""

    def h_storyboard(pid: str, node: PlanNode) -> None:
        from pipeline import storyboard as sb
        sb.create_storyboard(pid=pid, feedback=node.inputs.get("feedback"))

    def h_shot_image(pid: str, node: PlanNode) -> None:
        from pipeline import storyboard as sb
        sb.regenerate_images(pid, {_shot_idx(node): node.inputs.get("feedback", "")})

    def h_tts(pid: str, node: PlanNode) -> None:
        # 旁白修改：先把新文案回写 script.json，再只合成该镜音频（走网关缓存）
        from gateway import core as gw
        pdir = PROJECTS_DIR / pid
        sp = pdir / "script.json"
        script = json.loads(sp.read_text(encoding="utf-8"))
        idx = _shot_idx(node)
        shot = next(s for s in script["shots"] if int(s["idx"]) == idx)
        if node.inputs.get("new_text"):
            shot["narration"] = node.inputs["new_text"]
            sp.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        gw.call("tts", {"text": shot["narration"],
                        "out_path": str(pdir / _aud(idx))}, project_id=pid)

    def h_align(pid: str, node: PlanNode) -> None:
        from pipeline import storyboard as sb
        sb.align_audio(pid)

    def h_shot_video(pid: str, node: PlanNode) -> None:
        from pipeline import videos
        r = videos.batch_generate_videos(pid, only_shots=[_shot_idx(node)])
        if r["failed"]:
            raise RuntimeError(f"镜头视频生成失败: {r['failed_idx']}")

    def h_edl(pid: str, node: PlanNode) -> None:
        from editing import edl as edl_mod
        edl_mod.build_edl(pid)

    def h_render(pid: str, node: PlanNode) -> None:
        from editing import edl as edl_mod
        from editing import ffmpeg
        edl = edl_mod.build_edl(pid)
        ffmpeg.render_edl(edl, PROJECTS_DIR / pid / "final" / "draft.mp4")

    return {"storyboard": h_storyboard, "shot_image": h_shot_image,
            "tts": h_tts, "align": h_align, "shot_video": h_shot_video,
            "edl": h_edl, "render": h_render}


def apply_duration_decision(plan: Plan, project_dir: Path) -> None:
    """时长调整执行前置：按 meta.dropped_shots 从 script.json 移除被删镜头
    （原文件备份为 script.full.json，可回滚）。EDL/渲染随即只处理保留镜头。"""
    if plan.kind != "duration_adjust" or not plan.meta.get("dropped_shots"):
        return
    sp = project_dir / "script.json"
    script = json.loads(sp.read_text(encoding="utf-8"))
    backup = project_dir / "script.full.json"
    if not backup.exists():
        backup.write_text(sp.read_text(encoding="utf-8"), encoding="utf-8")
    dropped = set(plan.meta["dropped_shots"])
    script["shots"] = [s for s in script["shots"] if int(s["idx"]) not in dropped]
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
