const providerReasons: Record<string, string> = {
  provider_reported_failure: "服务商报告失败，请核对逐平台记录；不会自动重投",
  provider_tracking_needs_attention: "自动跟踪需要核对，不会盲目重投",
  provider_tracking_pending: "后台持续跟踪平台结果，无需手工推进",
  provider_success_unverified: "服务商报告成功，平台公开状态与成片质量尚未验收",
  provider_reported_success: "服务商报告成功，平台公开状态与成片质量尚未验收",
};
export function providerReasonLabel(reason: string): string {
  return providerReasons[reason] ?? reason;
}
