# OneTake（一条过）

端到端 AI 视频创作 Agent 工作台 + 其下的小型 AI 基础设施平台。输入选题，Agent 自动完成「大纲 → 脚本 → 分镜 → 素材生成 → 配音 → 自动剪辑 → 成片」，中间节点可查看、可修改、可重生成。

> 当前进度：**P0 奠基与最小管线已完成**（2026-08-05）——固定文案一条命令产出 60s 草稿片，所有外部 API 真实打通。路线与验收见 `TODO.md`，架构与双 JD 定位见 `prd.md`。

## 快速开始

```bash
# 1. 环境：Python 3.13 + uv；FFmpeg 需要含 libass 的 full 版（烧字幕用）
brew install ffmpeg-full        # 或自备含 libass 的静态构建
uv sync

# 2. 配置密钥
cp .env.example .env            # 填入 ARK_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY

# 3. 一条命令出片
uv run python main.py run --script examples/demo.txt
# 产物：projects/{pid}/final/draft.mp4（约 60s，含配音与硬字幕）

# 4. 成本报表
uv run python main.py report                 # 全部调用
uv run python main.py report -p <pid>        # 单项目
```

`--video-shots N`：前 N 个镜头用 Seedance 真实视频生成，其余镜头用分镜图 Ken Burns 推近填充（视频按 token 计费较贵，这是草稿档控成本的核心取舍）。

## 架构要点（P0 现状）

- `gateway/`：模型网关（P4 服务化为 HTTP API）。所有外部模型调用的唯一入口——统一 `call(task_type, payload, tier)`、计费日志写 `generations` 表、日预算硬熔断 `DAILY_BUDGET_LIMIT=15`；`pricing.py` 是全项目单价唯一事实源
- `pipeline/linear.py`：线性版管线（刻意无 LangGraph/调度器，P3/P4 再加），文件级幂等（产物存在即跳过）
- `db/`：SQLite 八表 schema 一次到位（projects/shots/generations/jobs/events/model_perf_daily/skills/memories）
- `editing/ffmpeg.py`：FFmpeg 封装。字幕烧录依赖 libass，**必须用 full 版 FFmpeg**，路径走 `FFMPEG_PATH` 环境变量

## 成本

P0 实测（2026-08-05）：DeepSeek 分镜 ¥0.0076/次，Seedream 图 ¥0.20/张，edge-tts ¥0。单条 60s 草稿片（10 图 + 配音 + 字幕）≈ ¥2.1。详见 `uv run python main.py report` 与 DEVLOG.md。
