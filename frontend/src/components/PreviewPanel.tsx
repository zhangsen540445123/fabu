import { useEffect, useState } from "react";
import {
  approvePreview,
  createPreview,
  getJobPreviews,
  getOperation,
  getPreview,
  resumeJob,
  type Job,
  type MediaOperation,
  type Preview,
} from "../api";
export default function PreviewPanel({
  job,
  refresh,
}: {
  job: Job;
  refresh: () => Promise<void>;
}) {
  const [operation, setOperation] = useState<MediaOperation | null>(null),
    [sample, setSample] = useState<Preview | null>(null),
    [checked, setChecked] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [custom, setCustom] = useState(false),
    [start, setStart] = useState("0"),
    [end, setEnd] = useState("15");
  useEffect(() => {
    let alive = true;
    getJobPreviews(job.id)
      .then((rows) => {
        if (alive && rows[0]) setOperation(rows[0]);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [job.id]);
  useEffect(() => {
    if (!operation) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const id = operation.id;
    async function poll() {
      try {
        const row = await getOperation(id);
        if (!alive) return;
        if (row.state === "SUCCEEDED" && row.result) {
          const preview = await getPreview(row.result.preview_id);
          if (alive) {
            setSample(preview);
            setBusy(false);
          }
        } else if (row.state === "FAILED") {
          setError("样片生成失败或已中断，请检查任务配置后重试。");
          setBusy(false);
        } else {
          setBusy(true);
          timer = setTimeout(() => void poll(), 1000);
        }
      } catch (e) {
        if (alive) {
          setError(e instanceof Error ? e.message : "无法读取样片状态");
          setBusy(false);
        }
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [operation?.id]);
  async function generate() {
    setError("");
    setBusy(true);
    setChecked(false);
    setSample(null);
    try {
      let windows: [number, number][] | undefined;
      if (custom) {
        const a = Number(start),
          b = Number(end);
        if (!Number.isFinite(a) || !Number.isFinite(b) || a < 0 || b <= a)
          throw new Error("请填写有效的样片起止时间。");
        windows = [[Math.round(a * 1000), Math.round(b * 1000)]];
      }
      setOperation(await createPreview(job, windows));
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
      setBusy(false);
    }
  }
  async function confirm() {
    if (!sample || !checked) return;
    setBusy(true);
    setError("");
    try {
      await approvePreview(sample.id);
      await resumeJob(job);
      await refresh();
      setSample({ ...sample, approved: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "确认失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="preview-panel">
      <h3>先看样片，再确认预设</h3>
      <p>
        默认检查主片15秒，并覆盖片头片尾及关键图层边界。只生成本地文件，不会发布。
      </p>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={custom}
          onChange={(e) => setCustom(e.target.checked)}
        />
        自定义样片区间
      </label>
      {custom && (
        <div className="preset-fields">
          <label>
            开始时间（秒）
            <input
              type="number"
              min={0}
              step={0.1}
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            结束时间（秒）
            <input
              type="number"
              min={0}
              step={0.1}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
        </div>
      )}
      <button
        className="secondary"
        disabled={busy || job.content_frozen}
        onClick={() => void generate()}
      >
        {busy && !sample ? "生成样片中…" : "生成样片"}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {sample && (
        <>
          <div className="preview-clips">
            {sample.clips.map((clip) => (
              <article key={clip.index}>
                <header>
                  <strong>{clip.proxy_label}</strong>
                  <small>
                    {(clip.start_ms / 1000).toFixed(1)}–
                    {(clip.end_ms / 1000).toFixed(1)}秒
                  </small>
                </header>
                <video controls preload="metadata" src={clip.proxy_url} />
                <a className="media-link" href={clip.official_url}>
                  下载正式HEVC样片
                </a>
              </article>
            ))}
          </div>
          <p>
            上方是兼容浏览器的观看代理，不能替代正式画质样片。请检查正式样片的构图、文字、声音和同步。
          </p>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            我已检查正式样片的画面与声音
          </label>
          <button
            className="primary"
            disabled={
              !checked || busy || job.content_frozen || job.state !== "PAUSED"
            }
            onClick={() => void confirm()}
          >
            确认预设并继续处理
          </button>
          <p>
            确认绑定处理参数、素材与引擎版本；后续合规视频可复用，不必每天重复确认。
          </p>
        </>
      )}
    </section>
  );
}
