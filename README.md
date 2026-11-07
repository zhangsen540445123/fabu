# Fabu · 本地视频工作台

Windows单用户本地网页与独立后台Worker：单条YouTube输入、原封面获取、可编排FFmpeg处理、样片/预设确认、三平台发布配置、仿真和受控发布验证。

> **当前是可运行的开发交付，不是已完成真实发布验收的正式版。**
> 本地处理与仿真已测试；真实上传、投稿、平台原生预约保持禁用。免费扣次、平台实际接收和指定YouTube链接的登录验证仍待实机验证。不会暗中切换收费API、云发布或GUI自动化。

## 当前机器启动

以下命令应在自己的PowerShell终端运行；`$Project`按实际项目所在位置修改。

```powershell
$Project = 'C:\Users\77465\.codex\worktrees\fabu-v1\auto-deploy'
& "$Project\scripts\verify-installation.ps1" `
  -ToolsDir 'D:\devtools\idm\video\Fabu-comparison-20261107\tools' `
  -NodePath 'D:\devtools\nodejs\node.exe'
& "$Project\scripts\start-fabu.ps1" `
  -ToolsDir 'D:\devtools\idm\video\Fabu-comparison-20261107\tools' `
  -NodePath 'D:\devtools\nodejs\node.exe'
```

- 启动自动打开带单次认证入口的本机浏览器，不打印认证令牌。
- 重复运行启动脚本复用同一进程，并为重新打开的页面签发新的单次入口；既有页面会话不受影响。
- 关闭网页不停止后台任务。需要停止时执行：

```powershell
& "$Project\scripts\stop-fabu.ps1"
```

- 停止是经过身份校验的本机控制，不使用`taskkill /IM`，也不按名称杀死Python、FFmpeg或蚁小二。
- 使用`-NoBrowser`可只启动后台。`python -m fabu`与脚本使用同一启动器，不创建另一个无归属的服务。
- 不修改系统执行策略。若当前策略禁止脚本，可先阅读脚本，再按自己的管理策略对该次执行授权；项目不会自动绕过系统策略。

## 数据与安装目录

- 默认状态目录：`%LOCALAPPDATA%\Fabu`；默认媒体目录：`D:\FabuData\jobs`。
- 可向脚本显式传`-StateDir`、`-DataDir`、`-ToolsDir`、`-NodePath`、`-Port`。自定义状态目录/端口后，停止脚本也须传相同参数。
- 在网页“运行设置”保存的工具/媒体路径优先于启动默认值。检查报告会显示生效路径，不会假定你机器上有某个预安装程序。
- 大视频/素材库放在媒体目录下的`assets`；旧版本状态目录下的素材保留可读，不自动移动或删除。已有素材或任务时不支持在设置中直接迁移媒体盘。
- 预览审批绑定FFmpeg/ffprobe二进制摘要；更换工具后应创建新内容版本并重新检查样片。
- 凭证位于当前用户DPAPI保护的本地文件；临时CLI配置只允许当前用户与SYSTEM。不要将状态目录加入Git或发给他人。
- 私有进程身份记录：`<状态目录>\runtime\owner.dpapi`；精简启动日志：`<状态目录>\runtime\runtime.log`。
- 不自动下载/更新工具，不更改电源策略，不强制开关机。电脑休眠会暂停本地工作。

缺少FFmpeg、ffprobe或Node时仍可打开设置页面，但不能把缺工具的状态当作媒体处理成功。缺前端构建或Python环境则明确报出缺失路径。

## 新机器开发安装

需要用户自行准备Python3.12、Node/npm、FFmpeg/ffprobe与官方蚁小二CLI。依赖锁定在`requirements.lock.txt`和`frontend/package-lock.json`。下列操作是用户显式执行的安装步骤，不在软件运行时自动执行：

```powershell
Set-Location $Project
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
& '.\.venv\Scripts\python.exe' -m pip install --no-deps -e .
npm --prefix frontend ci
npm --prefix frontend run build
```

不要将本机`.venv`作为跨机器可搬移的Python安装包。首版交付为项目目录、锁定依赖、已构建前端及明确配置的工具路径，不是单文件exe或系统服务。

## 使用流程

1. 在“处理流水线”编辑预设：截取、变速、裁切、翻转、色彩、画布、图片/文字/SRT、片头片尾、声音处理均可按支持的阶段编排；附加处理默认关闭。
2. 新建本地任务：输入单条YouTube链接、发布标题、处理预设。需要认证时暂停，不自动提取浏览器cookies。
3. 首次预设或参数变化时生成样片并确认。已确认且适配新源的预设可以复用，无需每次重做确认。
4. 完整成片验证后，可在详情配置发布环境、稳定账号ID、话题、来源声明、立即/北京时间预约。
5. 本地仿真不上传或扣真实次数。真实环境现阶段只允许配置/预检；实际投稿未解锁。

**自动流程：**首次在任务详情保存发布配置后，可“保存为可复用发布流程模板”。以后新建任务时选择该模板及立即/预约方式，下载与媒体验证完成后自动进入发布仿真，无需额外点击提交。模板不复用旧标题、封面或预约时间；首次处理预设仍需样片确认。真实账号模板可以保存，但自动真实投稿仍受未完成的能力验收门槛限制，不能把合成源/仿真链路当作真实“一条链接全自动发布”验收。

## 结果与恢复

- “成片就绪”不是“已发布”。提供方接收、平台投稿、平台预约、公开分别显示。
- 任一目标FAILED或UNKNOWN即停止其余尚未提交目标；已有平台内容不承诺原子撤销。
- UNKNOWN只能对账，不盲目重投。资源上传结果不明与来源已被其他任务占用都会在排队前阻断。
- 已经可能投稿的真实任务冻结共享内容。已有预约需去平台修改/删除，本地不自动撤销或改为立即发布。
- 实际原生预约确认后是否可退出客户端必须有平台托管证据，不能只凭CLI成功回执判断。

详见`docs/operations/publishing-workbench.md`、`live-verification.md`、`security-recovery.md`及`acceptance.md`。

## 开发验证

```powershell
$env:FABU_TEST_FFMPEG = 'D:\devtools\idm\video\Fabu-comparison-20261107\tools\ffmpeg.exe'
$env:FABU_TEST_FFPROBE = 'D:\devtools\idm\video\Fabu-comparison-20261107\tools\ffprobe.exe'
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' -m ruff check src tests
npm --prefix frontend run test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e -- --project=chromium
```

普通测试强制拒绝`live`标记测试，不会调用真实上传/投稿。浏览器测试使用隔离临时目录与合成媒体；它不代替真实YouTube下载和三平台验收。
