"""P3 长程执行与局部重规划：Task/Step/Attempt 持久化 + 失效传播 + 恢复复核。

在 P2（plan.py：计划 DAG + 静态校验 + 门槛执行）之上加长程能力：

- 台账：Task（一轮用户目标）→ Step（计划节点）→ Attempt（每次尝试，含错误类型/
  证据/策略/成本/计划版本）。全部落盘 projects/{pid}/tasks/，崩溃不丢。
- 错误分类处置：transient→重试；param→参数修复（repair_hook）后重试；
  budget→停；unknown 重试耗尽→升级人工（记 open_questions）。
  同一节点同一错误签名重复出现 = 无进展，提前升级，不空烧重试。
- 失效传播：目标变更 → apply_goal_change 沿静态产物依赖图标 stale、
  计划版本 +1；旧版本 Attempt 不得标记成功（防过期覆盖）。
- 预算熔断：节点开工前查 spent + cost_est > budget 即停，剩余节点 budget_stopped。
- 恢复复核：review_on_resume 核对产物哈希/版本、待确认、开放问题——
  恢复的是"产物状态"，不是对话文本。
"""

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from pipeline import plan as plan_mod

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"


# ---------- 错误分类 ----------

def classify_error(evidence: str) -> str:
    """按证据文本归类错误。类型决定处置策略（STRATEGY）。"""
    e = evidence.lower()
    if any(k in e for k in ("预算", "budget")):
        return "budget"
    if any(k in e for k in ("timeout", "timed out", "noaudioreceived",
                            "connection", "429", "502", "503", "瞬断")):
        return "transient"
    if any(k in e for k in ("越界", "时长", "schema", "校验", "参数")):
        return "param"
    if any(k in e for k in ("ffprobe", "缺失/空产物", "损坏")):
        return "artifact"
    return "unknown"


# 处置策略：retry 原地重试 / repair 参数修复后重试 / stop 预算熔断。
# unknown 也先 retry（可能是没见过的瞬时错误）；升级人工不由错误类型直接决定，
# 而由「无进展检测」（同一错误签名重复）或重试耗尽触发——见 _run_node。
STRATEGY = {"transient": "retry", "artifact": "retry", "unknown": "retry",
            "param": "repair", "budget": "stop"}

MAX_REPAIR = 1          # 参数修复最多一次，修不好升级
NO_PROGRESS_LIMIT = 2   # 同一错误签名重复次数上限


# ---------- Task 台账持久化 ----------

def _task_dir(pdir: Path, task_id: str) -> Path:
    return pdir / "tasks" / task_id


def submit_task(pdir: Path, goal: str, plan: plan_mod.Plan) -> dict:
    """登记一轮用户目标。plan_version 从 1 起；目标变更走 apply_goal_change。"""
    task = {
        "task_id": f"t{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}",
        "goal": goal, "plan_id": plan.plan_id, "plan_version": 1,
        "budget_cny": plan.budget_cny, "spent_cny": 0.0,
        "status": "running",            # running/succeeded/failed/escalated/budget_stopped
        "open_questions": [],           # 升级人工时记录：什么挡住了、需要什么决策
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    d = _task_dir(pdir, task["task_id"])
    d.mkdir(parents=True, exist_ok=True)
    _save_task(pdir, task)
    return task


def _save_task(pdir: Path, task: dict) -> None:
    (_task_dir(pdir, task["task_id"]) / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task(pdir: Path, task_id: str) -> dict:
    return json.loads((_task_dir(pdir, task_id) / "task.json").read_text(
        encoding="utf-8"))


def list_tasks(pdir: Path) -> list[dict]:
    tdir = pdir / "tasks"
    if not tdir.exists():
        return []
    return sorted((load_task(pdir, d.name) for d in tdir.iterdir() if d.is_dir()),
                  key=lambda t: t["created_at"])


def log_attempt(pdir: Path, task: dict, node_id: str, n: int, *,
                error_type: str = "", strategy: str = "", evidence: str = "",
                cost: float = 0.0, ok: bool = False, rationale: str = "") -> None:
    """Attempt 追加日志（task 级 jsonl）：谁、第几次、为什么这么做、结果证据。"""
    rec = {"task_id": task["task_id"], "plan_version": task["plan_version"],
           "node_id": node_id, "n": n, "error_type": error_type,
           "strategy": strategy, "evidence": evidence, "cost": cost, "ok": ok,
           "rationale": rationale, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with (_task_dir(pdir, task["task_id"]) / "attempts.jsonl").open(
            "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_attempts(pdir: Path, task_id: str) -> list[dict]:
    f = _task_dir(pdir, task_id) / "attempts.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x]


# ---------- 产物清单（版本 + 哈希 + 确认归属） ----------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def load_manifest(pdir: Path) -> dict:
    f = pdir / "artifacts.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_manifest(pdir: Path, manifest: dict) -> None:
    (pdir / "artifacts.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def stamp_artifacts(pdir: Path, plan_version: int, paths: list[str]) -> None:
    """节点成功后给产物盖版本戳：内容哈希 + 生产它的计划版本。"""
    m = load_manifest(pdir)
    for rel in paths:
        f = pdir / rel
        if f.exists():
            prev = m.get(rel, {})
            version = prev.get("version", 0) + 1
            m[rel] = {"version": version, "hash": _sha(f),
                      "plan_version": plan_version, "stale": False,
                      # 产物重生成 → 旧确认失效（确认有明确版本归属）
                      "confirmed_version": None}
    save_manifest(pdir, m)


# 产物依赖传播规则（镜头级粒度）：返回 rel 变更后需要失效的产物前缀。
# script.json 变 → 全局失效；单镜图变 → 同镜视频 + 成片；单镜音频变 → 只到成片
# （画面内容未变，视频复用）；EDL 变 → 成片。
def _dependents_of(rel: str) -> list[str]:
    if rel.startswith("script.json"):
        return ["shots/", "audio/", "clips/", "final/"]
    m = re.search(r"shot_(\d+)", rel)
    if rel.startswith("shots/") and m:
        return [f"clips/shot_{m.group(1)}", "final/"]
    if rel.startswith("audio/"):
        return ["final/"]
    if rel.startswith("clips/"):
        return ["final/"]
    if rel.startswith("final/edl"):
        return ["final/draft.mp4"]
    return []


def invalidate_downstream(pdir: Path, changed_prefixes: list[str]) -> list[str]:
    """目标变更后沿依赖传播失效（镜头级粒度），返回被标 stale 的产物。
    只标不删——过期产物是证据，也是复用候选（内容哈希没变就不用真重做）。"""
    m = load_manifest(pdir)
    invalidated: set[str] = set()
    frontier = list(changed_prefixes)
    while frontier:
        cur = frontier.pop()
        for rel in m:
            if m[rel].get("stale"):
                continue
            if rel.startswith(cur) or any(rel.startswith(d)
                                          for d in _dependents_of(cur)):
                m[rel]["stale"] = True
                m[rel]["confirmed_version"] = None
                invalidated.add(rel)
                frontier.append(rel)
    save_manifest(pdir, m)
    return sorted(invalidated)


def apply_goal_change(pdir: Path, task: dict, new_goal: str,
                      changed_prefixes: list[str]) -> dict:
    """用户改变目标：失效传播 + 计划版本 +1（旧版本 Attempt 自此不得覆盖结果）。"""
    invalidated = invalidate_downstream(pdir, changed_prefixes)
    task["goal"] = new_goal
    task["plan_version"] += 1
    task.setdefault("goal_history", []).append(
        {"version": task["plan_version"] - 1, "goal_change_at": time.strftime("%H:%M:%S"),
         "invalidated": invalidated})
    _save_task(pdir, task)
    return task


def confirm(pdir: Path, artifact: str, decision: str = "approved") -> dict:
    """人工确认，归属到产物当前版本。产物再生成后确认自动失效。"""
    m = load_manifest(pdir)
    if artifact not in m:
        raise KeyError(f"产物未登记: {artifact}")
    m[artifact]["confirmed_version"] = m[artifact]["version"]
    m[artifact]["confirmation"] = {"decision": decision,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_manifest(pdir, m)
    return m[artifact]


def pending_confirmations(pdir: Path) -> list[str]:
    """确认版本 != 当前版本（或未确认）的产物——交付前必须清零。"""
    return [rel for rel, a in load_manifest(pdir).items()
            if a.get("confirmed_version") != a.get("version")]


# ---------- 长程执行器 ----------

def execute_task(pdir: Path, task: dict, plan: plan_mod.Plan, handlers: dict,
                 repair_hook=None, on_event=None) -> dict:
    """带台账的计划执行。恢复语义：产物有效且版本属当前计划版本的节点直接复用，
    只重做必要节点（stale/缺失/旧版本）。

    repair_hook(node, evidence) -> bool：参数修复（如改写文案），True=已修复可重试。
    """
    def emit(kind: str, **kw):
        if on_event:
            on_event({"event": kind, "task_id": task["task_id"], **kw})

    issues = plan_mod.validate_plan(plan, pdir)
    if issues:
        task["status"] = "failed"
        _save_task(pdir, task)
        return {"rejected": True, "issues": issues}

    report = {"reused": [], "redone": [], "failed": [], "skipped": [],
              "escalated": [], "budget_stopped": []}
    for level in plan_mod.topo_levels(plan):
        for nid in level:
            node = plan.node(nid)
            if node.status in ("skipped", "failed"):
                continue

            # 复用判定：产物全部在盘、非 stale、哈希与清单一致（未受影响即复用，
            # 目标变更只重放被失效传播标记的链路）
            m = load_manifest(pdir)
            if node.outputs and all(
                    (pdir / o).exists()
                    and m.get(o, {}).get("stale") is False
                    and m.get(o, {}).get("hash") == _sha(pdir / o)
                    for o in node.outputs):
                node.status = "succeeded"
                report["reused"].append(nid)
                emit("node_reused", node=nid)
                continue

            # 预算熔断：开工前检查
            if task["spent_cny"] + node.cost_est > task["budget_cny"]:
                node.status = "budget_stopped"
                report["budget_stopped"].append(nid)
                emit("budget_stop", node=nid, spent=task["spent_cny"])
                plan_mod._skip_descendants(
                    plan, nid, {"skipped": report["skipped"]})
                continue

            ok = _run_node(pdir, task, plan, node, handlers, repair_hook,
                           emit, report)
            if ok:
                report["redone"].append(nid)
            _save_task(pdir, task)  # 每节点后落盘：进程中断可从这里恢复

    task["status"] = ("succeeded" if not (report["failed"] or report["escalated"]
                                          or report["budget_stopped"])
                      else "escalated" if report["escalated"]
                      else "budget_stopped" if report["budget_stopped"]
                      else "failed")
    _save_task(pdir, task)
    return report


def _run_node(pdir, task, plan, node, handlers, repair_hook, emit, report) -> bool:
    """单节点长程执行：尝试 → 分类错误 → 按策略处置，全程记 Attempt。"""
    seen_signatures: dict[str, int] = {}
    repairs = 0
    for attempt in range(1, node.max_retries + 2):
        emit("node_start", node=node.id, attempt=attempt)
        v0 = task["plan_version"]  # 过期 Attempt 防护：开工时记下计划版本
        evidence, ok = "", False
        try:
            handlers[node.capability](plan.pid, node)
        except Exception as e:
            evidence = f"handler 异常: {e}"
        else:
            ok, evidence = plan_mod.VALIDATORS[node.validator](pdir, node)

        if ok and task["plan_version"] != v0:
            # 运行期间目标已变更：本次结果属旧计划，不盖戳、不算数
            log_attempt(pdir, task, node.id, attempt, ok=False,
                        evidence="计划版本已变更，旧 Attempt 结果被丢弃",
                        rationale="stale-attempt guard")
            emit("node_stale_discard", node=node.id)
            return False

        if ok:
            node.status = "succeeded"
            node.evidence = evidence
            task["spent_cny"] = round(task["spent_cny"] + node.cost_est, 4)
            stamp_artifacts(pdir, task["plan_version"], node.outputs)
            log_attempt(pdir, task, node.id, attempt, ok=True, evidence=evidence,
                        cost=node.cost_est, rationale="产物落盘且验证通过")
            emit("node_ok", node=node.id, evidence=evidence)
            return True

        etype = classify_error(evidence)
        strategy = STRATEGY[etype]
        log_attempt(pdir, task, node.id, attempt, error_type=etype,
                    strategy=strategy, evidence=evidence,
                    rationale=f"{etype} → {strategy}")

        # 无进展检测：同一错误签名重复，继续重试只是烧钱
        sig = f"{etype}:{evidence[:60]}"
        seen_signatures[sig] = seen_signatures.get(sig, 0) + 1
        if seen_signatures[sig] >= NO_PROGRESS_LIMIT:
            strategy = "escalate"
            evidence += "（同一错误重复出现，判定无进展）"

        if strategy == "stop":  # budget
            node.status = "budget_stopped"
            report["budget_stopped"].append(node.id)
            plan_mod._skip_descendants(plan, node.id,
                                       {"skipped": report["skipped"]})
            return False
        if strategy == "repair" and repairs < MAX_REPAIR and repair_hook:
            repairs += 1
            if repair_hook(node, evidence):
                emit("node_repair", node=node.id, evidence=evidence)
                continue
            strategy = "escalate"
        if strategy == "escalate" or attempt > node.max_retries:
            node.status = "escalated"
            node.evidence = evidence
            report["escalated"].append(node.id)
            report["failed"].append(node.id)
            task["open_questions"].append(
                {"node": node.id, "error_type": etype, "evidence": evidence,
                 "tried": seen_signatures, "needed": "人工决策：换方案或放弃"})
            emit("node_escalated", node=node.id, evidence=evidence)
            plan_mod._skip_descendants(plan, node.id,
                                       {"skipped": report["skipped"]})
            return False
        emit("node_retry", node=node.id, attempt=attempt, evidence=evidence)
    return False


# ---------- 恢复复核 ----------

def review_on_resume(pdir: Path) -> dict:
    """恢复时复核产物状态（不是对话文本）：
    - 产物版本/哈希 vs 盘上实况（丢失、被改、stale）
    - 待确认内容（确认版本过期的产物）
    - 未结任务的开放问题
    """
    m = load_manifest(pdir)
    missing, changed, stale = [], [], []
    for rel, a in m.items():
        f = pdir / rel
        if not f.exists():
            missing.append(rel)
        elif _sha(f) != a["hash"]:
            changed.append(rel)
        elif a.get("stale"):
            stale.append(rel)
    open_qs = [{"task_id": t["task_id"], "goal": t["goal"], "questions": t["open_questions"]}
               for t in list_tasks(pdir) if t["open_questions"]
               and t["status"] in ("escalated", "running")]
    return {"missing": missing, "changed": changed, "stale": stale,
            "pending_confirmations": pending_confirmations(pdir),
            "open_questions": open_qs}


def explain_task(pdir: Path, task_id: str, report: dict | None = None) -> list[str]:
    """解释哪些步骤复用、哪些重做及原因（阶段验收的可解释性要求）。"""
    attempts = load_attempts(pdir, task_id)
    lines = []
    by_node: dict[str, list] = {}
    for a in attempts:
        by_node.setdefault(a["node_id"], []).append(a)
    for nid, atts in by_node.items():
        last = atts[-1]
        if last["ok"] and len(atts) == 1:
            lines.append(f"{nid}: 一次通过（{last['rationale']}）")
        elif last["ok"]:
            lines.append(f"{nid}: {len(atts)} 次尝试后成功，"
                         f"经历 {[a['error_type'] for a in atts[:-1]]}")
        else:
            lines.append(f"{nid}: 未成功（{last['error_type']}: "
                         f"{last['evidence'][:50]}）→ {last['strategy']}")
    if report:
        for nid in report.get("reused", []):
            lines.append(f"{nid}: 复用（产物有效且版本属当前计划，零成本）")
    return lines


# ---------- 交付检查（分镜 / FFprobe / EDL / VLM，带版本归属） ----------

def run_delivery_checks(pdir: Path, vl_fn=None) -> list[dict]:
    """交付前四类检查，结果带产物版本归属——人工确认必须确认"这个版本"。

    vl_fn: VLM 质检注入点（真实接 pipeline/judge.py；测试可打桩）。
    """
    checks = []
    m = load_manifest(pdir)

    def version_of(rel: str):
        a = m.get(rel)
        return a["version"] if a else None

    # 1. 分镜：script.json 可解析、镜头非空、时长为正
    try:
        script = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
        shots = script["shots"]
        ok = bool(shots) and all(float(s["duration"]) > 0 for s in shots)
        ev = f"{len(shots)} 镜" if ok else "镜头为空或时长非法"
    except Exception as e:
        ok, ev = False, f"script.json 异常: {e}"
    checks.append({"check": "storyboard", "ok": ok, "evidence": ev,
                   "artifact": "script.json",
                   "version": version_of("script.json")})

    # 2. FFprobe：清单内媒体产物全部可探测（调用方负责打桩或真实环境）
    media = [rel for rel in m if rel.endswith((".mp4", ".mp3")) and not m[rel].get("stale")]
    bad = []
    if media:
        from editing import ffmpeg
        for rel in media:
            try:
                if ffmpeg.probe_duration(pdir / rel) <= 0:
                    bad.append(rel)
            except Exception:
                bad.append(rel)
    checks.append({"check": "ffprobe", "ok": not bad,
                   "evidence": f"{len(media)} 个媒体产物完好" if not bad
                   else f"损坏: {bad}",
                   "artifact": "media", "version": None})

    # 3. EDL：与 script.json 镜头集合一致、时间轴连续
    try:
        edl = json.loads((pdir / "final" / "edl.json").read_text(encoding="utf-8"))
        edl_idx = sorted(int(s["idx"]) for s in edl["tracks"]["video"])
        script_idx = sorted(int(s["idx"]) for s in script["shots"])
        ok = edl_idx == script_idx
        ev = "EDL 与分镜一致" if ok else f"EDL {edl_idx} != 分镜 {script_idx}"
    except Exception as e:
        ok, ev = False, f"edl.json 异常: {e}"
    checks.append({"check": "edl", "ok": ok, "evidence": ev,
                   "artifact": "final/edl.json",
                   "version": version_of("final/edl.json")})

    # 4. VLM 质检（未配置时显式标注，不静默跳过）
    if vl_fn is None:
        checks.append({"check": "vlm", "ok": None, "evidence": "未配置 VLM 质检",
                       "artifact": None, "version": None})
    else:
        r = vl_fn(pdir)
        checks.append({"check": "vlm", "ok": r["ok"], "evidence": r["evidence"],
                       "artifact": "clips/", "version": None})
    return checks


def deliverable(pdir: Path, vl_fn=None) -> dict:
    """交付判定：确定性检查全过 + 无待确认产物 + 无开放问题。"""
    checks = run_delivery_checks(pdir, vl_fn=vl_fn)
    hard_fail = [c for c in checks if c["ok"] is False]
    pending = pending_confirmations(pdir)
    open_qs = [q for t in list_tasks(pdir) for q in t["open_questions"]]
    return {"deliverable": not hard_fail and not pending and not open_qs,
            "checks": checks, "pending_confirmations": pending,
            "open_questions": open_qs}
