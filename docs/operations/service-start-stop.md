# Windows 服务启动与停止

## 桌面一键入口

本机已创建 `C:\Users\77465\Desktop\启动 Fabu.lnk` 和 `C:\Users\77465\Desktop\停止 Fabu.lnk`，分别指向 `D:\auto-deploy\scripts\start-fabu.bat` 与 `D:\auto-deploy\scripts\stop-fabu.bat`。

BAT 使用脚本自身位置寻找项目虚拟环境，与调用终端的当前目录无关；固定当前用户 `%LOCALAPPDATA%\Fabu` 和端口8765，不继承旧 `FABU_STATE_DIR` / `FABU_PORT` 等配置。它直接调用已有 Python launcher，与 PowerShell 入口共用 DPAPI / HMAC 进程控制，没有绕过系统执行策略或添加强杀。

双击启动会打开新认证页面；双击停止不会关闭浏览器旧标签页。操作失败时窗口保留显示错误；`stopped:false` 仍仅表示没有停止到记录进程，不能据此认定端口上任意程序已停止。若移动项目目录，须相应更新桌面快捷方式的目标位置。

## 推荐入口

主工作区为 `D:\auto-deploy`。在 Windows PowerShell 5.1 或 PowerShell 7 中执行：

```powershell
$Project = 'D:\auto-deploy'
$State = Join-Path $env:LOCALAPPDATA 'Fabu'
& "$Project\scripts\verify-installation.ps1" -StateDir $State -Port 8765
& "$Project\scripts\start-fabu.ps1" -StateDir $State -Port 8765
# 只需要后台服务、不打开认证页面时，追加 -NoBrowser。
& "$Project\scripts\stop-fabu.ps1" -StateDir $State -Port 8765
```

也支持独立 shell 的 `powershell.exe -NoProfile -File <脚本完整路径>` 或
`pwsh.exe -NoProfile -File <脚本完整路径>`。不更改执行策略，不使用 Bypass。
脚本路径含空格时使用调用运算符 `&` 和引号；参数名称不要放在引号中。
不要点源执行（`. <脚本>`）；这些入口是会返回退出码的独立脚本。

后台 Python 使用隐藏窗口。启动脚本通过正常 launcher 在浏览器打开一次性认证入口，
不打印 bootstrap。不要从日志、所有权文件或浏览器地址复制令牌，也不要手工拼接认证 URL。

## 参数、环境与工作区

- 显式参数优先于当前 shell 的 `FABU_*` 环境变量；没有 `-Port` 时沿用
  `FABU_PORT`，变量也不存在时默认为 8765。不要给未设置的环境变量写
  `-Port $env:FABU_PORT`，空值会绑定为 0，PowerShell 会拒绝它。
- 脚本设置的环境变量在当前 shell 中保留；新打开的 shell 不继承之前脚本的参数。
  对自定义实例，启动/停止都传同一 `-StateDir` 和 `-Port`。
- 三个脚本使用各自项目的 `.venv`，并通过 `-I -X utf8` 隔离 Python 导入来源，
  避免调用目录或旧 `PYTHONPATH` 覆盖已安装包，同时保留中文路径的 UTF-8 输出。
  这不会自动修复一个安装来源本来就错误的虚拟环境。可以只读检查：

  ```powershell
  & 'D:\auto-deploy\.venv\Scripts\python.exe' -I -X utf8 -c 'from fabu.launcher import ROOT; print(ROOT)'
  ```

- `reused: true` 只表示复用了同一状态目录/端口的已认证服务，**不表示旧服务自动迁移了代码**。
  工作区迁移后须先确认没有活动任务，再正常 stop，然后从目标工作区 start。
- stop 只依赖服务控制配置，不要求旧 Node.exe、媒体目录、工具目录或运行模式仍有效。
  StateDir、Port 和绑定地址仍严格校验；不会扫描并停止其他配置的服务。

## 结果与故障处理

- start 成功会返回 PID、是否复用、页面入口和日志路径，不包含控制令牌。
- stop 的 `stopped: true` 表示通过 DPAPI 所有权记录、PID/创建时间/可执行文件校验、
  HMAC 控制请求之后，记录的进程已退出。`stopped: false` 表示该配置没有存活的记录所有者，
  不表示已停止任意占用端口的程序；首先核对 StateDir/Port。
- 配置验证错误会给出安全的字段名和非零退出码，不输出输入值或 traceback。
- 若端口被无关进程占用，启动器拒绝替换它。
- 若正常停止超过 45 秒，启动器报“仍在停止”，不强杀。先检查界面是否有活动处理；
  不删除 `owner.dpapi`、不改任务状态、不按 Python/FFmpeg 名称杀进程。
- 以下命令只观察监听，不终止任何程序：

  ```powershell
  Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort,OwningProcess
  ```

## 2026-11-07 调查记录

正常的 Windows PowerShell 5.1 / PowerShell 7 `-File` 与调用运算符执行方式均可运行，
没有复现正常配置下的启动失败或停止超时。原先 8765 的已认证服务实际来自旧 worktree；
在确认唯一真实任务为 PAUSED、媒体及发布队列无活动之后，正常 stop 完成，监听关闭，
服务进程及虚拟环境启动父进程退出，再从 D 盘启动并验证二次 start 复用。

隔离状态目录的回归测试复现了三类边界问题：

1. 旧工作目录/`PYTHONPATH` 可以遮蔽脚本所选虚拟环境中的 launcher。
2. 无效配置在错误处理之外解析，三个脚本都会出现 traceback，并暴露输入值。
3. 失效 Node/媒体配置会令 stop 在发送签名停止请求之前失败。

修复仅涉及入口隔离、配置错误处理及停止时排除无关媒体配置；未更改运行时的签名协议、
进程终止机制、任务恢复或发布逻辑。测试用临时状态目录和独立端口，不修改真实任务/账号。


### 回归命令与已知测试边界

```powershell
& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest `
    'D:\auto-deploy\tests\test_launcher.py' `
    'D:\auto-deploy\tests\test_launcher_scripts.py' -q
```

首次新增边界用例的有效 RED 为 12 failed / 4 passed。修复并保留中文输出后，
该阶段 launcher + scripts 为 29 passed；后续增加两种 shell 的显式端口覆盖用例。
未更改 stop 的 45 秒等待算法或签名校验，没有证据支持增加强杀、关闭保护或伪造 owner。

扩大回归首次使用 `python -m pytest -m "not media" -q`，结果是
**645 passed、7 skipped、45 deselected、5 个 fixture errors**，不是全套通过。
五个未标记为 media 的用例仍依赖真实媒体 fixture；缺少
`FABU_TEST_FFMPEG` / `FABU_TEST_FFPROBE`，因此在调用工具之前就失败：

- `tests/media/test_layers.py::test_layer_asset_changes_after_compile_rejected`
- `tests/media/test_media_service.py::test_source_login_failure_is_a_safe_persisted_reason`
- `tests/media/test_media_service.py::test_untrusted_source_error_text_is_not_used_as_a_pause_reason`
- `tests/media/test_media_service.py::test_browser_session_failure_has_actionable_safe_pause_reason`
- `tests/media/test_preview.py::test_output_validation_honors_cancel_before_tools`

本次禁止实际媒体处理，因此不为这些用例配置/调用 FFmpeg，也不修改范围外测试的标记。
安全扩大回归可在 `-m "not media"` 上为上述五个完整 node id 各追加一个 `--deselect=`。
7 个 skip 来自测试套件本身，不能据此声明相关可选能力已验证。

真实服务的修复后闭环：PowerShell 7 stop → 服务与虚拟环境父进程退出 → 8765 关闭 →
Windows PowerShell 5.1 正常 start（由 launcher 打开认证入口）→ PowerShell 7 二次 start
复用 → 签名 status ready。运行来源为 D 盘，真实 SQLite 28 张表的内容摘要在闭环前后完全一致。


安全扩大回归最终结果：**645 passed、7 skipped、50 deselected，退出码 0**（203.62 秒）；
其中包含全部 31 项 launcher/scripts 用例。50 个排除项由 45 个 media 标记用例及上述
5 个未标记但需要真实媒体 fixture 的用例构成，不能视为完整媒体验收。
`ruff check`、`ruff format --check` 与 `git diff --check` 也通过。
从初始调查到最终复查，真实数据库全部 28 张表的内容摘要仍与初始快照一致。
