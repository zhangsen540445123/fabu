import { useEffect, useState } from "react";
import {
  getAccounts,
  getPublishingConnection,
  refreshPublishingAccounts,
  savePublishingConnection,
  type AccountCatalogue,
  type PublishingConnection,
} from "../api";
import { platformNames } from "../components/TargetStatus";
export default function AccountsPage() {
  const [scope, setScope] = useState<"live" | "simulation">("live"),
    [catalog, setCatalog] = useState<AccountCatalogue | null>(null),
    [connection, setConnection] = useState<PublishingConnection | null>(null),
    [root, setRoot] = useState(""),
    [key, setKey] = useState(""),
    [clientId, setClientId] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setCatalog(null);
    getAccounts(scope)
      .then((r) => {
        if (alive) setCatalog(r);
      })
      .catch((e) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, [scope]);
  useEffect(() => {
    let alive = true;
    getPublishingConnection()
      .then((c) => {
        if (alive) {
          setConnection(c);
          setRoot(c.package_root ?? "");
        }
      })
      .catch((e) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, []);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setConnection(
        await savePublishingConnection({
          package_root: root,
          api_key: key,
          local_client_id: clientId,
        }),
      );
      setKey("");
      setClientId("");
      setCatalog(await getAccounts(scope));
      setMessage("凭证已加密保存。尚未连接或发布，下一步可只读同步账号。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const result = await refreshPublishingAccounts();
      setCatalog(await getAccounts(scope));
      setMessage(
        `已只读同步 ${result.refreshed} 个支持的平台账号；同步本身不会上传或投稿。真实流程以本机授权与就绪检查为准。`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="page-heading">
        <div>
          <span className="eyebrow">ACCOUNTS / 账号与连接</span>
          <h1>身份先确认，发布再执行。</h1>
          <p>账号使用稳定ID绑定。同名账号不会被自动当作同一账号。</p>
        </div>
      </section>
      <section className="panel padded">
        <h2>蚁小二官方CLI连接</h2>
        <p>
          CLI是命令行工具，不是智能体。这里只保存本机凭证和进行只读查询，不上传或发布。
        </p>
        <p>
          {connection?.live_enabled
            ? "本机真实发布授权已开启"
            : "本机真实发布尚未明确授权"}
        </p>
        <p>
          授权不等于当前工具、凭证或默认配置已就绪，请查看工作台发布就绪状态。
        </p>
        <form onSubmit={(e) => void save(e)}>
          <fieldset disabled={busy}>
            <label>
              官方CLI包目录
              <input
                value={root}
                onChange={(e) => setRoot(e.target.value)}
                placeholder="选择包含bin-native、schemas、references的目录"
                required
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <label>
              本地发布客户端ID
              <input
                type="password"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <p>
              当前状态：
              {connection?.configured ? "已配置（不会回显密钥）" : "未配置"}
              。密钥使用当前Windows用户加密；不会上传到本项目仓库。
            </p>
            <button className="secondary">加密保存连接配置</button>
          </fieldset>
        </form>
        <button
          className="secondary"
          disabled={busy || !connection?.configured}
          onClick={() => void refresh()}
        >
          只读同步真实账号与B站分类
        </button>
      </section>
      <section className="panel padded">
        <div className="panel-title">
          <h2>账号目录</h2>
          <label>
            账号环境
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as typeof scope)}
            >
              <option value="live">真实账号（仅查看）</option>
              <option value="simulation">本地仿真账号</option>
            </select>
          </label>
        </div>
        {scope === "simulation" && (
          <p className="preset-note">
            演示账号，不关联你的实际平台账号；不会消耗发布额度。
          </p>
        )}
        {!catalog?.accounts.length && (
          <p>
            尚无已同步的账号。配置连接后可进行只读同步，同步本身不会授权或发送真实投稿。
          </p>
        )}
        {catalog?.accounts.map((a) => (
          <div className="target-row" key={a.account_id}>
            <div>
              <strong>
                {platformNames[a.platform]} · {a.nickname}
              </strong>
              <small className="break">稳定ID：{a.account_id}</small>
              {a.category_names.length > 0 && (
                <p>视频分类：{a.category_names.join(" / ")}</p>
              )}
            </div>
            <span className="badge neutral">
              {a.active ? "目录查询时登录有效" : "需要重新登录"}
            </span>
          </div>
        ))}
      </section>
      {message && (
        <p className="preset-note" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
