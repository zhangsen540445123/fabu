# Fabu Publishing Implementation Plan — 三平台发布与Windows交付

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已验证媒体之上完成三平台模板、仿真发布、独立授权的免费立即/原生预约实测与可恢复的Windows交付。

**Architecture:** PublishSnapshot冻结输入，Publisher接口分为Fake与官方CLI本机通道；事务记录先于副作用，结果依据平台证据推进。预检、受控测试许可、正式模式能力门槛分离，不以未知结果自动重试。

**Tech Stack:** Python 3.12、SQLite、Windows用户级凭证保护、yxer官方CLI、本地React页面、pytest/Playwright。

**Spec:** `D:\auto-deploy\docs\superpowers\specs\2026-11-07-fabu-v1-design.md`，重点第6–9/11节。

## Global Constraints

继承`D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-v1-roadmap.md`全部约束与契约。目标是抖音ASMR、头条号ASMR、B站枕畔慢时光；用用户本地配置的稳定ID，不把昵称当唯一标识。来源声明逐平台核对；北京时间平台原生预约；失败/UNKNOWN暂停剩余新提交；不自动收费、云通道回退或重投。P05/P06必须另有具体实测授权，编写此计划不提供该授权。

## Review Focus

- CLI错误、上传URL、配置临时文件包含凭证时不得入日志/Git：P02/P07。
- 超时/崩溃后平台已接收不能重投，双启动不能同时发：P03/P08。
- 媒体几何由M01/M05覆盖；P01只接受匹配任务修订且已验证的OutputManifest。
- 处理延迟使预约太近或过期、CLI接收但平台未预约：P03/P06。
- 新标题/预设/素材版本不得绕过源视频UNKNOWN保护：P01/P03。

---

## 文件与接口边界

模板只生产PublishSnapshot；cli_gateway只负责契约与脱敏；coordinator拥有副作用顺序/停止规则；reconcile拥有证据映射。真实CLI命令名/字段只从已核对版本的帮助/schema取得，计划不猜`query groups`就是个人话题组，也不把schema能力当平台实测结果。

### Task 1: P01 — 话题、三平台字段与冻结发布快照

**依赖：** F03–F05、M03/M09完成后才能接真实成片；提前开发时用严格类型fixture。**Files：** `D:\auto-deploy\src\fabu\publishing\models.py`、`D:\auto-deploy\src\fabu\publishing\topics.py`、`D:\auto-deploy\src\fabu\publishing\templates.py`、`D:\auto-deploy\tests\publishing\test_templates.py`。

**Interfaces：** `select_topic_group(title: str, override_group: str | None) -> TopicSelection`；`build_publish_snapshot(job: JobSnapshot, output: OutputManifest, account_config: dict) -> PublishSnapshot`。接口不调用投稿，TopicSelection零/多命中返回需人工选择状态。

- [ ] RED：`test_title_topic_rules`断言“剃须仙人的日常”→WRHkkYwU、“剃光头”→2aZjVP8u、“沉浸式剪发”→QKHLZMFO；“剃光头与剪发”暂停、无匹配不默认助眠、人工覆盖优先；`test_authorized_does_not_imply_self_shot`拒绝未经语义确认的来源字段；`test_wrong_revision_output_rejected`不消费别的任务成片。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\publishing\test_templates.py' -q`观察RED。
- [ ] 实现标签快照：助眠=[解压,助眠]；2aZjVP8u=[ASMR,解压,助眠,剃光头]；QKHLZMFO=[解压,助眠,ASMR,剪发]；WRHkkYwU=[解压,ASMR,剃须仙人,助眠]。组名不当标签发出；不假设CLI能实时同步个人模板。
- [ ] B站分类从真实查询解析“生活经验”完整路径/ID，歧义暂停；每平台schema映射字段/长度/标签语义、公开/简介默认值可见可改；不自动删改标题/增事实/署名/独家声明。发布前核对output摘要、预览批准与源封面。
- [ ] GREEN：同命令PASS；三平台共享同一output/title/cover/tags语义，字段格式各自适配；首次SUBMITTING后禁止修改共享快照。
- [ ] 提交`feat: build verified platform metadata snapshots`，限定Files。

### Task 2: P02 — 官方CLI网关、凭证与FakePublisher

**依赖：** F02–F03、P01。**Files：** `D:\auto-deploy\src\fabu\publishing\gateway.py`、`D:\auto-deploy\src\fabu\publishing\cli_gateway.py`、`D:\auto-deploy\src\fabu\publishing\fake_gateway.py`、`D:\auto-deploy\src\fabu\publishing\permits.py`、`D:\auto-deploy\src\fabu\credentials.py`、`D:\auto-deploy\tests\publishing\test_gateway.py`、`D:\auto-deploy\tests\test_credentials.py`。

**Interfaces：** `Publisher.preflight(snapshot: PublishSnapshot) -> CapabilityReport`；`prepare(snapshot: PublishSnapshot, permit: ExecutionPermit) -> PreparedSubmission`（可能上传，属于写入）；`submit(prepared: PreparedSubmission, permit: ExecutionPermit) -> SubmissionReceipt`；`reconcile(attempt: TargetAttempt) -> ReconcileResult`。本任务在permits.py定义ExecutionPermit模型及范围校验；P03复用，P05添加受控实测证据流程。

- [ ] RED：`test_default_gateway_never_writes`未许可prepare/submit拒绝且调用数0；`test_local_channel_cannot_fall_back`CLI失败后云通道调用数0；`test_ok_does_not_mean_published`ok只映射provider accepted；`test_secret_roundtrip_no_plaintext_persistence`DPAPI往返正确、临时配置ACL受限且最后删除。运行Python的`test_gateway.py`与`test_credentials.py`观察RED。
- [ ] 实现官方CLI固定实际入口、显式YIXIAOER_CONFIG/PROJECT_DIR/API_URL与本机Client ID；校验/提交显式使用经当前版本核对的`--publish-channel local`和`--client-id`，检查返回实际通道一致；只解析已核实字段，使用受控payload文件，忽略响应内建议自动执行的nextCommand。yxer 3.2.32仅为已有已验证版本，实作必须核对实际安装版本而非自动升级。
- [ ] 凭证经用户显式配置保存于用户级Windows保护存储；需要明文CLI配置时短期生成受限文件，try/finally清理并启动清遗留；错误日志白名单脱敏。FakePublisher可注入成功/失败/延迟/未知，绝不隐式切到真实实现。
- [ ] GREEN：上述测试PASS；Fake覆盖全部接口，真实网关单测用合成CLI响应；恶意nextCommand/含密钥URL/未知字段不执行不泄漏。
- [ ] 提交`feat: isolate local publishing gateway and credentials`，限定Files，测试不得实际上传。

### Task 3: P03 — 事务发布状态机与对账防重

**依赖：** F04、P01–P02。**Files：** `D:\auto-deploy\src\fabu\publishing\coordinator.py`、`D:\auto-deploy\src\fabu\publishing\reconcile.py`、`D:\auto-deploy\src\fabu\publishing\permits.py`、`D:\auto-deploy\src\fabu\migrations\004_publish_attempts.sql`、`D:\auto-deploy\tests\publishing\test_coordinator.py`。

**Interfaces：** 复用P02的ExecutionPermit；`run_submission(job_id: str, permit: ExecutionPermit, publisher: Publisher, clock: Callable[[],datetime]) -> JobSnapshot`；`reconcile_pending(store: JobStore, publisher: Publisher) -> list[ReconcileResult]`。资源上传attempt与投稿attempt分别持久化，不能上传超时就重复创造资源/扣次。

- [ ] RED：`test_failure_stops_remaining_targets`fake第一目标成功第二失败时第三调用数0；`test_unknown_does_not_retry_after_restart`超时后重启仍只submit一次；`test_title_or_preset_change_cannot_bypass_unknown`源ID相同仍阻断；`test_expired_schedule_never_becomes_immediate`时间过期调用数0；`test_batch_receipt_does_not_promise_rollback`已发批量仅记录结果。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\publishing\test_coordinator.py' -q`观察RED。
- [ ] 实现预检全部目标→冻结内容/permit→记录attempt→上传/提交→保存回执→平台对账；接受权利/额度不明则门槛阻断。PROVIDER_ACCEPTED不等于SUBMITTED/SCHEDULED/PUBLISHED；有限查询仍无法唯一匹配就UNKNOWN。
- [ ] 逐目标路线在可证明提交或预约后才推进下一目标，失败/不明暂停；批量路线先验证全部，发出后无原子撤销保证。路线选择必须以真实契约/扣次为依据。已提交不自动重发，明确未提交临时故障才有限重试；恢复重验冻结快照、时间/额度/登录，UNKNOWN未核清不放行。
- [ ] GREEN：同命令PASS，覆盖在记录attempt前/后、网络返回前/后逐点崩溃；唯一平台ID/资源ID/账号/时间证据映射，不能同标题猜匹配；首次提交冻结在并发事务中生效。
- [ ] 提交`feat: coordinate fail-closed publishing and reconciliation`，限定Files。

### Task 4: P04 — 账号、发布表单与三平台状态界面（S5）

**依赖：** F06、M10、P03。**Files：** `D:\auto-deploy\src\fabu\api\publishing.py`、`D:\auto-deploy\frontend\src\pages\AccountsPage.tsx`、`D:\auto-deploy\frontend\src\components\PublishForm.tsx`、`D:\auto-deploy\frontend\src\components\TargetStatus.tsx`、`D:\auto-deploy\frontend\src\__tests__\publish.test.tsx`、`D:\auto-deploy\frontend\tests\publish-fake.spec.ts`、`D:\auto-deploy\tests\test_publish_api.py`。

**Interfaces：** GET `/api/accounts`、POST `/api/publish/preflight`、POST `/api/jobs/{id}/submit`、GET `/api/jobs/{id}/targets`、POST `/api/jobs/{id}/reconcile`；后端校验认证/审批/能力与模式，不能通过前端传`live=true`跳过。

- [ ] RED：`schedule_uses_beijing_not_browser_zone`浏览器非中国时区仍显示/提交Asia/Shanghai对应UTC时间点；`provider_accepted_is_not_published`显示不同状态；`unknown_shows_reconcile_not_retry`无直接重投按钮；`denied_mode_cannot_be_posted_directly`绕过UI仍拒绝。运行前端test及Python`test_publish_api.py`观察RED。
- [ ] 实现账号稳定ID绑定与同名歧义、B站分类选择/来源声明、话题自动命中和手动覆盖、立即/预约时间、三目标状态。展示“已被蚁小二接收/平台已提交/已预约/已公开”，不合并为成功。
- [ ] 单任务正常已解锁路径无需额外弹确认；受控测试、未知结果、过期时间/必填缺失按门槛处理。已有提交任务禁止一键改时间或删除远端内容，提示去平台处理；本地未提交任务可取消/改版本。
- [ ] GREEN：前端test/typecheck/build和`publish-fake.spec.ts`PASS；注入一成一败一未提交，刷新/重启显示正确；外部真实write请求数0。
- [ ] 提交`feat: add publishing forms and per-platform status`，限定Files。

### Task 5: P05 — 受控上传/立即发布实测（G2第一部分）

**依赖：** S4/S5通过、F03报告没有未解决收费风险、用户授权确切媒体/封面/标题/目标账号/立即方式/最大尝试数。**Files：** `D:\auto-deploy\src\fabu\publishing\verification.py`、`D:\auto-deploy\tests\publishing\test_controlled_verification.py`、`D:\auto-deploy\docs\operations\live-verification.md`；必要修改cli_gateway/capabilities，禁止把真实日志或配置加入仓库。

**Interfaces：** `verify_controlled_run(permit: ExecutionPermit, snapshot: PublishSnapshot, evidence: list[dict]) -> CapabilityReport`；`grant_production_mode(report: CapabilityReport, targets: list[str], mode: str) -> bool`。CONTROLLED_TEST只授权测试范围；PRODUCTION只按充分证据开启。

- [ ] RED：`test_controlled_permit_cannot_expand_scope`改账号/文件摘要/标题/方式/次数任一项即拒绝；`test_unknown_write_capability_can_be_tested_but_not_auto_enabled`明确允许有费用边界和人工授权的受控探测，不强求事前SUPPORTED；`test_failed_run_does_not_grant_production`失败/未知不解锁。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\publishing\test_controlled_verification.py' -q`观察RED。
- [ ] 实现证据登记与对账门槛；上传前后、提交前后记录可获取的额度/资源有效期/大小/HEVC接受情况，无法确认的项目保留UNKNOWN。相同资源仅在实际契约允许时复用，不把临时上传假定为素材库或永久资源。
- [ ] GREEN：同命令PASS后，才按用户本次授权执行有限真实链路；先核对全部预检再发，失败/不明立即停止剩余。没有授权只交付测试工具和待办，不实际操作、不使用历史示例暗自投稿。
- [ ] 验收：三个目标分别记录可关联的平台记录、准确元数据/账号/文件/公开状态、真实扣次、失败行为；免费额度不足就停，不自动购买/改日重跑。若HEVC失败，报告并另行确认兼容方案，不暗改H.264。控制程序与用户终端都核对。
- [ ] 提交`feat: verify controlled immediate publishing evidence`，仅代码/合成测试/脱敏操作文档；实测未完成则任务不打勾，不宣称完整接入。

### Task 6: P06 — 平台原生预约实测（G2第二部分）

**依赖：** P05、用户单独确认测试媒体/三目标/Asia/Shanghai具体日期时间/次数；预约窗口/扣次信息足够明确。**Files：** `D:\auto-deploy\src\fabu\publishing\schedule.py`、`D:\auto-deploy\tests\publishing\test_schedule.py`，修改verification/reconcile和`D:\auto-deploy\docs\operations\live-verification.md`。

**Interfaces：** `validate_schedule(targets: list[str], when_utc: datetime, capabilities: CapabilityReport, now: datetime) -> list[ValidationIssue]`；`verify_native_schedule(receipt: SubmissionReceipt, evidence: list[dict], expected_utc: datetime) -> ReconcileResult`。时区显示固定Asia/Shanghai，内部UTC，CLI适配转换为经核实单位的时间戳。

- [ ] RED：`test_one_platform_window_blocks_all_before_submit`任一窗口不满足全阻断；`test_provider_queue_not_native_schedule`仅蚁小二排队不能SCHEDULED；`test_wrong_timestamp_or_account_is_unknown`目标/时间不匹配不能成功；`test_clock_passage_does_not_mean_published`过点不凭时钟设PUBLISHED。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\publishing\test_schedule.py' -q`观察RED。
- [ ] 实现平台窗口交集、提交前二次校验及确定的时间转换，UNKNOWN窗口不能猜；过期不改成立即、不同平台不擅自改不同时间。
- [ ] GREEN：同命令PASS，再按授权范围真实提前提交；核对平台端确切预约记录，而非只看CLI回执。明确记录到哪个阶段以后电脑/客户端可退出；仅有provider队列证据不得宣称可关机。
- [ ] 验收：在授权测试时间观察平台最终结果，区分审核/失败/延迟公开；必要时由用户配合关闭本地程序验证平台托管，不替用户关机。未到测试时间只标“已预约，待公开复核”，不得提前完成本任务。
- [ ] 提交`feat: verify native scheduling and reconcile final status`，仅代码/合成测试/脱敏文档；按已证实的目标/模式解锁，不外推其他平台。

### Task 7: P07 — 安全与可靠性总回归

**依赖：** 全部实现任务；真实门槛未过不阻止做本地安全测试，但阻止最终完整验收。**Files：** `D:\auto-deploy\tests\integration\test_security.py`、`D:\auto-deploy\tests\integration\test_recovery.py`、`D:\auto-deploy\frontend\tests\recovery.spec.ts`、`D:\auto-deploy\docs\operations\security-recovery.md`；缺陷修复只改测试对应责任模块。

**Interfaces：** 消费现有应用/Worker/存储/Publisher公开接口，不加入第二个执行入口。目标是端到端验证边界，而非只调用私有函数。

- [ ] RED：新增`test_cross_site_cannot_import_or_publish`、`test_media_id_cannot_read_credentials`、`test_disk_full_then_restart_no_partial_publish`、`test_shutdown_during_submit_reconciles_without_resend`、`test_secret_not_in_logs_or_db`；用故障注入先观察未覆盖行为失败，若已满足记录既有保护证据。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\integration' -m 'not live' -q`。
- [ ] 修复验证发现的漏洞；文件清理限定解析后的管理目录、拒绝重解析逃逸，权限/路径变化不得扩大读写；关闭/清理不删除用户源视频，不杀用户其他进程。
- [ ] 复核源码、fixture、DB导出和日志均无真实密钥/Client ID/上传临时URL；前端只获脱敏状态；系统不更改电源计划、不强制唤醒。
- [ ] GREEN：Python非live全量、前端测试/构建/类型、仿真E2E均通过；恢复后未知状态需对账，不自动回到PENDING。真实测试状态按P05/P06证据原样报告。
- [ ] 提交`test: cover security and recovery across the full workflow`，只stage明确测试/修复/文档文件，形成未解决风险清单。

### Task 8: P08 — Windows启动交付与用户实机验收

**依赖：** P07，完整交付还要求P05/P06全部通过。**Files：** `D:\auto-deploy\scripts\start-fabu.ps1`、`D:\auto-deploy\scripts\stop-fabu.ps1`、`D:\auto-deploy\scripts\verify-installation.ps1`、`D:\auto-deploy\src\fabu\launcher.py`、`D:\auto-deploy\tests\test_launcher.py`、`D:\auto-deploy\README.md`、`D:\auto-deploy\docs\operations\acceptance.md`。

**Interfaces：** `launch(settings: Settings) -> LaunchResult`、`stop_owned_runtime(settings: Settings) -> None`；首版交付本地目录+锁定Python环境+已构建前端静态资源+固定工具路径清单，不强求单文件exe或后台系统服务。未安装工具显式引导核验，不自动升级/自动下载未知发行包。

- [ ] RED：`test_second_launch_reuses_instance`启动两次只一个Worker；`test_missing_tool_explains_actual_path`实际用户路径缺工具明确提示；`test_close_browser_keeps_worker`关页面不取消任务；`test_stop_only_terminates_owned_processes`不杀其他程序。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\test_launcher.py' -q`观察RED。
- [ ] 实现后台启动使用隐藏窗口（PowerShell Start-Process需WindowStyle Hidden），仅用户入口打开浏览器；首次配置展示真实数据目录/磁盘/版本；异常显示最近日志位置而非敏感全文。
- [ ] GREEN：同命令PASS与全套非live回归；在用户实际PowerShell中核验路径、工具、文件访问、账户设置，不只在代理工具环境里验收。
- [ ] 最终验收：单URL+标题+预设分别走立即/原生预约；三平台素材/标题/话题/分类/声明正确，重启无重复，额度符合零新增费用，预览/修改预设符合约定。若G2不通过，报告“本地部分完成，发布未解锁”，保留可用本地处理，不自动换路线。
- [ ] 提交`feat: deliver Windows launcher and acceptance guide`；展示提交/测试/尚未验证项，按用户选择再决定推送/PR/合并，不自行上远端或真实账号重测。
