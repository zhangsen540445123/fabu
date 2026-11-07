# Chrome YouTube 登录态接入

本功能用于下载你有权访问和使用的视频。不保证绕过 YouTube 登录验证、账号限制或其他访问要求，也不改变素材来源声明和发布授权。

## 一次性安装（当前本机）

1. 在已经登录 YouTube 的 Chrome 中打开 `chrome://extensions`。
2. 打开右上角 **开发者模式**，点击 **加载已解压的扩展程序**。
3. 选择文件夹：`C:\Users\77465\AppData\Local\Fabu\browser-helper\chrome-extension`。
4. 初次安装会打开 **Fabu YouTube 助手** 页面。点击 **启用连接**，确认仅 YouTube 网站的权限请求。
5. 点击 **刷新连接状态**，看到 **已连接本机 Fabu 助手** 后即可使用 Fabu 下载。

这里只是建立本机连接，不会自动下载或发布任何视频。现有暂停任务保留暂停状态。尚未完成上述步骤时，不能将下载问题标为已解决。

## 使用

- Chrome 保持运行，并使用安装扩展的同一个用户配置。
- 默认先匿名访问。只有遇到下载器明确要求登录时才请求浏览器登录态，增加一次有限认证尝试。
- 已通过认证获取视频信息后，下载该视频前重新读取一次当前登录态；不能将这一授权转用于别的视频。
- 不需要手动导出 Cookie。Chrome 未连接、权限被撤销、登录失效或账号验证时，任务暂停，按提示处理后手动重试。
- 不会因为连接成功自动恢复旧任务，也不会触发上传或发布。
- 点击扩展中的 **停用并撤销权限** 可关闭此功能；移除扩展同样会断开。

## 保护范围

扩展只请求 `https://*.youtube.com/*` 网站权限，不申请 Google 全站、浏览历史、标签页、调试或下载权限。只通过 Chrome 官方 Cookie API 按需读取，不访问 Chrome 本地 Cookie 数据库、不关闭浏览器安全保护。

登录态仅通过 Native Messaging 交给本机受当前 Windows 用户保护的目录；跨进程响应使用 DPAPI 加密。下载子进程所需临时 Cookie 文件使用限制访问的目录，结束后删除。进程意外退出的残留由本机助手按心跳检查带 PID 创建时间的租约，确认进程已退出后清理，不按文件年龄删除正在使用的会话。Cookie 不进入日志、Git、蚁小二或第三方中转服务；下载器只将对应域 Cookie 用于 YouTube 请求。

连接状态不等于 YouTube 已登录，也不等于实际视频下载成功。真实验收必须完成视频下载及轨道/时长检查。

## 开发与部署

稳定扩展 ID：`kodlebnlbllemoihjlkchlldpaollgll`。不得随意更换 manifest 中的公钥，否则本机 host 的允许来源需要同步更换。这里只保留公钥，没有扩展签名私钥。

```powershell
# 在本仓库执行；prepare 只生成本机包，不写注册表。
& .\.venv\Scripts\python.exe -m fabu.browser_auth.setup prepare `
  --state-dir "$env:LOCALAPPDATA\Fabu" `
  --extension-dir (Join-Path (Get-Location) 'extensions\chrome-youtube')

# install 额外登记唯一的当前用户 NativeMessagingHosts\com.fabu.youtube_session。
# 不强装扩展、不改 Chrome 用户配置，不覆盖不同路径的已有主机注册。
& .\.venv\Scripts\python.exe -m fabu.browser_auth.setup install `
  --state-dir "$env:LOCALAPPDATA\Fabu" `
  --extension-dir (Join-Path (Get-Location) 'extensions\chrome-youtube')

# 只看连接状态，不读取 Cookie。
& .\.venv\Scripts\python.exe -m fabu.browser_auth.setup status `
  --state-dir "$env:LOCALAPPDATA\Fabu"
```

本机 host 使用当前 Fabu 虚拟环境的绝对 Python 路径，不依赖 PATH。如果移动/删除工作树或虚拟环境，需要重新部署；这次交付尚非独立打包安装器。Chrome 内的扩展安装/启用确认由用户完成。

## 验证状态

- 合成 Cookie 的 Python 请求响应、DPAPI、ACL、取消/异常清理、来源绑定与域范围已测试。
- 已测试真实 Windows `.bat -> 隔离 Python -> Native Messaging` 启动及 EOF 退出，未读取实际浏览器数据。
- 扩展使用模拟 Chrome API 运行权限与生命周期测试；这不代替真实 Chrome 验证。
- 真实视频下载仍待扩展安装/启用后验证，不因上述自动化测试通过而标记成功。
