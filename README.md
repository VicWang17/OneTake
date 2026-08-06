# OneTake（一条过）

端到端 AI 视频创作 Agent 工作台 + 其下的小型 AI 基础设施平台。输入选题，Agent 自动完成「大纲 → 脚本 → 分镜 → 素材生成 → 配音 → 自动剪辑 → 成片」，中间节点可查看、可修改、可重生成。

> 当前进度：**P2 端到端 v1 已完成**（2026-08-06，里程碑）——选题一句话 → 大纲 → 分镜 → 角色锚点 → 分镜图 → 真视频 → EDL 渲染，一条命令产出 60s 成片（含配音/硬字幕/BGM 闪避），两处人工确认 + 幂等断点续跑。路线与验收见 `TODO.md`，架构与双 JD 定位见 `prd.md`。

## 快速开始

```bash
# 1. 环境：Python 3.13 + uv；FFmpeg 需要含 libass 的 full 版（烧字幕用）
brew install ffmpeg-full        # 或自备含 libass 的静态构建
uv sync

# 2. 配置密钥
cp .env.example .env            # 填入 ARK_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY

# 3. 端到端：选题一句话 → 60s 成片（约 8 分钟，~¥8）
uv run python main.py run --topic "为什么人睡觉会做梦"
uv run python main.py run --topic "..." --auto     # 跳过人工确认，全自动
uv run python main.py run --pid <pid>              # 断点续跑（增量扣费，零重复）

# 4. 成本报表
uv run python main.py report                 # 全部调用
uv run python main.py report -p <pid>        # 单项目
```

`--video-shots N`：前 N 个镜头用 Seedance 真实视频生成，其余镜头用分镜图 Ken Burns 推近填充（视频按 token 计费较贵，这是草稿档控成本的核心取舍）。

## 架构要点

- `gateway/`：模型网关（P4 服务化为 HTTP API）。所有外部模型调用的唯一入口——统一 `call(task_type, payload, tier)`、计费日志写 `generations` 表、日预算硬熔断 `DAILY_BUDGET_LIMIT=15`；`pricing.py` 是全项目单价唯一事实源
- `nodes/`：能力节点（P1 起）——outline（大纲+LLM 自选风格项目级锁定）、storyboard（分镜表，四层校验+错误回灌自修复）、character（角色设定表文本锚点）
- `pipeline/`：编排层。`linear.py`（P0 固定文案管线）、`storyboard.py`（P1 选题→分镜包管线），文件级幂等（产物存在即跳过）
- `db/`：SQLite 八表 schema 一次到位（projects/shots/generations/jobs/events/model_perf_daily/skills/memories）+ 轻量迁移
- `editing/ffmpeg.py`：FFmpeg 封装。字幕烧录依赖 libass，**必须用 full 版 FFmpeg**，路径走 `FFMPEG_PATH` 环境变量

## 成本

实测（2026-08-05）：DeepSeek 大纲/分镜 ¥0.001–0.02/次，Seedream 图 ¥0.20/张，edge-tts ¥0。P0 单条 60s 草稿片 ≈¥2.1；P1 单个选题完整分镜包（大纲+分镜+锚点+9 图+对齐）≈¥1.9。详见 `uv run python main.py report` 与 DEVLOG.md。
