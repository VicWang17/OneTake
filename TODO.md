# OneTake · 开发任务清单

> 功能导向、随时可更新。配合 `prd.md`（v2.0，活文档）使用；v1.1 PDF 仅作历史存档。
> 最近更新：2026-08-06 · 当前阶段：**P2 端到端 v1 达成（里程碑：选题一键到成片，JD 职责 1/2/3 闭环）；可进入 P3**

## 使用说明

- 状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成 · `[-]` 取消/降级 · `[!]` 阻塞（注明原因）
- 任务粒度原则：单个任务 ≤ 半天可完成、可独立验收；做大了就再拆。
- 每完成一项随手勾掉并更新顶部「最近更新」；范围变更记入文末「变更日志」。
- 每个 Phase 结束：对照「验收标准」逐项打勾，回填实际成本到总览表，再进入下一阶段。
- 预算硬约束：全项目充值 ≤ ¥300（推荐档 ≤¥150）；日熔断 ¥15/天。阶段超支即触发降级预案（见文末）。
- 双 JD 叙事：P0–P3 + P6–P8 主证据链对 B 站 JD；**P4/P5 是对剪映 AI 架构 JD 的核心周**，不可轻易砍掉（底线见降级预案）。

## 进度总览

| Phase | 目标 | 预算 | 状态 | 实际花费 |
|---|---|---|---|---|
| 开工前核实 | 价格/API/风险前置验证 | ¥0 | ✅ 8/8 完成 | |
| P0 奠基（W1） | 固定文案 → 60s 无声草稿片 | ≤¥30 | ⚠️ 完成（视频模型待开通） | ¥2.21 |
| P1 脚本分镜（W2） | 选题 → 分镜包（script.json + 图） | ≤¥20 | ⬜ | |
| P2 端到端 v1（W3） | 选题一键 → 草稿成片（不可让渡） | ≤¥40 | ⬜ | |
| P3 Agent 化（W4） | LangGraph + 断点续跑 + 成本报表 | ≤¥20 | ⬜ | |
| P4 服务化+调度（W5） | 模型服务层 + 任务调度器 ★剪映JD核心 | ≈¥0 | ⬜ | |
| P5 观测+数据（W6） | 可观测性 + 数据链路 ★剪映JD核心 | ≈¥0 | ⬜ | |
| P6 Skills/记忆（W7） | 2 个 Skill 包 + 风格跨项目复用 | ≤¥30 | ⬜ | |
| P7 质检成片（W8） | VLM 质检 + 3 条成片产出（不发布） | ≤¥40 | ⬜ | |
| P8 开源交付（W9） | 可复现仓库 + 演示视频 + 双 JD 简历条目 | ≈¥0 | ⬜ | |

---

## 开工前核实清单（Day 0）

**2026-08-04 已完成联网核实与本地实测（✅=符合预期 ⚠️=需修正 ❌=阻塞项），结论已写入 prd.md 第 3 章：**

- [x] ⚠️ 火山引擎：Seedream 最新 5.0 Pro（¥0.30/张），预算档继续用 4.0（¥0.20/张）；视频 API 稳定版 Seedance 2.0（`doubao-seedance-2-0-260128`），2.5 约 8/7 开放；**按 token 计费，单价约为 v1.1 口径 6–8 倍**；仅 1.5 pro 有样片模式（Draft）。免费额度：每模型 50 万 token + 协作奖励每日最高返 500 万
- [x] ⚠️ DeepSeek：`deepseek-chat` 已下线，改用 `deepseek-v4-flash`（$0.14/$0.28 每百万 token）；500 万 token 赠送以控制台到账为准；注意高峰时段拟 2 倍计费
- [x] ⚠️ 百炼：免费额度实为每模型**总计** 100 万 token（90 天，仅北京）；VL 质检用 `qwen3-vl-flash`，LLM 降级用 `qwen3.7-flash`
- [x] ⚠️ 可灵：按秒计费（2.5 Turbo 无声 720P ¥0.3/s，比 Seedance 2.0 便宜）；**API 新用户赠点未查到官方政策**，注册后控制台实测
- [x] ✅ edge-tts 实测可用（v7.2.8 中文配音跑通），**但有瞬断，调用层必须重试 ≤3 次**；备选火山 TTS ¥1–5/万字符
- [x] ✅ **FFmpeg 字幕烧录（已解决，2026-08-04）**：本机 homebrew `ffmpeg` 是 lite 构建无 libass。已按共存方案安装 `brew install ffmpeg-full`（v8.1.2，含 libass/fontconfig/freetype，keg-only 不影响全局 lite 版），烧录实测通过（Hiragino Sans GB 中文字幕 + 描边渲染正常）
  - **项目约定：本项目的 ffmpeg/ffprobe 一律用 `/opt/homebrew/opt/ffmpeg-full/bin/` 下的 full 版，代码层走 `FFMPEG_PATH` 环境变量（默认值即此路径），全局 lite 版不动**
  - 注意事项：ffmpeg-full 的依赖会升级共享库（本次 x265 .215→.216 曾使 lite 版 ffprobe 动态链接失效，`brew upgrade ffmpeg` 后即修复）；今后 brew 升级后若 lite 版报错，升级对应包即可
  - 开源复现路径（README 用）：方案 A 装 ffmpeg-full；方案 B 项目内置 Martin Riedl arm64 静态构建（ffmpeg.martin-riedl.de，含 libass）
  - 中文字体已确认：Hiragino Sans GB / STHeiti / Songti
- [x] ✅ BGM 方案：首选 **Pixabay Music**（免费商用、无需署名）。**已决策不公开发布，版权无硬约束**，但 README 演示录屏属公开传播，仍建议优先 Pixabay；FMA 须逐曲核 CC；国内素材站不作首选
- [x] ✅ B 站 AIGC 规则（备查）：投稿须勾选「该视频使用人工智能合成技术」创作声明（2025-09 起法规义务）。**本项目已决策不公开发布，此条仅在未来改变主意时适用**

---

## Phase 0 · 奠基与最小管线（W1，预算 ≤¥30）

目标：所有外部依赖真实打通；最笨的线性代码证明「文案 → 视频」链路成立。**本周不写任何架构。**

### 0.1 仓库与环境
- [x] 建仓：`pyproject.toml`、`.env.example`、`.gitignore`（含 `projects/`、`.env`），目录按 prd.md 附录 A（uv 工程，2026-08-05）
- [x] Python 环境 + 依赖（openai SDK、火山方舟 SDK、edge-tts、typer、python-dotenv、requests；FastAPI/uvicorn 推迟到 P4 serving 周再装）
- [x] FFmpeg 环境接入：代码层 ffmpeg/ffprobe 路径走 `FFMPEG_PATH`（默认 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`，见 `editing/ffmpeg.py`）；硬字幕烧录随 0.5 合成项一并实测
- [x] `.env` 统一管理密钥，加载工具函数（python-dotenv，`gateway/adapters.py` 顶部 load_dotenv）

### 0.2 平台注册（充值策略：首批 ≤¥150，先耗尽免费额度再充）
- [~] 火山引擎：✅ API Key 已验证可用（2026-08-04，正确路径：方舟控制台 console.volcengine.com/ark → API Key 管理；踩坑记录：统一密钥管理页建的 key 调方舟必 401）。待办：实名认证确认、开通 Seedream + Seedance 模型服务、**首充 ¥20–50**、确认 Seedream 按张计费是否在 token 免费额度内、控制台查看"超额后付费"、设消费告警。**已确认模型清单：`doubao-seedream-4-0-250828`（草稿图）、`doubao-seedream-5-0-pro-260628`（质量档）、`doubao-seedance-2-0-260128`（主力视频）、`doubao-seedance-2-5-260628`（已上线）、`deepseek-v4-flash-260425`（方舟侧 LLM 互备）均可用；注意 `doubao-seedance-1-5-pro-251215` 状态 Retiring，草稿档样片模式策略需在 P0 重定**
- [x] DeepSeek：✅ key 已验证可用（2026-08-04）。**实测赠送余额 ¥0（"注册送 500 万 token"未对本账号兑现）**，账号已有充值余额 ¥8.77——按测算（单条视频 LLM 成本 <¥0.1）足够撑完整个项目，**无需再充**；已知高峰时段拟 2 倍计费
- [x] 阿里百炼：✅ key 已验证可用（2026-08-04），`qwen3.7-flash` / `qwen3-vl-flash` 均在列表且推理调用实测通过。**注意：qwen3.7-flash 是思考型模型，响应带 `reasoning_content` 字段，网关适配层需剥离**；免费额度每模型 100 万 token（90 天，北京），不用充值
- [-] 可灵开放平台：**暂缓接入（2026-08-04）**——已注册，控制台确认 API 不支持在线充值、需联系客服采购，不适合 ¥300 小额预算。降级/灰度演示改用方舟内多版本（seedance 2.0 ↔ 2.0-mini/fast ↔ 2.5）；可灵作为已调研待接入供应商保留适配器位，后续有批量需求再走客服采购
- [ ] Pixabay Music 下载 5–10 首 BGM，建 `assets/bgm/` 与来源清单

### 0.3 数据层
- [x] `db/schema.sql`：projects / shots / generations / jobs / events / model_perf_daily / skills / memories 八表一次到位（2026-08-05）
- [x] `db/dao.py`：最小 CRUD（projects / shots / generations + today_spend 供熔断）

### 0.4 网关骨架（本周为内部库形态，P4 服务化）
- [x] 统一入口 `call(task_type, payload, tier)`（`gateway/core.py`，透传 + 日志）
- [x] 计费日志写 generations 表（模型/档位/用量/单价/成本，成功失败都记）
- [x] 日预算熔断 `DAILY_BUDGET_LIMIT=15`（超上限抛 BudgetExceededError）
- [x] `pricing.py`：按开工前核实结论填单价表（注明 2026-08-04 数据时点；seedance fast/mini 为占位价待 0.6 实测回填）

### 0.5 各能力单点打通
- [x] DeepSeek：文案 → 合法 JSON（10 个分镜，narration 拼接与原文逐字一致，单次 ¥0.0076）
- [x] Seedream：第一张分镜图落盘 `projects/{pid}/shots/`（1280x720，¥0.20/张，内容符合 prompt）
- [-] Seedance：第一段 5s 草稿视频（创建 → 轮询 → 下载全流程）——**延期至 P2 开工前**：账号未开通任何视频模型，且开通需 ¥200 底额充值，决策推迟（DEVLOG 010）；前期全图卡模式测试。调用代码（`gateway/adapters.py::seedance_video`）已就绪，开通后 `--video-shots N` 复验
- [x] edge-tts：第一段中文配音 mp3 + ffprobe 真实时长（重试封装已就位，本次一次成功）
- [x] FFmpeg：视频段 + 音频 + 硬字幕 + BGM 合成 60s 样片（63.3s 草稿片实测：图卡 Ken Burns + AAC 配音 + Hiragino Sans GB 硬字幕；`assets/bgm/` 暂无文件，BGM 混音接口已留好）

### 0.6 供应商实测对比（成本策略的决策依据，剪映 JD"实验到上线"叙事的第一环）
- [-] Seedance 2.0 与 2.0-mini/fast 各跑一条 5s 样片（同 prompt 同参考图），查实测单价与质量——**延期至 P2 开工前**（同 0.5，开通需 ¥200 底额），开通后优先补做
- [!] 记录实测单价、耗时、质量对比，定草稿档主力模型——同上阻塞；pricing.py 中 fast/mini 为占位价，实测后回填
- [ ] 回填单条成本目标到 prd.md 表 1-4 与本文件 P2/P7 验收标准

### 0.7 串联
- [x] `uv run python main.py run --script examples/demo.txt` 一条命令产出 `projects/p20260805-150630/final/draft.mp4`（63.3s，854x480，含配音+硬字幕；`--video-shots N` 控制真实视频镜头数）
- [x] `report` 命令雏形：按环节/模型汇总次数与成本 + 当日预算水位（支持 `-p <pid>` 单项目）

### P0 验收标准
- [x] 一条命令产出 60 秒可播放草稿片（63.3s，抽帧目检字幕/画面/音轨正常）
- [x] report 能列出各环节成本
- [-] 供应商对比结论落档（写进 prd.md 3.6）——延期至 P2，随 Seedance 开通一并完成
- [x] 本周总花费 ≤¥30（实际 ¥2.21）
- [x] 红线变体已关闭：非代码问题而是账号未开通 + 开通需 ¥200 底额；决策延期至 P2（DEVLOG 010），P0 主交付物由全图卡草稿片保住

---

## Phase 1 · 脚本与分镜生成（W2，预算 ≤¥20）✅ 已完成（2026-08-05，实际 ¥7.5）

目标：输入选题，自动产出「大纲 → 脚本 → 分镜表 → 分镜图」，两处人工确认（确认交互经决策延后至 P2）。

### 1.1 大纲生成
- [x] 大纲 Prompt（style 由 LLM 按选题自行设计，固化为项目级常量——"LLM 选型，项目内锁定"）——`nodes/outline.py`，实测微波炉选题产出 5 段大纲（¥0.0013）
- [x] 大纲落库（script.json + projects 表 style_json，含 `_migrate` 老库迁移）

### 1.2 分镜表
- [x] 分镜表 JSON Schema（idx/duration/narration/visual_prompt/camera/purpose）——`nodes/storyboard.py`
- [x] 四层校验器（语法→结构→业务→一致性，错误清单带 位置/现状/期望）
- [x] 错误回灌重试（多轮对话式 ≤3 次；网关 llm 支持 messages 多轮模式，回灌照常计费）
- [x] 分镜写入 shots 表——实测 8 镜头 60s 一次通过校验（¥0.024）

### 1.3 角色一致性（基础手段）
- [x] 角色设定表生成——`nodes/character.py`，落 script.json（"月牙小梦"/"小波"/六边形眼镜蜜蜂，均贴合选题）
- [x] character_sheet 拼入出图 prompt（注入点集中在 create_images，原始分镜数据不改写）

### 1.4 分镜图产出（确认交互延后）
- [x] 分镜图批量生成（经网关，落盘规范命名，文件级幂等）
- [-] CLI 逐张确认（y/n/r）——**延后到 P2 端到端集成时补**（2026-08-05 决策），本周全自动通过
- [x] 参考图链：首镜新图 URL 作为后续镜头参考图（adapters/gateway 透传 `reference_url`；URL 时效边界已注释，P3 缓存升级时处理）
- [-] 打回修改意见记录——随确认交互一并延后（P6 记忆系统数据源相应后移）

### 1.5 台词时长对齐
- [x] TTS 真实时长回写 shots 表（duration 从预估值覆写为实测值）
- [x] 对齐规则：±20% 容忍带 → 台词改写（≤2 次）→ 仍越界标记 align=audio 以音频为准
- [x] 超长台词自动改写 Prompt（带具体时长目标）——`rewrite_narration`

### P1 验收标准
- [x] 3 个不同选题均产出结构合法的分镜包（微波炉 8 镜 / 做梦 9 镜 / 蜂巢 9 镜，script.json 四键齐全 + 图 + 音频）
- [x] 分镜图质量抽看：做梦 1/5/9 镜与蜂巢 1/7 镜，角色造型与色调高度一致；发现瑕疵 2 类（图内乱入文字、背景乱码字符）→ 留作 P7 VLM 质检靶样本。抽看通过率约 90%（26/28 张无可见问题）
- [x] 单选题成本 ≤¥3（实际 ¥1.9–2.5/选题），本周 ≤¥20（实际 ¥7.5，含 P0 已花 ¥2.2 合计 ¥9.7）

---

## Phase 2 · 端到端 v1（W3，预算 ≤¥40）★ 项目最重要里程碑

目标：选题一键到草稿成片（含配音/字幕/BGM）。本周末项目具备简历最小可用形态。

**开工前提（W3 第一天）**：充值 ¥200 底额并开通 Seedance 视频模型（2.0/2.0-fast/2.0-mini 三个），复验 0.5 单点 + 补做 0.6 供应商对比（DEVLOG 010）

### 2.0 延期项补做
- [x] 充值 ¥200 底额 + 开通 Seedance 2.0/fast/mini（控制台，2026-08-06）
- [x] 复验 Seedance 单点（创建 → 轮询 → 下载全流程）：5s 480p 一次成功，耗时 150.7s，50638 tokens，实测 ¥2.31 与官方口径完全一致（`projects/p0-probe/clips/seedance_probe.mp4`，抽帧目检画面符合 prompt）
- [x] 0.6 供应商对比：同 prompt 5s 480p 三家实测——token 数相同（50638），成本差异全在单价：2.0 ¥2.31（150.7s）/ fast ¥0.71（93.2s）/ mini 单价待账单核实；三片目检均合格。**决策：草稿档主力 = 2.0-fast（省 69%）**，pricing.py 已改 token 优先计费并回填实测价，结论落 prd.md 3.6 + DEVLOG 012

### 2.1 视频生成批量管理（本周为简易队列，P4 迁移到调度器）
- [x] 异步任务队列：并发 ≤5（ThreadPoolExecutor，防限流）——`pipeline/videos.py`
- [x] 轮询 + 指数退避重试（单镜头 ≤3 次，5/10/20s，失败不拖垮全局）
- [x] 失败任务落库可查（video_failed），`--shots 3,7` 单独重跑
- [x] motion_prompt 自动生成（`nodes/motion.py`，三要素：镜头运动/主体动作/节奏；script.json 缓存）
- [x] 图生视频路线探针：base64 首帧实测可行，批量下锚定生效（DEVLOG 013）
- [x] 真实批量验证：蜂巢 9 镜 9/9 成功 ¥6.38（并发 5，约 3 分钟）

### 2.2 EDL 时间线
- [x] EDL JSON 结构定义（视频轨/语音轨/字幕轨/BGM 轨）——`editing/edl.py`，落盘 `final/edl.json`
- [x] 粒度对齐：5s 出片 vs 台词 4–8s → 短则慢放补齐（setpts，speed 0.75–0.83 实测无感）、长则裁剪；无视频镜头退回图卡兜底
- [x] TTS 真实时长驱动 EDL（时间轴唯一事实源=音频）

### 2.3 FFmpeg 合成器
- [x] 分辨率/帧率归一化 + concat 拼接（P0 已有，EDL 化改造 `render_edl`）
- [x] SRT 生成 → 硬字幕烧录（抽帧验证字幕与镜头对齐）
- [x] BGM 混音：人声闪避（sidechaincompress 侧链压缩，ratio=8，release=300ms）

### 2.4 端到端集成
- [x] `main.py run --topic "..."` 全流程（六步编排 `pipeline/endtoend.py`）+ `--pid` 断点续跑 + `--auto` 低干预模式
- [x] 两处人工确认补上：脚本确认（y/n/r，r 带意见全量重生成）、分镜图确认（打开目录 + 镜头号打回带意见重画 + 旧视频作废）
- [x] 端到端真实验证（彩虹选题，10 镜 56.3s 成片 ¥9.11）：**日预算熔断真实触发**（shot 10 被拦，三机制协同）→ 提额后 `--pid` 幂等续跑，增量成本精确 ¥0.71（DEVLOG 014）

### P2 验收标准
- [x] 选题 → 60s 草稿成片一键产出（彩虹 56.3s，含确认点的话术流程已备）
- [x] 耗时 ≤30 分钟（全自动 ~8 分钟；含视频生成的续跑仅 1.5 分钟）
- [x] 成本达标（10 镜 ¥9.11 ≈ 回填目标 ¥8 的口径差 1 镜；9 镜约 ¥8.4 达标）
- [x] 声画同步无可见错位（40s 抽帧：画面/字幕/台词三者对齐）
- [x] **里程碑达成：B 站 JD 职责 1/2/3 最小闭环成立**

---

## Phase 3 · Agent 化与成本工程（W4，预算 ≤¥20）

> ⚠️ 节前提醒：`.env` 里 `DAILY_BUDGET_LIMIT=9999` 是手动调试期的临时放宽（2026-08-06 用户要求），**P3 开工第一件事：恢复为 15**（3.1 幂等缓存验收时顺便复核熔断仍生效）

目标：从「脚本能跑」升级为「系统可信」。
**原则：线性版保留为主路径，LangGraph 并行重构、回归验证后转正。**

### 3.1 幂等缓存（先做，独立受益）
- [ ] `idem_key = sha256(model + prompt + params + tier)`，命中直接返回文件路径
- [ ] 文件存在性校验（记录在但文件被删不算命中）
- [ ] 缓存命中率统计与展示

### 3.2 LangGraph 重构
- [ ] `pipeline/state.py`：TypedDict 共享状态（project/shots/memory/skill）
- [ ] `pipeline/graph.py`：StateGraph，节点 = 现有能力函数
- [ ] SQLite checkpointer：进程 kill 后断点恢复
- [ ] 两处 interrupt 人工节点（脚本确认、分镜确认）移植进图
- [ ] 回归验证：同一选题，图版与线性版产物一致

### 3.3 成本报表
- [ ] `report` 完整版：按项目/环节/档位输出明细与单条成本

### 3.4 失败降级
- [ ] 降级链：Seedance 2.0 ↔ 2.0-mini/fast（同平台跨档位）、DeepSeek ↔ Qwen（跨供应商，机制演示主力）；可灵保留适配器位（暂缓）
- [ ] 互备演练：人为制造失败，验证切换生效

### P3 验收标准
- [ ] 中途 kill 3 次，恢复后零重复扣费且产物一致
- [ ] 缓存命中率 ≥70%
- [ ] report 输出单条成本报表

---

## Phase 4 · 模型服务化 + 任务调度（W5，≈¥0）★ 剪映 JD 核心周 I

目标：模型调用走服务、异步负载走调度——从「工具」变「平台」。
**本周全是本地工程，近零 API 成本；是岗位 B 叙事的地基，优先级高于 P5/P6。**

### 4.1 模型注册表
- [ ] 注册表 YAML Schema（name/provider/capability/tier/单价/免费额度/并发上限/fallback_to/状态）
- [ ] 加载器 + 热更新（改 YAML 即上下架模型，演示录屏素材）
- [ ] 已接入的 6 个模型全部录入注册表

### 4.2 Serving 服务
- [ ] FastAPI app：`POST /v1/chat/completions`、`/v1/images/generations`、`/v1/videos/generations`（异步任务式）、`GET /v1/models`、`GET /health`
- [ ] 厂商适配器（deepseek/ark/kling/qwen）迁移到 serving 内部，网关四机制（路由/缓存/计费/降级）随迁
- [ ] 灰度路由：按百分比分流（如 seedance:kling = 90:10）
- [ ] provider 熔断：连续失败断开 + 半开探测恢复
- [ ] 管线节点全部改为 HTTP 调 serving（回归：同一选题产物与 P3 一致）

### 4.3 任务调度器
- [ ] jobs 表状态机：pending → running → succeeded/failed → dead
- [ ] asyncio worker 池 + 按 provider 并发限流（读注册表配置）
- [ ] 指数退避 + jitter 重试 ≤3 次，超限进死信
- [ ] 优先级：交互 > 批量，final > draft
- [ ] worker 崩溃恢复：running 回滚 pending；轮询型任务凭 task_id 续查不重复提交
- [ ] 视频生成任务从 P2 简易队列迁移到调度器
- [ ] CLI：`jobs list / stats / retry / cancel`

### P4 验收标准
- [ ] kill worker 后恢复：无任务丢失、无重复扣费
- [ ] 灰度演示：视频生成 10% 流量切 seedance 2.5（新版本灰度，正对"实验到上线"叙事），注册表热更新生效
- [ ] 管线节点 100% 经 serving 调用（grep 确认 nodes/ 无厂商 SDK import）
- [ ] 批量提交 3 条视频任务经调度器完成，含 ≥1 次失败重试成功

---

## Phase 5 · 可观测性 + 数据链路（W6，≈¥0）★ 剪映 JD 核心周 II

目标：系统可观测、数据可沉淀、决策有依据。

### 5.1 可观测性
- [ ] 结构化 JSON 日志：ts/level/trace_id/node/job_id/model/cost/latency
- [ ] trace_id 贯穿：编排节点 → 调度任务 → serving 调用全链路透传
- [ ] metrics 落库：调用量/成功率/时延/成本/缓存命中，按 model/tier/provider/day 维度
- [ ] `onetake stats` 仪表盘：今日/累计成本、燃烧速率、各环节成功率、模型对比、队列深度、熔断状态
- [ ] 告警：预算燃烧/失败率/队列积压/熔断 → CLI 显著警告

### 5.2 数据链路
- [ ] events 事件日志接入：generation / eval / job 三类（publish 类 P7 补）
- [ ] 每日聚合任务（调度器驱动）→ model_perf_daily / skill 效果表
- [ ] `onetake analyze`：模型质量-成本对比报告 + 失败原因分布
- [ ] 失败模式挖掘 → 规避性提示词回写经验记忆（与记忆系统闭环）
- [ ] 风险治理轻量版：审核失败落库 + Skill 敏感题材 blocklist

### P5 验收标准
- [ ] 单次运行可用 trace_id 串起全部节点与任务
- [ ] stats 一屏看全系统状态（成本/成功率/队列/熔断）
- [ ] 聚合报告真实产出（Seedance 2.0 vs 2.5/mini-fast 对比，复验 P0 结论；可灵接入后纳入对比）
- [ ] ≥1 次数据驱动决策记录（写进 prd.md 附录 C 答题素材）

---

## Phase 6 · Skills 与记忆系统（W7，预算 ≤¥30）

目标：创作方法论资产化。时间不足时本阶段可压缩为演示级。

### 6.1 Skill 系统
- [ ] Skill YAML Schema + 加载器 + 注册表
- [ ] Skill `knowledge_explainer_v1.yaml`（知识科普解说）
- [ ] Skill `movie_commentary_v1.yaml`（影视片段解说）
- [ ] Skill 选择器：LLM 按选题自动匹配；`--skill` 强制指定

### 6.2 记忆系统
- [ ] memories 表启用：profile / episode 两类
- [ ] 提取管线：确认节点修改意见 → LLM 归纳 → 人工确认写入画像记忆
- [ ] 经验记忆接通 P5 的失败模式挖掘回写
- [ ] 记忆合并：同主题 LLM 归并去重、更新置信度
- [ ] 记忆注入：新项目启动时 Top-K 记忆进入大纲/分镜 prompt

### 6.3 批量验证（经任务调度器）
- [ ] 批量模式：同一 Skill 连出 3 条视频，任务经调度器提交
- [ ] 录屏留证：「第二次创作自动应用第一次的风格偏好」

### P6 验收标准
- [ ] 风格复用演示录屏
- [ ] 批量 3 条总成本 ≤¥20，stats 可见队列与成功率
- [ ] Skill 切换产出风格差异肉眼可辨

---

## Phase 7 · 质检与成片闭环（W8，预算 ≤¥40）

目标：质量自评 + 成片产出，把「做过」升级为「可验证、有数据」。**不公开发布**（2026-08-04 决策：开发者已有视频创作经验，无需以此证明；画质标准降为 480p）。

### 7.1 VLM 质检
- [ ] 抽帧工具：每条镜头视频抽 3 帧
- [ ] `qwen3-vl-flash` 双维度评分（语义一致性/画面质量，各 1–5）
- [ ] 低分自动重生成（任一 <3，≤2 次，第 2 次附失败原因）
- [ ] 仍不通过 → 标记人工介入
- [ ] 质检结果进 events + 质量报表（一次通过率、重试原因分布）

### 7.2 成片渲染
- [ ] 成片档渲染：质检通过 + 人工放行的项目重渲 720p（idem_key 含 tier，草稿缓存不覆盖成片）

### 7.3 成片产出与演示物料
- [ ] 产出 ≥3 条 480p 成片（覆盖至少 2 个选题方向，留存 `projects/` 作为演示素材）
- [ ] 演示物料（轻量）：每片生成 1 个标题 + 封面图（用于 README 展示，非发布用途）
- [ ] 成片质检数据回写数据链路（eval 事件 → model_perf / skill 效果表；无 publish 事件，skill 效果以 VLM 质检分为代理指标）

### P7 验收标准
- [ ] 质检真实拦截并自动重生成 ≥1 个镜头（留日志）
- [ ] 3 条成片产出（480p，本地留存可播放）
- [ ] 成片档单条成本 ≤ P0 回填的目标值（480p 标准，应显著低于原 720p 口径）
- [ ] 终稿 VLM 一次通过率 ≥80%

---

## Phase 8 · 开源与双 JD 简历交付（W9，≈¥0）

- [ ] README：双 JD 定位、五层架构图、demo 链接、成本数据、快速开始（30 分钟可复现验证一遍）
- [ ] 3 分钟演示视频：选题 → 确认节点 → 成片 → stats 仪表盘 → 灰度切换 → 成本报表
- [ ] 技术复盘长文一篇（B 站专栏/掘金）：两档生成、幂等缓存、调度器/服务化取舍、数据驱动模型选型
- [ ] 双 JD 简历条目定稿（prd.md 附录 B.1/B.2 + 真实数据回填）
- [ ] 面试问答清单自测（prd.md 附录 C，重点演练 SQLite 调度器选型、服务化价值、实验到上线三题）
- [ ] 代码清理：密钥外置确认、依赖锁定、issue 模板、`.env.example` 完整
- [ ] 回填「计划 vs 实际」成本对比到 prd.md 表 5-1

---

## Backlog（Could 档，主线完成后按余力做）

- [ ] 角色锚点条件化：分镜加 `has_character` 标记；sheet 拆「角色锚/风格锚」两段；参考图链分级（DEVLOG 016——吉祥物 vs 叙事主角）
- [ ] 素材语义搜索：本地素材库 BGE/CLIP 向量检索（CPU）
- [ ] 角色一致性增强：IP-Adapter 类方案调研
- [ ] 简易 Web 界面（优先做只读观测面板，服务 stats 数据）
- [ ] 记忆系统升级向量检索
- [ ] Skill 效果度量驱动的 Skill 推荐
- [ ] serving 层迁移到独立机器部署（架构已预留此路径）

## 风险触发器与降级预案

| 触发条件 | 预案 |
|---|---|
| Seedance 接口 2 天调不通 | 切 2.0-mini/fast 或降 480p，不阻塞（P0 红线） |
| edge-tts 失效/限流（已实测有瞬断） | 调用层带 ≤3 次重试；持续失败切火山 TTS（¥1–5/万字符） |
| FFmpeg 无法烧字幕（本机已确认） | `brew install ffmpeg-full`（官方完整构建，含 libass）替换 lite 版；或项目内置 Martin Riedl arm64 静态构建；代码层 ffmpeg 路径走 `FFMPEG_PATH` 环境变量 |
| 视频生成单价高于 v1.1 测算（已确认 6–8 倍） | 草稿档 seedance 2.0-mini/fast 或 480p 档；P0 实测后回填成本目标 |
| 视频供应商单点（可灵暂缓，仅方舟） | 低 | 方舟内多版本（2.0/mini/fast/2.5）互备；可灵已调研并预留适配器位，必要时走客服采购快速接入 |
| 某 Phase 预算超支 | 后续阶段降级：P6/P7 压缩为演示级 |
| 时间不足（课业冲突） | 砍 Scope 顺序：P2 > P3 > **P4（服务化+调度，剪映 JD 底线）** > P5 > P6 > P7 |
| 视频人物崩坏严重 | 选材硬约束：扁平插画/物件/风景类镜头，避开人物大场景 |
| 日账单异常 | 网关 ¥15/天熔断 + 平台侧消费告警 |

## 变更日志

- 2026-08-04 初版：基于 PRD v1.1 拆分；补充前置核实项
- 2026-08-04 完成开工前核实（联网 + 本地实测），结论回填；发现 FFmpeg 阻塞项
- 2026-08-04 **v2.0 对齐 prd.md**：新增目标岗位 B（剪映 AI 架构）；新增 P4（模型服务化+任务调度，W5）与 P5（可观测性+数据链路，W6）两个阶段；原 P4–P6 顺延为 P6–P8；P0 新增供应商实测对比任务（0.6）；数据层 schema 一次到位（含 jobs/events/model_perf_daily）；降级预案加入剪映 JD 底线说明
- 2026-08-05 **P0 完成**：仓库骨架（uv 工程）、八表数据层、网关（统一入口/计费/日熔断/单价表）、DeepSeek/Seedream/edge-tts/FFmpeg 四项真实打通，一条命令产出 63.3s 草稿片（`projects/p20260805-150630/final/draft.mp4`），实际花费 ¥2.21。范围微调：① 草稿片采用「分镜图 Ken Burns 填充 + `--video-shots N` 控制真实视频镜头数」的混合策略（视频按 token 计费贵的成本控制手段）；② 0.5 Seedance 单点与 0.6 供应商对比因账号未开通视频模型（控制台人工动作）标记阻塞，非代码问题，开通后复验；③ FastAPI/uvicorn 推迟到 P4 serving 周再装
