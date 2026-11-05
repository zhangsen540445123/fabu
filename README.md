# Fabu

Windows 本地视频下载、2K 处理和蚁小二多平台发布助手。

## 当前状态：第一阶段开发中，发布接入门槛未通过

已经实现客户端启动策略和只读诊断，不是完整发布软件。
**目前没有网页、下载、转码或真实投稿功能；不会点击发布、上传视频或改动现有任务。**
按已批准计划，必须先确认客户端的稳定控件、三个账号身份及逐平台提交记录，才能推进后续阶段。

当前实机能发现安装的快捷方式和客户端进程/窗口，但只能持续读到 Electron 外壳，内嵌账号页控件未稳定暴露。
曾通过系统快捷方式成功冷启动和读取首页；这不证明正式启动器及账号/投稿接入已经验收完成。
诊断返回 `ui_unavailable` 是安全暂停，不会报假成功。

## 安装

需要 Windows 11、Python 3.12，以及已经安装的蚁小二客户端。
在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 只读检查

```powershell
.\.venv\Scripts\python.exe -m fabu client inspect
```

默认只读取，不启动、不恢复窗口、不截图，不打印账户昵称、号码、页面原文或凭证。
快捷方式有多个候选时必须明确指定：

```powershell
.\.venv\Scripts\python.exe -m fabu client inspect --shortcut "C:\Users\Public\Desktop\蚁小二4.0.lnk"
```

## 自动启动或恢复客户端

```powershell
.\.venv\Scripts\python.exe -m fabu client start
```

最长90秒；只确认无进程才允许第二次启动，已有主窗口或隐藏窗口先恢复。
当前未限时的 UIA 快捷方式回退未启用，Shell启动失败时明确暂停，不尝试其他未经验证路线。
锁屏、登录问题、未知既存任务和不可核验控件都会暂停。不会强杀客户端。
`ready` 只表示可识别的内嵌页面已读到，**不表示已启用发布，更不表示已投稿**。
诊断退出码：0＝页面可读取，2＝需要处理或不满足安全条件；非法参数为 argparse 的退出码2。

项目提供 `scripts/check-client.cmd` 和 `scripts/start-client.cmd`，可直接双击；不具备完整Fabu面板。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

默认测试全部离线，使用模拟窗口、进程和媒体状态，不操作真实客户端或投稿。
Windows CI 同样不启动桌面应用。实机诊断结果不写入源码目录。

## 计划与验收

完整设计在 `docs/superpowers/specs/2026-11-06-fabu-design.md`。
实施计划在 `docs/superpowers/plans/2026-11-06-fabu-implementation.md`。
当前接入验证记录在 `docs/client-validation.md`。

不使用蚁小二 CLI、付费开放 API、私有接口、固定坐标或运行时大模型。
后续目标是链接和标题到抖音、今日头条、B站的可验证已提交；不声称审核通过。
