import type { Asset, CatalogStep, StepSpec, ValidationIssue } from "../api";
import StepParameters from "./StepParameters";
export default function StepList({
  steps,
  catalog,
  assets,
  issues,
  onChange,
}: {
  steps: StepSpec[];
  catalog: CatalogStep[];
  assets: Asset[];
  issues: ValidationIssue[];
  onChange: (steps: StepSpec[]) => void;
}) {
  function replace(index: number, value: StepSpec) {
    onChange(steps.map((s, i) => (i === index ? value : s)));
  }
  function move(index: number, delta: number) {
    const next = [...steps];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    onChange(next);
  }
  if (!steps.length)
    return (
      <div className="pipeline-empty">
        <span aria-hidden="true">◌</span>
        <h3>附加处理全部关闭</h3>
        <p>保留原构图与原声，仅执行已确认的基础转码。</p>
      </div>
    );
  return (
    <ol className="step-list">
      {steps.map((step, index) => {
        const def = catalog.find((d) => d.kind === step.kind);
        if (!def) return null;
        const errors = issues.filter((e) => e.step_id === step.id);
        return (
          <li
            key={step.id}
            className={`step-card ${step.enabled ? "" : "step-disabled"} ${errors.length ? "step-invalid" : ""}`}
          >
            <header className="step-header">
              <span className="step-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={step.enabled}
                  onChange={(e) =>
                    replace(index, { ...step, enabled: e.target.checked })
                  }
                />
                <strong>{def.label}</strong>
              </label>
              <div className="step-actions">
                <button
                  className="quiet"
                  aria-label={`上移${def.label}`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  className="quiet"
                  aria-label={`下移${def.label}`}
                  disabled={index === steps.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  className="quiet"
                  aria-label={`移除${def.label}`}
                  onClick={() => onChange(steps.filter((_, i) => i !== index))}
                >
                  移除
                </button>
              </div>
            </header>
            {step.enabled && (
              <>
                <label className="scope-field">
                  作用范围
                  <select
                    value={step.scope}
                    onChange={(e) =>
                      replace(index, {
                        ...step,
                        scope: e.target.value as "main" | "full",
                      })
                    }
                  >
                    {def.scopes.map((v) => (
                      <option key={v} value={v}>
                        {v === "main"
                          ? "仅主视频（不包含片头片尾）"
                          : "整片（包含片头片尾）"}
                      </option>
                    ))}
                  </select>
                </label>
                <StepParameters
                  step={step}
                  definition={def}
                  assets={assets}
                  onChange={(s) => replace(index, s)}
                />
              </>
            )}
            {errors.map((error, i) => (
              <p className="error" role="alert" key={i}>
                {error.message}
              </p>
            ))}
          </li>
        );
      })}
    </ol>
  );
}
