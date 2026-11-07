# 官方 CLI 离线契约检查（3.2.32）

## 验证范围

2026-11-07，用户在工作台配置连接后，只读账号/分类同步成功：目标范围仍为抖音、头条号、B站，未选择额外返回的百家号。平台 schema 查询成功。账号列表的有效状态不代表本机发布客户端在线，也不证明投稿、免费余额、HEVC或原生预约可用。

本轮使用已安装的官方 yxer 3.2.32 二进制（SHA256 `947a3bd1e68546707e75865328151e7f8b7309add70b9c720a32b16291a9c2c4`）执行离线契约检查：

- 独立临时目录、空配置文件、合成账号/资源 key/客户端 ID。没有读取或复制用户密钥。
- API 地址限定为 `http://127.0.0.1:1`；只运行 `validate` 和带 `--dry-run` 的 `publish`。
- 不运行 `upload`、正式 `publish`、删除或预约，不升级/修改官方包。
- 合成资源 key 仅用于本地结构校验，不代表已上传素材，不可用来正式投稿。

## 已发现并修正的 Fabu 适配问题

旧网关把校验回执当成 `data.publishChannel/clientId`。实际：

| 命令 | 实际关键结构 | 必须核对 |
|---|---|---|
| validate | `data.valid`、`data.prepared`、`data.request` | 校验成功、明确本机通道和客户端、完整请求一致 |
| publish --dry-run | `data.dryRun`、`data.request`、`data.meta` | 模式、有效通道、客户端、目标列表、schema 检查及请求一致 |

新解析器独立于正式提交回执。它核对当前版本、命令阶段及完整请求：唯一账号、视频/封面 key 与元数据、标题、标签、来源声明、预约时间等。只接受此次观察到的顶层 cover/desc/isAppContent 推导；不接受内容重写、额外目标、草稿、云端切换。JSON数值1.0与1允许等价，但布尔值、数字字符串不混同。

网关使用同一份冻结 payload 完成校验/预览；通过这些检查仍不证明任何平台接收或公开。正式提交回执、真实费用边界和生产模式接线继续待验。

## 已确认的上游限制：B站转载来源 URL

官方3.2.32离线验证会拒绝转载表单里的 HTTPS `contentSourceUrl`，错误明示该字段是“不允许的外部URL”。本轮测试所用 YouTube 来源网址符合本项目的来源保留要求，但被CLI通用URL检查拦截。

不能因此删除来源、去除网址协议、伪造素材key、将授权转载改为自制，或静默改用其他发布通道。此限制需要官方修复/明确支持的字段契约或用户重新确认合法的业务路线。

三平台校验成功的离线测试采用明确的**合成自制素材声明**，仅覆盖该独立表单分支，不把它应用到用户视频。另有负向测试保留转载来源URL，并确认固定版本的实际拒绝。

已补齐上传前全目标结构检查：使用明显的 `fabu-preflight-only/` 占位 key 构造**未签名的本地校验 payload**，只执行 validate。占位数据不生成 UploadReceipt、不写上传日志、不消耗上传次数，也不进入 publish --dry-run 或正式投稿。所有目标通过后重新核对文件摘要、CLI二进制和客户端身份，才允许进入原有上传流程。

上传后的真实资源 payload 仍必须重新 validate 和 dry-run；占位检查不能替代真实素材校验。独立安装包测试已确认B站转载URL拒绝发生在任何上传调用之前，不删改来源。已取消且尚未开始的上传不会创建结果不明的上传记录；真正开始后的不明结果仍禁止盲重试。

## 可重复执行

在仓库根目录的 PowerShell 中，明确指定已安装官方包根目录：

```powershell
$env:FABU_TEST_YIXIAOER_PACKAGE = 'C:\Users\77465\AppData\Local\Fabu\cli-probe-20261107\cli\node_modules\@yixiaoermail\cli'
& .\.venv\Scripts\python.exe -m pytest tests/publishing/test_cli_contract.py tests/publishing/test_gateway.py -q
```

未设置环境变量时，不搜索或执行本机 CLI，三项实际二进制测试会明确跳过；其他纯单元测试仍执行。

本地脱敏证据位于 `.superpowers/sdd/2026-11-07-fabu-publishing/`：
- `configured-readonly-evidence.json`：真实只读能力和字段观察。
- `offline-cli-contract/ba644ffd-6e14-4836-9597-226960260b08/results.json`：原始离线回执，仅含合成资源/身份。
- `offline-channel-red.log`：旧解析器拒绝正确嵌套回执的两项失败。
- `offline-channel-green.log`：适配修复后81项定向通过，含安装包离线矩阵。
- `offline-channel-full.log`：本轮完整Python回归550项通过（209.26秒）；Ruff108文件通过。

这不是三平台真实立即或平台原生预约的验收报告。

## 后续验证日志

- `preupload-red.log`：原顺序导致3项失败（全目标检查晚于上传、最后目标失败仍已上传、校验中素材变更未挡住）。
- `preupload-boundary-red.log`：取消发生在结构检查结束时，会错误留下UNKNOWN上传记录的失败用例。
- `preupload-auth-green.log`：定向124项通过，包括安装包对转载URL的真实离线拒绝先于网关上传；后加四项边界检查另已通过。
- `preupload-auth-full.log`：后续全Python回归。
- `preupload-auth-ui-green.log` / `preupload-auth-browser.log`：前端与隔离浏览器测试，不是用户账号的真实发布。

## 下载登录失败的产品提示

下载器明确返回登录验证要求时，用固定的类型化异常传递为 `source_authentication_required`。任务详情说明YouTube要求登录、未读取浏览器登录态，需先确认访问权限后手动重试。任意外部错误文字不能成为数据库中的暂停原因，普通故障仍显示通用下载失败。

此提示不解决YouTube的访问限制，不会导入cookie、自动登录或自动重试。用户既有暂停任务的历史原因不做猜测性迁移；后台未重启，新的原因传播在下次正常启动后用于新执行。
