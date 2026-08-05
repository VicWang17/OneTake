# DEVLOG · OneTake 开发经验与踩坑记录

> 规则（见 AGENTS.md §6）：凡开发过程中遇到的非平凡问题——报错、环境坑、文档与现实不符、选型纠结——都记录在此。
> 每条按「现象 → 排查 → 根因 → 解决 → 经验」五段式，注明日期。这是技术复盘长文和面试问答的一手素材库。

## 2026-08-04 · 开工前核实阶段

### 001 本机 ffmpeg 烧不了字幕

- **现象**：`ffmpeg -vf "subtitles=t.srt"` 报 `Unknown filter 'subtitles'`
- **排查**：`ffmpeg -filters | grep subtitles` 为空 → `brew deps ffmpeg | grep libass` 为 0 → 确认 homebrew 官方把 ffmpeg 拆成 lite（默认）与 `ffmpeg-full` 两个 formula，lite 版无 libass/freetype
- **根因**：`subtitles`/`ass` 滤镜由 libass 库实现，lite 构建未编译该依赖
- **解决**：`brew install ffmpeg-full`（bottled 免编译），共存方案——项目内用 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`，代码走 `FFMPEG_PATH` 环境变量，全局 lite 版不动。中文字体用系统自带 Hiragino Sans GB，烧录实测通过
- **经验**：① "装过 ffmpeg" ≠ "ffmpeg 能用"，功能验证要落到具体 filter；② 副坑：ffmpeg-full 的依赖升级了 x265（.215→.216），导致 lite 版动态链接失效，`brew upgrade ffmpeg` 修复——共享库版本漂移是共存方案的固有成本；③ 开源复现需在 README 提供备选路径（Martin Riedl arm64 静态构建）

### 002 火山方舟 API Key 401 三层排查

- **现象**：方舟 API 调用全部 401 `AuthenticationError: the API key or AK/SK in the request is missing or invalid`
- **排查**：① 逐字节比对密钥字符串（`wc -c` 对比，一致）→ 排除复制错误；② 确认端点与鉴权方式（`Bearer` + `ark.cn-beijing.volces.com/api/v3`，与官方一致）→ 排除调用方式；③ 测试 ID / Secret / ID.Secret 三种 Bearer 组合均 401；④ 检查 key 的「修改权限」，发现权限范围只有「智能处理」「Viking AI 搜索」两个与方舟无关的 scope → 定位到 **key 建错了页面**
- **根因**：key 在「火山引擎统一密钥管理页」创建（那里只有其他产品线的 scope），而方舟专属 API Key **只能在方舟控制台内**（console.volcengine.com/ark → API Key 管理）创建。正确 key 为 `ark-` 前缀格式，错误 key 为点号分隔长串
- **解决**：方舟控制台内重建 key，验证通过
- **经验**：① **401 三层排查法**：凭证字符串 → 凭证类型/鉴权方式 → 凭证权限范围，由表及里各有验证手段；② 云平台两级凭证体系（IAM AK/SK 签名鉴权 vs 产品级 API Key Bearer 鉴权）不可混用；③ 厂商会把权限不足报成 401 而非 403（安全混淆），排查时别被状态码字面意思带偏；④ 网关适配层设计启示：把厂商混合错误码翻译成内部统一错误类型，避免上层误判重试

### 003 edge-tts 瞬断

- **现象**：首次调用 edge-tts 报 `NoAudioReceived`，产出 0 字节文件；重试即成功
- **根因**：edge-tts 是非官方接口（走微软 Edge 大声朗读服务），存在瞬断/限流
- **解决**：调用层封装 ≤3 次重试；备选火山 TTS（¥1–5/万字符，一条 60s 配音约 ¥0.1–0.5）
- **经验**：免费/非官方依赖必须假设其不可靠，重试与降级预案在第一次接入时就写好，而不是挂了再补

### 004 文档滞后：PRD 模型 ID 与 draft 策略现形记

- **现象**：PRD 写的 `doubao-seedance-1-5-pro-250428` 在真实账号的 `GET /models` 中不存在；实际 ID 为 `-251215` 且状态 **Retiring**；同时发现 Seedance 2.0 已改按 token 计费（单价约为 PRD 口径 6–8 倍）、2.5（`doubao-seedance-2-5-260628`）已上线
- **解决**：以真实账号模型清单为准更新 pricing 与策略；草稿档改为 P0 实测对比可灵 2.5 Turbo / seedance 2.0 mini/fast 后定
- **经验**：所有模型选型结论必须用真实账号验证，文档和第三方文章永远滞后——"P0 实测回填"机制就是这个思想的制度化；`GET /models` 是零成本的选型核实手段，接入新平台第一天就该跑

### 005 开工前价格核实的方法论

- **做法**：动手前花半天联网核实四家平台的最新模型 ID、价格、免费额度，结果证伪/修正了 PRD 的四处假设（Seedance 计费方式、DeepSeek 模型名、百炼额度口径、可灵赠点）
- **经验**：PRD 里的价格表本质是"写作时刻的快照"，必须标注数据时点并留回填机制；核实时优先官方价格页，第三方整理只作线索——第三方普遍滞后且互相抄

### 006 可灵 API 采购门槛 → 供应商策略调整

- **现象**：注册可灵开放平台后发现 API 不支持在线充值，控制台提示"如需购买，请联系客服团队"——只适合走商务采购的大客户，不适合 ¥300 小额个人项目
- **决策**：暂缓接入。降级链与灰度演示改用方舟内多版本互备（seedance 2.0 ↔ 2.0-mini/fast ↔ 2.5）；跨供应商机制演示由 DeepSeek ↔ Qwen 承担（LLM 侧不受影响）；可灵作为"已调研待接入"供应商保留适配器位（其 JWT 鉴权差异已在设计中预留）
- **经验**：① 供应商调研的评估维度不只是价格和能力，**获取门槛**（采购流程、最小充值、实名要求）同样决定可用性；② 多供应商架构的价值不在于真的接入 N 家，而在于任何一家都可插拔——设计不锁定，可灵随时能插回来；③ 坦承"视频供应商单点"风险并写入风险表（方舟多版本互备 + 必要时走客服采购），比假装有多供应商更专业
