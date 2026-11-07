import { useEffect, useState } from "react";
import { getHardwareStatus, type HardwareStatus } from "../api";
import { hardwareReasons } from "../mediaLabels";

export default function HardwareStatusPanel() {
  const [status, setStatus] = useState<HardwareStatus | null>(null);
  useEffect(() => {
    let alive = true;
    getHardwareStatus()
      .then((value) => {
        if (alive) setStatus(value);
      })
      .catch(() => {
        if (alive) setStatus(null);
      });
    return () => {
      alive = false;
    };
  }, []);
  const report = status?.report;
  return (
    <section className="panel padded" aria-label="硬件加速检测结果">
      <h2>硬件加速</h2>
      {report ? (
        <>
          <p>{report.device_name || "本机显卡"}</p>
          <p>{hardwareReasons[report.reason] || "检测结果不可用"}</p>
          <small>
            {status?.stale
              ? "上次检测结果；任务开始前重新核验"
              : "最近检测结果；不表示有任务正在使用GPU"}
          </small>
        </>
      ) : (
        <p>尚未检测。选择自动或混合模式的新任务开始时，会串行检查硬件能力。</p>
      )}
      <p>
        自动模式不可用时明确回退CPU；强制混合模式检测失败会暂停。不会自动更新驱动。
      </p>
      <small>GPU解码与缩放快速通道暂未启用，等待本机等价性和画质验证。</small>
    </section>
  );
}
