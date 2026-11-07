import { providerReasonLabel } from "../publishingLabels";
import { formatBeijing } from "../publishing-time";
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
const observationLabels: Record<string, string> = {
  pending: "等待provider跟踪",
  running: "provider处理中",
  success: "provider报告成功（非平台验收）",
  failed: "provider报告失败",
  unknown: "provider结果不明",
};
function observationTime(value: string | null): string {
  return value && Number.isFinite(Date.parse(value))
    ? `${formatBeijing(value)}（北京时间）`
    : "暂无";
}
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
          <div className="provider-observation break">
            {t.provider_observation ? (
              <>
                <strong>
                  {observationLabels[t.provider_observation.status] ??
                    "provider结果不明"}
                </strong>
                {t.provider_observation.reason && (
                  <p>{providerReasonLabel(t.provider_observation.reason)}</p>
                )}
                <p>
                  最近观察：{observationTime(t.provider_observation.checked_at)}
                  ；下次检查：
                  {observationTime(t.provider_observation.next_check_at)}
                </p>
                <p>
                  平台内容ID：
                  {t.provider_observation.platform_content_id ?? "暂无"}
                </p>
                <p>
                  平台成片质量未验收（质量观察：
                  {t.provider_observation.quality_status}
                  ）；provider观察不代表平台已公开或完整成片验证通过。
                </p>
                <p>
                  预期时长：
                  {t.provider_observation.expected_duration_s ?? "未知"} 秒 /
                  观察时长：
                  {t.provider_observation.observed_duration_s ?? "未知"} 秒
                </p>
              </>
            ) : (
              <p>暂无provider观察；不代表平台已公开或成片质量已验收。</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
