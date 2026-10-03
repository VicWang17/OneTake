# 基线冻结记录 · 2026-10-03

> 对应 `AGENT_EVOLUTION_PLAN.md` §2.1「冻结环境与数据」。本记录是静态冻结（未跑任何真实调用，¥0）。
> 后续所有对照实验以本记录为环境参照点；环境若再变化，另存新冻结记录，不修改本文件。

## 1. 代码与依赖

| 项 | 值 |
| --- | --- |
| Git SHA | `b97a6b820d8344e729c7a8239a8727b347ed239d`（`docs: 精简工作守则与任务清单表述…`） |
| 工作区状态 | 干净，仅未跟踪文件 `AGENT_EVOLUTION_PLAN.md`（本次冻结入库后转为已跟踪） |
| Python | CPython 3.13.2（解释器路径 `/usr/local/Caskroom/miniconda/base/bin/python3`，miniconda base） |
| 依赖锁定 | `uv.lock` 在位；冻结时 `.venv` 不存在，`uv run` 全新安装 69 个包（686ms）——说明本机是**重建过的环境** |
| 包管理 | uv，索引见 `pyproject.toml`（清华镜像） |

## 2. 硬件与系统工具

| 项 | 值 |
| --- | --- |
| 架构 | **x86_64**（Intel Mac） |
| Homebrew | 前缀 `/usr/local`（非 arm64 的 `/opt/homebrew`） |
| FFmpeg | **缺失**：`brew list` 无 ffmpeg/ffmpeg-full，`/usr/local/bin` 无 ffmpeg/ffprobe |

**与 AGENTS.md §3 的冲突**：守则记载「一律使用 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`（2026-08-04 实测核实）」，该结论来自另一台 arm64 机器，**在当前机器上不成立**。代码层 `FFMPEG_PATH` 默认值指向一个不存在的路径。当前状态下任何涉及本地合成/烧字幕的管线运行都会失败。此为本轮基线的已知环境阻塞项，处理决策（在本机 `/usr/local` 装 ffmpeg-full，或更新 `FFMPEG_PATH`）留待需要真实运行管线前做出，并同步更新 AGENTS.md 的环境约定。

## 3. 模型与配置（事实源：`serving/registry.yaml`，数据时点 2026-08-06 实测）

- **LLM**：`deepseek-v4-flash`（主力 w100，USD $0.14/$0.28 per Mtok，高峰 2 倍计费）→ 备胎 `qwen3.7-flash`（w0，思考型需剥 reasoning_content，json 模式要求 prompt 含"json"）
- **文生图**：`doubao-seedream-4-0-250828`（w100，¥0.20/张）→ 备胎 4.5（¥0.25）；5.0 Pro（¥0.30）质量档待命
- **视频**：`doubao-seedance-2-0-fast-260128`（w90，¥14/Mtok，实测 ¥0.71/5s 480p）↔ `doubao-seedance-2-0-260128`（w10 灰度，¥46/Mtok）；mini 为 standby（占位价未核实）
- **VL 质检**：`qwen3-vl-flash`（百炼免费额度内 ≈¥0）
- **TTS**：edge-tts（免费，瞬断重试 ≤3）；火山 TTS 未接入
- **Skill 版本**：`knowledge_explainer_v1.yaml` v1.0.0、`movie_commentary_v1.yaml` v1.0.0
- **预算**：日熔断 `DAILY_BUDGET_LIMIT=15`（`.env.example`）；项目总充值 ≤¥300
- **降级路径**：网关 FALLBACKS 从注册表派生；降级结果不写 idem_key；缓存为内容寻址（sha256 收敛在网关，指纹只含语义参数）
- **Prompt 版本**：散落于 `nodes/*.py` 内联，无独立版本号——后续若改 prompt 需在评测记录中注明 git SHA 区分

## 4. 入口（两条路径分别记录，能力不合并计算）

| 入口 | 命令 | 说明 |
| --- | --- | --- |
| 线性（默认） | `uv run python main.py run --topic "..."` | 六步编排 `pipeline/endtoend.py`；`--pid` 断点续跑；`--auto` 低干预 |
| 图版 | `uv run python main.py run --topic "..." --graph` | LangGraph 七节点 + SqliteSaver + 两处 interrupt |

其他命令：`report` / `stats` / `analyze` / `jobs` / `memory`。

## 5. §2.1 完成度对照

- [x] 记录 Git SHA、工作区差异、Python 与依赖版本、FFmpeg 版本、运行命令 —— 本文件 §1/§2/§4（FFmpeg 为「缺失」的如实记录）
- [x] 记录模型标识、参数、Prompt 和 Skill 版本、价格口径、最大预算及所有降级路径 —— 本文件 §3
- [x] 分别记录默认线性入口和 `--graph` 入口 —— 本文件 §4
- [ ] 复制测试用 SQLite 与项目素材，保存输入文件哈希 —— 待 §2.2 任务集建立后按用例执行
- [ ] 明确冷缓存、热缓存两组实验 —— 待评测运行器实现时落实
- [ ] 先冻结任务、标注和评分器，再修改 Agent 逻辑 —— 进行中约束：本冻结之后、任务集冻结之前不改 Agent 决策逻辑

## 6. 已知限制

1. 本机无 FFmpeg，媒体类真实运行暂不可行（见 §2 冲突说明）。
2. 模型标识与价格为 2026-08-06 口径，距今约两个月，真实运行前需按 DEVLOG 005 方法重新核实（`GET /models` + 价格页）。
3. 各平台 API Key 与余额状态未在本步验证（静态冻结范围外）。
