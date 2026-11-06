import type { PublishTarget } from "../api";
const labels: Record<string, string> = {
  PENDING: "尚未提交",
  PRECHECKED: "预检通过",
  SUBMITTING: "正在提交",
  PROVIDER_ACCEPTED: "已被蚁小二接收",
  SUBMITTED: "平台已提交",
  SCHEDULED: "平台已预约",
  PUBLISHED: "平台已公开",
  FAILED: "提交失败",
  UNKNOWN: "结果不明 · 需要对账",
};
export const platformNames: Record<string, string> = {
  douyin: "抖音",
  toutiao: "头条号",
  bilibili: "B站",
};
export default function TargetStatus({
  targets,
  simulation = false,
}: {
  targets: PublishTarget[];
  simulation?: boolean;
}) {
  return (
    <div className="target-list" aria-label="逐平台状态">
      {simulation && (
        <p className="preset-note">
          以下是本地仿真回执，不代表平台已经收到或公开视频。
        </p>
      )}
      {targets.map((t) => (
        <div className="target-row" key={t.account_id}>
          <div>
            <strong>
              {platformNames[t.platform] ?? t.platform} · {t.nickname}
            </strong>
            <small className="break">账号ID：{t.account_id}</small>
          </div>
          <span
            className={`badge ${["FAILED", "UNKNOWN"].includes(t.state) ? "warning" : "neutral"}`}
          >
            {labels[t.state] ?? t.state}
          </span>
          {t.can_retry && <small>已确认未提交，可有限续跑</small>}
        </div>
      ))}
    </div>
  );
}
