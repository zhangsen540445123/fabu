import type { Capabilities, PublishingReadiness } from "../api";
export default function PublishingReadinessCard({
  readiness,
  capabilities,
  refresh,
}: {
  readiness: PublishingReadiness;
  capabilities: Capabilities | null;
  refresh: () => Promise<void>;
}) {
  const ready =
    readiness.enabled &&
    readiness.status === "ready" &&
    !readiness.blockers.length;
  const unknownBudget = readiness.blockers.some((b) =>
    b.includes("readiness_unavailable"),
  );
  return (
    <section className="publish-card" aria-label="真实发布就绪状态">
      <div className="publish-intro">
        <span className="eyebrow">PUBLISHING / 发布就绪状态</span>
        <h2>{ready ? "自动真实发布已就绪" : "真实发布已阻断"}</h2>
        <p>
          {readiness.enabled
            ? "本机真实发布授权已开启"
            : "本机真实发布尚未明确授权"}
          ；当前就绪状态与平台能力验收分开记录。
        </p>
        {ready && (
          <p>
            新建任务使用已授权的默认发布流程：链接＋标题 → 下载校验 → 处理校验 →
            上传 → 逐平台提交 → 跟踪。不会恢复或重发历史任务。
          </p>
        )}
        {!!readiness.blockers.length && (
          <ul className="break">
            {readiness.blockers.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        )}
        <p>
          本地今日剩余预算：
          {unknownBudget
            ? "未知"
            : `${readiness.remaining_local_submissions} / ${readiness.daily_submission_limit} 次`}
          （北京时间）。这是保守本地预算，不是官方剩余额度；外部手动投稿可能导致官方拒绝。
        </p>
        <p>
          三平台分别提交，每个平台一次，最多消耗3次；每日本地上限3次。官方拒绝时暂停，不支付、不盲目重试。
        </p>
        <p>
          平台成片质量未验收：provider成功不代表平台已公开或完整成片通过，B站2秒故障风险仍需核对。
        </p>
        <p>
          平台原生定时尚未验收；保留设置，不会回退为立即发布。处理生成成片不要求先确认样片。
        </p>
        <button className="secondary" onClick={() => void refresh()}>
          刷新发布就绪状态
        </button>
      </div>
      <div className="capability-list">
        {[
          ["视频上传", capabilities?.upload],
          ["立即发布", capabilities?.immediate],
          ["平台原生定时", capabilities?.native_schedule],
          ["免费扣次规则", capabilities?.quota],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <span className="badge neutral">
              {value === "SUPPORTED"
                ? "已核查"
                : value === "UNSUPPORTED"
                  ? "不支持"
                  : "未验收"}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
