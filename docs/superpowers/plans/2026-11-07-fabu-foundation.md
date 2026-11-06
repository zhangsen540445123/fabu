# Fabu Foundation Implementation Plan — 本机基础与能力门槛

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供不投稿的可启动本地应用、只读诊断、可靠任务存储和可复用的安全工具调用边界。

**Architecture:** 单用户127.0.0.1服务+独立串行Worker；SQLite保存任务、步骤、预设和外部提交证据。先构建受测试的副作用隔离，再接下载与发布。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、SQLite、React/TypeScript/Vite、pytest/Vitest/Playwright。

**Spec:** `D:\auto-deploy\docs\superpowers\specs\2026-11-07-fabu-v1-design.md`。

## Global Constraints

继承`D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-v1-roadmap.md`全部约束与类型。零新增付费、无真实上传/投稿、保留源工作区旧删除；仅在实施时安装并锁定依赖。测试数据不得包含实际API Key/Client ID/账号内部ID。

## Review Focus

- 中文/emoji/符号路径与文本不构成命令，日志不含秘密：F02。
- 休眠/崩溃/双启动/数据库锁不会创建重复执行者：F04。
- SAR与旋转由M01/M05覆盖；F05不得在导入时隐式转码破坏元信息。
- UNKNOWN/预约截止由P03/P05覆盖；F04只保存未知不推断成功。
- 素材移动/变更、磁盘不足不破坏快照：F05。

---

## 目录/接口归属

F01建立配置与可运行测试包；F02拥有工具/进程接口；F03拥有只读能力报告；F04拥有Job/Attempt领域类型、存储与Worker；F05拥有AssetRef/预设草稿持久化；F06拥有应用入口及基础网页。后续媒体/发布模块只通过这些接口操作状态，不各建第二份队列或数据库。

### Task 1: F01 — 可验证的配置与运行环境

**依赖：** 用户确认执行、总计划开工检查。**Files：** 拟建/重建`D:\auto-deploy\pyproject.toml`、`D:\auto-deploy\.gitignore`、`D:\auto-deploy\src\fabu\__init__.py`、`D:\auto-deploy\src\fabu\settings.py`、`D:\auto-deploy\tests\test_settings.py`、`D:\auto-deploy\tests\conftest.py`；锁文件`D:\auto-deploy\requirements.lock.txt`。

**Interfaces：** `load_settings(env: Mapping[str,str]) -> Settings`；`check_environment(settings: Settings) -> EnvironmentReport`。Settings含data_dir/db_path/tools_dir/bind_host/test_mode，默认媒体`D:\FabuData\jobs`，设置/DB在实际`LOCALAPPDATA\Fabu`；初始化不迁移旧媒体或读取auth文件。

- [ ] RED：先编写`test_paths_resolve_for_actual_user`与`test_public_bind_is_rejected`，断言`settings.bind_host == '127.0.0.1'`且公网地址验证失败；模拟LOCALAPPDATA不同和路径存在但为文件。仅建立本测试需要的包、venv与依赖，运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\test_settings.py' -q`，确认因未实现行为失败。
- [ ] 实现上述接口，显示Python/OS/磁盘/路径读写检查；路径不存在明确未配置，不猜用户实际环境已装好。
- [ ] 锁定实际可用依赖版本，创建受支持pytest标记`media/live`，默认拒绝live；忽略凭证、数据库、视频、预览、venv与node_modules。
- [ ] GREEN：重跑该测试文件，预期全部PASS；故意传入公网地址仍被拒绝，运行环境报告不包含凭证。
- [ ] 提交：仅stage本任务Files，提交`build: add validated local runtime configuration`；记录安装/测试版本，不声称已验证用户端发布。

### Task 2: F02 — 进程执行、超时与日志脱敏

**依赖：** F01。**Files：** `D:\auto-deploy\src\fabu\tools.py`、`D:\auto-deploy\src\fabu\redaction.py`、`D:\auto-deploy\tests\test_tools.py`。

**Interfaces：** 消费Settings/CommandSpec；`run_command(spec: CommandSpec, cancel: threading.Event) -> CommandResult`；`redact_event(event: dict, secrets: Sequence[str]) -> dict`。仅记录白名单事件，不默认存stdout/stderr全文；可执行文件是受配置固定路径，非从用户标题获取。

- [ ] RED：`test_arguments_remain_literal`使用本地假子进程打印JSON参数，断言输入`"剃须 [1] & echo x ' 😴"`完整为单参数且无旁路命令；`test_timeout_is_not_success`断言`result.timed_out is True`；`test_redaction_covers_stderr_and_urls`断言假密钥不出现在序列化日志中。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\test_tools.py' -q`观察RED。
- [ ] 实现参数数组、禁shell、明确cwd/env、超时和取消；用Windows Job Object或等价可证明的子进程归属机制清理本程序的进程树，不能按名称全杀FFmpeg/yxer。
- [ ] 校验工具路径类型/可执行版本，不硬编码已消失的bin-native路径；版本探测只用帮助/版本等无副作用命令。
- [ ] GREEN：重跑该测试，确认符号/中文/超时/子孙进程终止/旁路用户进程保留/日志脱敏全PASS。
- [ ] 提交`feat: isolate tool processes and redact diagnostics`，只stage上述3个文件。

### Task 3: F03 — 蚁小二只读能力清单（G1）

**依赖：** F02。**Files：** `D:\auto-deploy\src\fabu\publishing\capabilities.py`、`D:\auto-deploy\src\fabu\diagnostics.py`、`D:\auto-deploy\tests\publishing\test_capabilities.py`、`D:\auto-deploy\tests\fixtures\capabilities.json`、`D:\auto-deploy\docs\operations\capability-checklist.md`；新增包目录时一并创建必要`__init__.py`。

**Interfaces：** `inspect_capabilities(query: Callable[[str], dict], now: datetime) -> CapabilityReport`；`can_enable_mode(report: CapabilityReport, mode: str, target_ids: list[str]) -> bool`。query只接受已核实帮助中列出的只读操作名；不拼接未知CLI子命令。

- [ ] RED：`test_schema_field_does_not_prove_scheduling`断言存在scheduledTime时`report.native_schedule == 'UNKNOWN'`；`test_free_client_quota_does_not_unlock_cli`断言`can_enable_mode(...) is False`；`test_paid_gate_stops_route`断言已知付费阻断。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\publishing\test_capabilities.py' -q`观察RED。
- [ ] 实现报告：账号状态、工具版本、上传/立即/预约、三目标扣次、临时存储/有效期、单文件大小、HEVC、分类、来源声明逐项带证据和核对日期；缺信息保持UNKNOWN。
- [ ] 经已授权的只读范围在用户实际终端环境验证路径与账号；不打印或入库原API Key；此前聊天泄露的旧Key由用户撤销，不假定已撤销、不重复回显；付费/权限不足/接口不存在分别报告。此步不使用任何upload/publish命令，未证实项送P05/P06而非假装通过。
- [ ] GREEN：同命令PASS；能力清单区分“已证实”“文档声称”“待实际写入”；单测用合成fixture，不提交实际用户响应。
- [ ] 提交`feat: add evidence-based publishing capability gate`，限定上述Files。若G1发现明确收费硬限制，先报告用户，不自选收费或GUI替代路线。

### Task 4: F04 — 持久化任务、串行Worker及崩溃恢复

**依赖：** F01–F02。**Files：** `D:\auto-deploy\src\fabu\domain.py`、`D:\auto-deploy\src\fabu\store.py`、`D:\auto-deploy\src\fabu\worker.py`、`D:\auto-deploy\src\fabu\migrations\001_initial.sql`、`D:\auto-deploy\tests\test_jobs.py`。

**Interfaces：** 定义总计划JobSpec/JobSnapshot/TargetAttempt；`JobStore.create(spec: JobSpec) -> JobSnapshot`、`transition(job_id: str, expected_revision: int, next_state: str, reason: str | None) -> JobSnapshot`、`claim_next(worker_id: str) -> JobSnapshot | None`、`recover_interrupted() -> list[str]`；`Worker.run_once(handler: Callable[[JobSnapshot],None]) -> bool`。表覆盖jobs、job_revisions、stage_runs、target_attempts、events，后续迁移添加素材/预设。

- [ ] RED：`test_only_one_worker_claims_job`断言双worker实际handler调用总数1；`test_crash_does_not_reset_submitting`断言恢复目标`UNKNOWN`而非PENDING；`test_stale_revision_cannot_overwrite`断言旧版本更新失败；`test_sleep_does_not_spawn_second_owner`模拟长暂停，仍不允许第二owner提交。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\test_jobs.py' -q`观察RED。
- [ ] 实现事务/条件更新、Windows用户级单实例互斥和有界SQLite忙重试；不以“心跳超时”单独认定外部提交可以重跑；网络调用前后分别持久化，不能长事务包住网络。
- [ ] 阶段状态NEW/INSPECTING/DOWNLOADING/PROCESSING/READY/PUBLISHING及PAUSED/FAILED/CANCELED；等待预览用PAUSED+原因。渲染中断可重做渲染；已提交相关状态留给P03对账。
- [ ] GREEN：同命令PASS，另测关闭DB后重开、双启动、模拟崩溃、锁竞争，未丢事件且未重复执行副作用。
- [ ] 提交`feat: persist jobs and enforce a single worker`，限定Files及必要包文件。

### Task 5: F05 — 不可变素材、预设草稿与引用保护

**依赖：** F04。**Files：** `D:\auto-deploy\src\fabu\assets.py`、`D:\auto-deploy\src\fabu\presets.py`、`D:\auto-deploy\src\fabu\migrations\002_assets_presets.sql`、`D:\auto-deploy\tests\test_assets.py`。

**Interfaces：** `AssetStore.import_file(path: Path, media_type: str) -> AssetRef`、`verify(asset_id: str) -> bool`、`delete_unreferenced(asset_id: str) -> None`；`PresetStore.save_draft(preset_id: str | None, document: dict) -> PresetVersion`、`get_version(version_id: str) -> PresetVersion`。F05定义StepSpec信封（kind/enabled/scope/params/asset_ids）和PresetVersion；output先存dict，M03建立OutputProfile后严格验证，具体步骤参数交M04。必须复制并hash本地素材；数据库表assets、presets、preset_versions、asset_refs。

- [ ] RED：`test_external_change_does_not_mutate_asset`保存后修改源文件，断言库摘要不变；`test_referenced_asset_cannot_be_deleted`拒绝删除；`test_partial_copy_never_registers_asset`模拟磁盘不足，断言无可消费AssetRef；`test_import_rejects_directory_and_reparse_escape`拒绝文件路径实为目录及越权指向。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\test_assets.py' -q`观察RED。
- [ ] 实现临时复制→摘要/格式检查→同卷原子提升→登记，数据库失败时可清理孤立临时文件但不动源；导入font/SRT等类型按各自扩展与内容检测。
- [ ] 保存草稿创建新版本、名称改动不改处理版本的规则交M09完善；复制处理素材，不扫描或导入用户整个磁盘。
- [ ] GREEN：同命令PASS；移动源文件后现有素材仍能读取；引用库缺失/被外部篡改会检测失败，不默认信任只读标志。
- [ ] 提交`feat: store versioned assets and preset drafts`，限定Files。

### Task 6: F06 — 本地API与基础网页（首个可启动交付）

**依赖：** F03–F05。**Files：** `D:\auto-deploy\src\fabu\api\app.py`、`D:\auto-deploy\src\fabu\api\security.py`、`D:\auto-deploy\src\fabu\api\jobs.py`、`D:\auto-deploy\src\fabu\__main__.py`、`D:\auto-deploy\frontend\package.json`、`D:\auto-deploy\frontend\package-lock.json`、`D:\auto-deploy\frontend\src\App.tsx`、`D:\auto-deploy\frontend\src\pages\JobsPage.tsx`、`D:\auto-deploy\frontend\src\pages\SettingsPage.tsx`、`D:\auto-deploy\frontend\src\api.ts`、`D:\auto-deploy\tests\test_api_security.py`、`D:\auto-deploy\frontend\src\__tests__\jobs.test.tsx`、`D:\auto-deploy\frontend\tests\smoke.spec.ts`；附该任务必需的Vite/TS/Vitest/Playwright配置文件。

**Interfaces：** `create_app(settings: Settings, store: JobStore) -> FastAPI`；GET `/api/health`仅非敏感状态，认证后GET/POST `/api/jobs`、GET `/api/jobs/{id}`、POST `/api/jobs/{id}/cancel`、GET `/api/capabilities`、GET/PUT `/api/settings`。REST模型取自领域类型，敏感配置只回`configured: bool`。

- [ ] RED：API测试`test_cross_origin_write_denied`断言403，`test_media_path_not_arbitrary_file_read`拒绝任意磁盘路径，`test_secrets_never_returned`不回密钥；UI测试`jobs.test.tsx`断言刷新后任务仍在、未知能力显示“未验证”不是成功。运行Python该文件及`npm --prefix 'D:\auto-deploy\frontend' run test -- --run`观察RED。
- [ ] 实现本地会话随机密钥、同源/Host/Origin校验、写入CSRF保护、严格cookie；首个会话引导不得把密钥放访问日志/持久查询串。所有敏感GET也需认证；API请求只用受控资源ID，不提供通用读路径/执行命令接口。
- [ ] 实现任务列表/详情、首次路径与工具配置、能力状态；服务关闭/网络断开显示最后同步时间；开发态仅仿真/本地任务，真实发布按钮禁用并说明原因。
- [ ] GREEN：API测试PASS；前端test/typecheck/build全部0错误；`npm --prefix 'D:\auto-deploy\frontend' run test:e2e -- --project=chromium`的smoke通过，无外部投稿请求。
- [ ] 提交`feat: add authenticated local dashboard`，按Files明确stage；S1演示可创建仿真任务并重启恢复，不能宣称媒体或发布已经完成。
