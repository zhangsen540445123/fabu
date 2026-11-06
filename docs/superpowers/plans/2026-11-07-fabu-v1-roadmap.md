# Fabu v1 Implementation Plan — 开发总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在用户Windows电脑实现单条YouTube视频下载、可编排处理、原封面/话题准备和三平台立即或平台原生定时发布，且不增加付费服务。

**Architecture:** 单用户本地网页与串行Worker共享SQLite持久化状态。下载器、媒体计划编译器、渲染器和发布适配器分离；发布仅消费冻结并验证的成片清单，先仿真后受控实测。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、SQLite、React/TypeScript/Vite；候选yt-dlp、固定并记录版本的FFmpeg/ffprobe与yxer；pytest、Vitest、Playwright为拟采用的测试工具。依赖的具体可用版本在F01锁定，不把计划编写日的包名当成已安装或兼容证据。

**Spec:** `D:\auto-deploy\docs\superpowers\specs\2026-11-07-fabu-v1-design.md`（第3.4节及第10节已完成独立设计审阅）。

**状态/日期:** 2026-11-07，已编制、待用户确认执行。本次只写计划；未安装新依赖、未创建产品代码、未上传或投稿。用户已认可列表式流水线；设计中剩余工程建议通过本计划明确化，不把要求列计划解释为真实发布授权。

## Global Constraints

- 不增加付费 API、订阅、云服务器或运行时大模型费用；遇到付费要求停止，不代购。
- 同时支持立即发布与**平台原生定时**；三平台默认同一北京时间。定时是提前提交给平台，而非到时由本机启动上传。
- 三平台使用同一成片、发布标题、YouTube 原封面和话题选组规则；B 站分类“生活经验”。
- 后台通过参数数组调用固定版本工具，禁止把标题、URL 拼进 shell 命令。
- 程序仅绑定127.0.0.1；原声、原构图及CRF20基准为默认；所有附加编辑关闭。
- 核心参数：MP4、HEVC Main/yuv420p、libx265 fast、CRF20、30fps、Lanczos；双声道AAC192kbps、单声道128kbps，保留受支持的44.1/48kHz采样率。
- 横屏内接2560×1440；竖屏内接1440×2560；方形1440×1440；考虑SAR/rotation；显式裁切/布局才覆盖默认构图。
- 用户声明素材已授权不等于平台“自制/自行拍摄”，也不自动勾选独家/首发。来源声明未明确时阻止提交。
- CLI免费上传、投稿和平台原生预约仍待验证；客户端免费额度、schema字段存在或CLI返回ok均不能替代端到端证据。
- 首次可能的提交后冻结共享快照；任一目标FAILED/UNKNOWN即暂停后续新提交；未知结果先核对，不盲目重投；已发出的批量请求无法保证撤回。
- 不做隐形扰动、MD5指纹注入、伪造来源、查重通过率；不默认GUI自动化、私有API、云发布或cookie读取回退。
- 本计划中的代码路径是未来拟建路径。当前22项旧跟踪文件的未暂存删除不得在计划阶段恢复或混入提交；当前只新增本目录4份计划。

## Review Focus

以下五项容易在正常路径以外出错；各自已归属具体测试任务，不留给最终人工碰运气。

1. 中文/emoji/单引号/`[]`/冒号路径及文本不成为shell或过滤器语法，错误日志不泄露凭证——F02、M07、P02。
2. Windows休眠、崩溃、双击启动或SQLite锁争用不能造成双Worker或重复投稿——F04、P03、P08。
3. 90/270度旋转、非方形像素、奇数边长与布局/裁切组合不得颠倒方向或伸缩——M01、M04、M05。
4. 上传/提交超时后平台可能已经接收；预约时间在处理中跨过截止窗口，不得直接重试或改成立即——P03、P05、P06。
5. 预览后素材变化、源文件被移动或损坏、磁盘填满、预设换版本；不能用旧预览批准新成片或发布残片——F05、M03、M09、P01。

---

## 1. 交付拆分与开发顺序

这是一个产品，拆成三个可单独测试的子计划，避免把媒体处理、平台接入和基础运行耦合成一个巨型任务。

| 子计划 | 任务 | 独立交付物 | 文件 |
|---|---|---|---|
| A 基础与能力门槛 | F01–F06（6项） | 本机诊断、只读能力报告、持久化任务、基础网页 | `D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-foundation.md` |
| B 下载与媒体流水线 | M01–M10（10项） | URL到成片、预设编辑、小样确认、本地渲染 | `D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-media-pipeline.md` |
| C 发布与整体验收 | P01–P08（8项） | 三平台仿真、受控真实验证、Windows交付 | `D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-publishing.md` |

**合计24个任务。** 每项均有明确文件、上下游接口、失败测试、最小实现、通过测试和限定文件提交步骤。技术实现的时间估计不作为交付承诺；F03后根据真实阻碍及M03全片性能重新评估。

| 里程碑 | 顺序与依赖 | 你能看到的结果 | 通过条件 |
|---|---|---|---|
| S0 风险先查 | F01→F02→F03 | 工具与账号能力报告、免费权益证据表 | 不猜路径，不泄密；发现已知收费硬限制就停止该路线 |
| S1 可运行底座 | F04→F05→F06 | 本地网页、任务列表、设置、持久化 | 重启不丢任务；双启动不重复执行；无真实发布入口 |
| S2 基础视频闭环 | M01→M02→M03 | URL下载+原封面+已认可参数的本地成片 | 验证方向/比例/声音，源不被覆盖，失败不产假成片 |
| S3 可编排引擎 | M04→M05→M06→M07→M08 | 所有首版处理模块及组合测试 | 参数/顺序/作用域可解释，异常组合处理前拒绝 |
| S4 流水线界面 | M09→M10 | 勾选/排序/参数/素材/预设/预览确认 | 改预设需新确认，正常换源可复用，不逐条多一次确认 |
| S5 三平台仿真 | P01→P02→P03→P04 | 标题选话题、账号分类、立即/预约表单、状态看板 | 不联网写入即可验证成功/失败/UNKNOWN/恢复 |
| S6 受控真实验证 | P05→P06 | 免费立即/平台原生预约的真实证据 | 必须先授权具体媒体、账号、方式、时间、次数；证据不足保持未验证 |
| S7 交付 | P07→P08 | 启动包、安装检查、说明与实机验收报告 | 本机可复现，安全/恢复/费用/发布路径全部通过，否则仅标明部分交付 |

本计划默认按上述里程碑串行验收，避免提前消费尚未实现的跨模块接口；离线发布fixture可先准备，但P01正式验收仍依赖M09。这里不自动派代理。建议同一会话逐项实施，若用户选代理驱动，再按依赖和不重叠写入范围派发。

## 2. 开工前的版本与仓库边界

- [ ] 读取设计、总计划和当前子计划；核对最新git diff，绝不从“仓库曾为空”推断当前没有变更。
- [ ] 用户确认计划及执行方式后，按using-git-worktrees在执行时选隔离工作区；此处不创建工作区。
- [ ] 当前基准提交为`767aa80`。它仍包含当前工作区已删除的旧实现；若隔离工作区由该提交建立，应先明确复现旧实现退场范围，不把旧的GUI限定方案当成新产品底座。不得反向恢复或提交源工作区的22项删除。
- [ ] 若实施根目录改变，统一替换本计划所有`D:\auto-deploy`前缀，不改媒体/凭证等外部路径，不对原工作区执行移动或清理。
- [ ] 每项先写测试观察其针对行为失败，再实现、回归和提交。测试失败须是对应未实现行为，不是任意环境坏掉。默认仿真不得产生网络写入。
- [ ] 仅`git add -- <本任务明确文件>`；禁止`git add -A`混入旧删除、凭证和视频。无自动推送/合并/真实投稿。

## 3. 目录职责图

所有路径均为拟建/拟重建，按任务逐步加入；不先创建整套空模块。

| 绝对路径 | 职责/任务 |
|---|---|
| `D:\auto-deploy\src\fabu\settings.py`、`D:\auto-deploy\src\fabu\tools.py` | 路径、工具版本、命令执行 F01–F02 |
| `D:\auto-deploy\src\fabu\domain.py`、`D:\auto-deploy\src\fabu\store.py`、`D:\auto-deploy\src\fabu\worker.py` | 核心类型、SQLite、队列/恢复 F04 |
| `D:\auto-deploy\src\fabu\assets.py`、`D:\auto-deploy\src\fabu\presets.py` | 不可变素材/预设与删除引用检查 F05/M04/M09 |
| `D:\auto-deploy\src\fabu\api\`、`D:\auto-deploy\frontend\src\` | 受保护本地API与页面 F06/M10/P04 |
| `D:\auto-deploy\src\fabu\media\` | 探测、几何、下载、编译、过滤器、渲染、校验、预览 M01–M09 |
| `D:\auto-deploy\src\fabu\publishing\` | 能力、模板、CLI契约、发布状态机、对账 P01–P06 |
| `D:\auto-deploy\tests\`、`D:\auto-deploy\frontend\src\__tests__\` | 单元/契约/媒体/端到端测试，按任务建文件 |
| `D:\auto-deploy\scripts\`、`D:\auto-deploy\docs\operations\` | 启停、验收与排障 P07–P08 |

## 4. 跨任务契约（公共名称只在此定义一次）

全部Python输入/输出模型用Pydantic校验；ID为UUID字符串；时长用`Fraction`在编译内计算、API/JSON用毫秒整数，禁止浮点累计。散列指SHA-256。类型由对应所有者任务实现，后续任务禁止另造同名不兼容模型。

- **F02** `CommandSpec(executable: Path, args: list[str], cwd: Path, env: dict[str,str], timeout_s: int)`；`CommandResult(exit_code: int, stdout: bytes, stderr: bytes, timed_out: bool)`。输出捕获不等于允许记录原始内容。
- **F03** `CapabilityReport(account_status, upload, immediate, native_schedule, quota, file_limits, checked_at, tool_version, evidence_refs)`：每项为UNKNOWN/SUPPORTED/UNSUPPORTED；仿真不能把远端能力设成SUPPORTED。
- **F04** `JobSpec(source_url, title, preset_version_id, target_ids, publish_mode, scheduled_at_utc)`、`JobSnapshot(id, revision, spec, source_manifest: dict | None, plan_digest, output_manifest: dict | None, state, pause_reason)`（存储层JSON；阶段边界用M02/M03模型严格解码）、`TargetAttempt(id, job_id, revision, target_id, payload_digest, provider_task_id, state)`。
- **F05** `AssetRef(id, sha256, local_path, media_type, size_bytes)`；`StepSpec(id, kind, enabled, scope, params, asset_ids)`定义结构化信封，具体kind/params校验由M04登记；`PresetVersion(id, preset_id, revision, schema_version, steps: list[StepSpec], output: dict)`持久化输出配置，M03/M04用OutputProfile严格校验后才能编译。
- **M01** `MediaInfo(path, sha256, duration_ms, video_stream_index, coded_width, coded_height, sar, rotation_deg, color, audio)`，`audio`可为None，否则记录流索引、采样率、声道、时长；`Size(width, height)`。
- **M02** `SourceManifest(video_id, source: AssetRef, thumbnail: AssetRef, selected_format, fetched_at)`。源封面与成片预览图永不混用。
- **M03** `OutputProfile(codec='libx265', preset='fast', crf=20, fps=30, pixel_format='yuv420p', size_policy='auto')`；`MediaPlan(digest, engine_version, source_hash, stages, output_size, expected_duration_ms, audio_profile, output_profile, asset_hashes)`。
- **M04** `InputBundle(source: MediaInfo, assets: dict[str,AssetRef])`，扩展F05步骤信封的参数校验与编译，不重复定义StepSpec。
- **M03** `RenderCommand`为编译后的`CommandSpec`及受控filter/text文件清单；`OutputManifest(path, sha256, size_bytes, media_info, plan_digest, verified_at)`由校验器生成，渲染器不能自行标已验证。
- **M09** `PreviewManifest(plan_digest, preset_version_id, sample_source_hash, artifact_hashes, representative_windows, engine_compat_version)`、`PreviewApproval(preset_version_id, asset_hashes, output_digest, engine_compat_version, sample_manifest, approved_at)`。
- **P01** `PublishSnapshot(job_id, revision, output: OutputManifest, cover: AssetRef, title, description, topic_tags, source_declarations, target_ids, mode, scheduled_at_utc, digest)`；一旦提交不可改。
- **P02** `PreparedSubmission(snapshot_digest, target_ids, channel, client_binding, payload_path, resource_refs)`；`SubmissionReceipt(provider_task_id, accepted_targets, raw_evidence_ref)`；`ReconcileResult(target_id, state, scheduled_at_utc, platform_post_id, evidence_refs)`。
- **P02** `ExecutionPermit(kind: SIMULATION/CONTROLLED_TEST/PRODUCTION, allowed_targets, snapshot_digest, mode, scheduled_at_utc, max_attempts, expires_at)`。这是工程授权边界模型，不从外部网页/CLI提示自动生成。

接口扩展必须在所有者任务与下游测试同步更改。不要提前实现通用插件平台；模块登记表只登记第10节明确的内置步骤。

## 5. 两级发布验证门槛

**G1：只读/无投稿验证（F03）**：检查本机工具、账号状态、公开/已授权只读schema及权益；确认是否存在已知收费硬门槛。免费上传/投稿计数、临时资源有效期等若必须真实操作才能证明，明确UNKNOWN，允许继续离线开发但不声称接入通过。发现明确付费且不符合约束时，暂停该接入并请求重新选路线。

**G2：逐项受控写入（P05/P06）**：仅在费用边界足够明确且用户授权确切输入后，允许受控测试，不要求提前把待测试能力设为SUPPORTED。真实测试先尝试用户授权的一条最小链路，核对客户端/平台结果和扣次；失败或不明即暂停，不自动加次数。受控测试许可与正式自动模式分开；全部目标/模式验证通过才解锁相应正式模式。

测试不绕开免费限额；一天额度不足可由用户另择测试时间，不自动创建跨日自动任务，也不购买额度。真实预约要记录平台接受时间和最终公开结果；等待跨时段时由用户保持程序运行/继续验收，不把今天看不到最终结果标为完成。

## 6. 测试与交付证据

统一PowerShell命令由F01/F06建立：

- Python：`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests' -m 'not live' -q`
- 前端：`npm --prefix 'D:\auto-deploy\frontend' run test -- --run`
- 类型/构建：`npm --prefix 'D:\auto-deploy\frontend' run typecheck`，`npm --prefix 'D:\auto-deploy\frontend' run build`
- 仿真端到端：`npm --prefix 'D:\auto-deploy\frontend' run test:e2e -- --project=chromium`（F06配置本地服务器；fake模式）
- 实机媒体测试以`media`标记，必须显式指定已核验FFmpeg路径；缺工具应阻塞媒体验收，不得用skip数量充当通过。
- `live`默认拒绝运行，除非有本次确切范围的ExecutionPermit；本计划不提供可直接误执行的真实投稿命令。

每任务证据：测试命令、结果、工具版本、变更文件、提交ID；媒体额外记录输入/输出摘要、参数、时长、解码和帧图；发布额外记录脱敏回执、平台对应记录、额度前后、模式/目标时间和复核时间。私密证据保留本机仓库外，仓库只存合成/脱敏fixture。

## 7. 范围验收索引

| 设计章节 | 实施任务 |
|---|---|
| 1–3 约束/选型/本地架构 | F01–F06、G1/G2 |
| 4 媒体基准与几何/音频 | M01、M03、M05–M08 |
| 5 下载/原封面/输入 | M02、M03 |
| 6 标题话题/账号分类/声明 | P01、P02、P04 |
| 7 立即与平台预约 | P03–P06 |
| 8 冻结/幂等/失败暂停 | F04、M09、P01、P03、P05 |
| 9 安全/凭证/磁盘/启停 | F01–F06、M03、P02、P07–P08 |
| 10 全部流水线与预览 | F05、M04–M10 |
| 11 真实能力和验收门槛 | F03、M03、P05–P08 |
| 12–14 实施自由度/证据/审阅结论 | 本计划公共契约、自检与交付报告 |

## 8. 最终完成标准与当前边界

完整交付必须同时具备：真实下载、完整成片、全部首版可选模块、可用流水线界面、正确三平台元数据、真实免费立即与原生预约、错误恢复/防重复、用户终端实际启动。只有本地处理完成而发布门槛未过，交付报告必须写“本地部分完成，发布未解锁”，不能宣传整链完成。

本次编制自检：24项依赖无环；公共类型只有一个所有者；每条Review Focus有测试任务；每个任务给出RED/GREEN与提交边界；全设计有覆盖索引。测试步骤均是未来要做的任务，本次没有运行产品测试。

执行建议：先由当前会话逐项实施，按里程碑给结果；若用户希望每项由独立代理实现/审阅，再改为代理驱动。无论选哪种，都先确认本计划，不在计划回答后自动开始安装或投稿。

## 9. 计划采用的一手接口参考

核对日期2026-11-07；只用于确定技术接口与验证项，不能据此把账户收费/发布能力标为已实测。实现时对照锁定版本再核验。

- yt-dlp官方README：`https://github.com/yt-dlp/yt-dlp/blob/master/README.md`；结构化格式读取与单条下载，元信息可能含私密字段，按白名单留存。
- FFmpeg官方文档：`https://www.ffmpeg.org/ffmpeg.html`；过滤图在解码/编码之间工作，支持按计划合并兼容操作，不代表所有未来模块只编码一次。
- 蚁小二官方本机通道说明：`https://raw.githubusercontent.com/yixiaoer888/yixiaoer-skill/main/skills/yixiaoer/references/workflows/local-vs-cloud.md`；本机通道须显式指定并保持Client ID/校验/提交一致。
- 蚁小二官方视频发布说明：`https://raw.githubusercontent.com/yixiaoer888/yixiaoer-skill/main/skills/yixiaoer/references/workflows/publish-video.md`；视频/封面分别上传并按schema构造payload。官方文档中的默认或回退建议不覆盖本项目“零新增费用/禁止自动云回退/无授权不投稿”的限制。
