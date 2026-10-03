# evals/cases · 评测任务集说明

> 对应 `AGENT_EVOLUTION_PLAN.md` §2.2。30 个任务：开发集 20 / 留出集 10。
> 本目录只存小型用例定义与评分规则；大型媒体 fixture 按需 gitignore。

## 文件划分（按场景家族）

| 文件 | 场景 | dev | holdout |
| --- | --- | --- | --- |
| `regular_creation.yaml` | 常规创作 | 3 | 1 |
| `context_constraints.yaml` | 上下文与约束 | 4 | 2 |
| `local_edit.yaml` | 局部修改 | 4 | 2 |
| `failure_recovery.yaml` | 失败与恢复 | 5 | 3 |
| `quality_budget.yaml` | 质量与预算 | 4 | 2 |

## 防泄漏规则

- 同一家族内，dev 与 holdout 的选题/素材必须不同源（不同题材、不复用 fixture）
- 同一任务的参数改写版不得跨集合
- fixture 项目快照按 `case_id` 独立复制，每个用例从独立快照开始

## 字段语义

| 字段 | 含义 |
| --- | --- |
| `case_id` | 家族前缀-序号：rc/cc/le/fr/qb |
| `set` | `dev` 开发集 / `holdout` 留出集 |
| `goal` | 任务目标（一句话） |
| `topic` | 选题；null 表示由 fixture 项目决定 |
| `skill` | 期望选中的 Skill；`any` 不限制；`none` 期望回退自决 |
| `initial_assets` | 初始素材引用（fixture 路径）；null = 从零开始 |
| `interactions` | 交互轮次脚本（确认/修改意见/取消），空 = 全自动 |
| `key_facts` | 必须注入且来源可追溯的关键事实（上下文类用例） |
| `hard_constraints` | 硬约束清单（验收逐条核） |
| `allowed_scope` | 允许修改范围：`full` 或具体镜头/字段 |
| `fault_injection` | 故障注入点与方式；null = 无 |
| `expected.baseline` | 当前（升级前）系统的预期表现：`deliver` / `stop` / `escalate` / `unsupported` |
| `expected.final` | 升级完成后的预期表现 |
| `verifiers` | 验收器清单（确定性优先；runner 实现后逐条对应到可执行检查） |
| `budget_cny` | 单次运行预算上限（含失败重试；日熔断 ¥15 仍为硬约束） |

## 计分口径（与计划 §4.2 对齐）

- `expected.*: deliver` → 计入视频交付完成率分母
- `stop` / `escalate`（预算不足、重复失败、取消等正确停止）→ 只计入预期行为通过率，不计入交付完成率
- `unsupported`（基线无入口）→ 基线轮计未通过，另报原有能力子集
