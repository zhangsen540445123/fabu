# Fabu v1 开发进度

日期：2026-11-07。目标模式active，预算80,000,000 tokens。整体未完成。

## 工作区
- 实施根：`C:\Users\77465\.codex\worktrees\fabu-v1\auto-deploy`
- 分支：`codex/fabu-v1`；基准`3c04d39`；未推送/合并。
- 原目录`D:\auto-deploy`的22项删除不动。旧GUI-only实现只在隔离分支退场。
- 计划中的`D:\auto-deploy`代码路径在执行时映射到上述隔离根，外部媒体/凭证路径不替换。

## 已完成
F01–F06：配置/环境、受控工具进程、只读CLI能力报告、SQLite任务与恢复、不可变素材/预设、本地受保护API及基础网页。

验证：81 Python tests、5前端tests、类型检查、构建及1个真实Chromium端到端测试通过。桌面与390px界面截图已检查；窄屏任务表采用横向滚动，页面无溢出。F06完整复验命令/结果见同计划的git-ignored `.superpowers/sdd/2026-11-07-fabu-foundation/progress.md`。

## 下一步
M01→M02→M03：媒体探测/比例、单条视频下载/原封面、基础渲染与校验；之后M04–M10流水线与预览，P01–P08发布/交付。

## 重要裁定
- `JobSnapshot.revision`用于状态CAS；`content_revision`用于内容/媒体版本。TargetAttempt.revision等于content_revision，后续编译/发布不得混用。
- 低层record_attempt只持久化/冻结，不是发布授权或幂等网关；P03必须补完整协调门禁。
- 首次启动创建的基础预设仍是未验收的草稿；旧FFmpeg样片不能代替新引擎回归。
- UI此阶段只保存本地任务；未接处理器，不假装下载/渲染完成。真实发布始终关闭。
- 查询发现百家号库存新增，不增加第四发布目标。原三平台仍为既定范围。
- 免费上传/扣次、HEVC限制和平台原生预约仍UNKNOWN。G1未找到已证实的收费硬限制，可继续离线开发；G2测试要另行确切授权。
- 本次未上传、投稿、预约或修改平台账号。此前泄露旧Key是否撤销未知，禁止重复输出。

## 恢复上下文
先读本文件、当前子计划、对应`.superpowers/sdd/<plan-name>/progress.md`和git log。已完成任务不要重新实现。每任务RED→GREEN，提交后用executing-plans/task-done记录；媒体与发布计划各有独立ledger。
