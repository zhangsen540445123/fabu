import { useEffect, useState } from "react";
import {
  ApiError,
  getAssets,
  getCatalog,
  getPresets,
  importAsset,
  savePreset,
  validatePreset,
  type Asset,
  type CatalogStep,
  type PresetDocument,
  type PresetSummary,
  type PresetVersion,
  type ValidationIssue,
} from "../api";
import StepList from "../components/StepList";
function documentOf(version: PresetVersion): PresetDocument {
  return {
    name: version.name,
    schema_version: version.schema_version,
    steps: version.steps,
    output: version.output,
  };
}
const blank: PresetDocument = {
  name: "新建处理预设",
  schema_version: 1,
  steps: [],
  output: { crf: 20 },
};
export default function PipelinePage({
  onChanged,
}: { onChanged?: () => void } = {}) {
  const [presets, setPresets] = useState<PresetSummary[]>([]),
    [catalog, setCatalog] = useState<CatalogStep[]>([]),
    [assets, setAssets] = useState<Asset[]>([]),
    [current, setCurrent] = useState<PresetVersion | null>(null),
    [doc, setDoc] = useState<PresetDocument | null>(null),
    [issues, setIssues] = useState<ValidationIssue[]>([]),
    [checking, setChecking] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [assetPath, setAssetPath] = useState(""),
    [assetType, setAssetType] = useState<Asset["media_type"]>("image");
  useEffect(() => {
    let alive = true;
    Promise.all([getPresets(), getCatalog(), getAssets()])
      .then(([rows, defs, files]) => {
        if (alive) {
          setPresets(rows);
          setCatalog(defs);
          setAssets(files);
          const first = rows[0]?.version ?? null;
          setCurrent(first);
          setDoc(first ? documentOf(first) : blank);
        }
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "读取失败");
      });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (!doc) return;
    let alive = true;
    setChecking(true);
    validatePreset(doc)
      .then((result) => {
        if (alive) {
          setIssues(result.issues);
          setChecking(false);
        }
      })
      .catch((e) => {
        if (alive) {
          setIssues([
            {
              step_id: "output",
              code: "validation_unavailable",
              message: e instanceof Error ? e.message : "校验暂不可用",
            },
          ]);
          setChecking(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [doc]);
  function choose(id: string) {
    const version = presets.find((p) => p.version.id === id)?.version;
    if (version) {
      setCurrent(version);
      setDoc(documentOf(version));
      setMessage("");
    }
  }
  function add(def: CatalogStep) {
    if (!doc) return;
    const params: Record<string, unknown> = {};
    for (const [name, prop] of Object.entries(def.parameters.properties)) {
      if ("default" in prop) params[name] = prop.default;
    }
    setDoc({
      ...doc,
      steps: [
        ...doc.steps,
        {
          id: crypto.randomUUID(),
          kind: def.kind,
          enabled: true,
          scope: def.scopes[0],
          params,
          asset_ids: [],
        },
      ],
    });
    setMessage("");
  }
  async function save(copy = false) {
    if (!doc) return;
    setBusy(true);
    setError("");
    try {
      const version = await savePreset(
        copy ? null : (current?.preset_id ?? null),
        copy ? { ...doc, name: `${doc.name} 副本` } : doc,
      );
      setCurrent(version);
      setDoc(documentOf(version));
      setPresets(await getPresets());
      setMessage(
        `版本 ${version.revision} 已保存。任务将按所选版本自动处理，样片检查可选。`,
      );
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
      if (e instanceof ApiError && e.issues.length) setIssues(e.issues);
    } finally {
      setBusy(false);
    }
  }
  async function importFile(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await importAsset(assetPath, assetType);
      setAssets(await getAssets());
      setAssetPath("");
      setMessage("素材已复制到本地素材库，原文件不会改动。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "素材导入失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="page-heading">
        <div>
          <span className="eyebrow">PIPELINE / 可编排处理</span>
          <h1>让每一步处理，都可预览。</h1>
          <p>勾选步骤、调整参数、保存新版本。原构图与原声始终是起点。</p>
        </div>
        <button
          className="secondary"
          disabled={busy || !doc}
          onClick={() => {
            setCurrent(null);
            setDoc({ ...blank, steps: [] });
            setMessage("");
          }}
        >
          新建预设
        </button>
      </section>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {!doc ? (
        <div className="panel padded">读取预设…</div>
      ) : (
        <div className="pipeline-grid">
          <aside className="pipeline-sidebar">
            <section className="panel padded">
              <h2>处理方式</h2>
              <p>
                时间处理 → 画面 → 主片图层 → 拼接 →
                整片效果。顺序由后端校验，不会悄悄重排。
              </p>
              <div className="module-list">
                {catalog.map((def) => (
                  <button
                    key={def.kind}
                    className="module-button"
                    disabled={
                      busy ||
                      (!def.multiple &&
                        doc.steps.some((s) => s.kind === def.kind)) ||
                      doc.steps.length >= 50
                    }
                    aria-label={`添加${def.label}`}
                    onClick={() => add(def)}
                  >
                    <span>{def.label}</span>
                    <span aria-hidden="true">＋</span>
                  </button>
                ))}
              </div>
            </section>
            <section className="panel padded">
              <h2>本地素材库</h2>
              <p>装饰图、字体和字幕，与YouTube原封面分开管理。</p>
              <form onSubmit={importFile}>
                <label>
                  素材类型
                  <select
                    disabled={busy}
                    value={assetType}
                    onChange={(e) =>
                      setAssetType(e.target.value as Asset["media_type"])
                    }
                  >
                    <option value="image">图片 PNG / JPEG</option>
                    <option value="video">片头片尾视频</option>
                    <option value="font">字体 TTF / OTF</option>
                    <option value="subtitle">UTF-8 SRT字幕</option>
                  </select>
                </label>
                <label>
                  素材绝对路径
                  <input
                    required
                    disabled={busy}
                    value={assetPath}
                    onChange={(e) => setAssetPath(e.target.value)}
                    placeholder="D:\素材\图片.png"
                  />
                </label>
                <button className="secondary wide" disabled={busy}>
                  导入素材
                </button>
              </form>
              <div className="asset-inventory">
                {assets.slice(0, 10).map((a) => (
                  <div key={a.id}>
                    <span title={a.display_name}>{a.display_name}</span>
                    <small>{a.media_type}</small>
                  </div>
                ))}
                {!assets.length && <p>尚未导入素材</p>}
              </div>
            </section>
          </aside>
          <fieldset disabled={busy} className="pipeline-workspace">
            <div className="panel padded">
              <div className="preset-fields">
                <label>
                  选择已有预设
                  <select
                    value={current?.id ?? ""}
                    onChange={(e) => choose(e.target.value)}
                  >
                    <option value="">未保存的新预设</option>
                    {presets.map((p) => (
                      <option key={p.version.id} value={p.version.id}>
                        {p.version.name} · v{p.version.revision}
                        {p.approved ? " · 样片已确认" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  预设名称
                  <input
                    value={doc.name}
                    maxLength={100}
                    onChange={(e) => setDoc({ ...doc, name: e.target.value })}
                  />
                </label>
              </div>
              <div className="pipeline-baseline">
                <span className="badge">固定基础输出</span>
                <strong>
                  HEVC · CRF{String(doc.output.crf ?? 20)} · 30fps
                </strong>
                <p>
                  自动识别横竖比例 · Lanczos缩放 ·
                  保留受支持的原声采样率与声道。这里不是AI超分，也不保证规避查重。
                </p>
                <details>
                  <summary>高级输出参数</summary>
                  <label>
                    CRF质量参数
                    <input
                      type="number"
                      min={0}
                      max={51}
                      value={Number(doc.output.crf ?? 20)}
                      onChange={(e) =>
                        setDoc({
                          ...doc,
                          output: {
                            ...doc.output,
                            crf: Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                </details>
              </div>
            </div>
            <StepList
              steps={doc.steps}
              catalog={catalog}
              assets={assets}
              issues={issues}
              onChange={(steps) => setDoc({ ...doc, steps })}
            />
            <div className="panel padded pipeline-save">
              {issues
                .filter((e) => ["output", "assets"].includes(e.step_id))
                .map((issue, i) => (
                  <p role="alert" className="error" key={i}>
                    {issue.message}
                  </p>
                ))}
              <div className="button-row">
                <button
                  className="primary"
                  disabled={
                    busy || checking || issues.length > 0 || !doc.name.trim()
                  }
                  onClick={() => void save()}
                >
                  {current ? "保存新版本" : "保存预设"}
                </button>
                <button
                  className="secondary"
                  disabled={busy || checking || issues.length > 0}
                  onClick={() => void save(true)}
                >
                  另存为副本
                </button>
                {checking && <small>校验步骤中…</small>}
              </div>
              {message && <p role="status">{message}</p>}
              <p>
                保存不会改动正在运行的任务。新任务下载完成并通过完整性校验后自动按所选预设处理，样片检查可选；发布仍需通过样片确认等专用预检。
              </p>
            </div>
          </fieldset>
        </div>
      )}
    </>
  );
}
