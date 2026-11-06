import type { Asset, CatalogStep, FieldSchema, StepSpec } from "../api";
const labels: Record<string, string> = {
  start_ms: "开始时间（毫秒）",
  end_ms: "结束时间（毫秒）",
  x: "横向位置（0–1）",
  y: "纵向位置（0–1）",
  width: "宽度比例",
  height: "高度比例",
  rate: "速度倍数",
  brightness: "亮度",
  contrast: "对比度",
  saturation: "饱和度",
  aspect: "画布比例",
  top_fraction: "顶部装饰区比例",
  bottom_fraction: "底部装饰区比例",
  top_image_id: "顶部装饰图片",
  bottom_image_id: "底部装饰图片",
  background: "背景颜色",
  opacity: "不透明度（0–1）",
  text: "文字内容",
  font_id: "字体文件",
  font_size: "字号",
  color: "文字颜色",
  subtitle_id: "字幕文件",
  position: "字幕位置",
  duration_ms: "片段时长（毫秒）",
  keep_audio: "保留片段声音",
  fit: "比例不同时",
  gain_db: "音量增益（dB）",
  fade_in_ms: "淡入时长（毫秒）",
  fade_out_ms: "淡出时长（毫秒）",
};
const choices: Record<string, string> = {
  reject: "拒绝不同画面比例",
  contain: "完整容纳并补边",
  top: "顶部",
  bottom: "底部",
};
export function assetIds(step: StepSpec): string[] {
  if (["image_layer", "intro", "outro"].includes(step.kind))
    return step.asset_ids;
  const references = [
    "font_id",
    "subtitle_id",
    "top_image_id",
    "bottom_image_id",
  ]
    .map((key) => step.params[key])
    .filter((value): value is string => typeof value === "string" && !!value);
  return [...new Set(references)];
}
export default function StepParameters({
  step,
  definition,
  assets,
  onChange,
}: {
  step: StepSpec;
  definition: CatalogStep;
  assets: Asset[];
  onChange: (step: StepSpec) => void;
}) {
  function set(name: string, value: unknown) {
    const next = { ...step, params: { ...step.params, [name]: value } };
    onChange({ ...next, asset_ids: assetIds(next) });
  }
  function field(name: string, property: FieldSchema) {
    const label = labels[name] ?? name;
    const schema = property.anyOf?.find((p) => p.type !== "null") ?? property;
    const value = name in step.params ? step.params[name] : property.default;
    if (name.endsWith("_id")) {
      const kind =
        name === "font_id"
          ? "font"
          : name === "subtitle_id"
            ? "subtitle"
            : "image";
      return (
        <label key={name}>
          {label}
          <select
            value={String(value ?? "")}
            onChange={(e) => set(name, e.target.value || null)}
          >
            <option value="">请选择素材</option>
            {assets
              .filter((a) => a.media_type === kind)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))}
          </select>
        </label>
      );
    }
    if (schema.type === "boolean")
      return (
        <label key={name} className="inline-check">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => set(name, e.target.checked)}
          />
          {label}
        </label>
      );
    if (schema.enum)
      return (
        <label key={name}>
          {label}
          <select
            value={String(value ?? schema.enum[0])}
            onChange={(e) => set(name, e.target.value)}
          >
            {schema.enum.map((v) => (
              <option key={v} value={v}>
                {choices[v] ?? v}
              </option>
            ))}
          </select>
        </label>
      );
    if (name === "text")
      return (
        <label key={name} className="span-two">
          {label}
          <textarea
            rows={3}
            value={String(value ?? "")}
            maxLength={1000}
            onChange={(e) => set(name, e.target.value)}
            placeholder="中文和符号按原文字面烧录；字体需要覆盖所用字符"
          />
        </label>
      );
    if (name === "color" || name === "background")
      return (
        <label key={name}>
          {label}
          <input
            type="color"
            value={String(value ?? "#000000")}
            onChange={(e) => set(name, e.target.value)}
          />
        </label>
      );
    const numeric = schema.type === "number" || schema.type === "integer";
    return (
      <label key={name}>
        {label}
        <input
          type={numeric ? "number" : "text"}
          value={value == null ? "" : String(value)}
          min={schema.minimum ?? schema.exclusiveMinimum}
          max={schema.maximum}
          step={schema.type === "integer" ? 1 : 0.01}
          onChange={(e) =>
            set(
              name,
              e.target.value === ""
                ? null
                : numeric
                  ? Number(e.target.value)
                  : e.target.value,
            )
          }
        />
      </label>
    );
  }
  return (
    <div className="step-parameters">
      {["image_layer", "intro", "outro"].includes(step.kind) && (
        <label className="span-two">
          {step.kind === "image_layer" ? "图片素材" : "片段素材"}
          <select
            value={step.asset_ids[0] ?? ""}
            onChange={(e) =>
              onChange({
                ...step,
                asset_ids: e.target.value ? [e.target.value] : [],
              })
            }
          >
            <option value="">请选择素材</option>
            {assets
              .filter(
                (a) =>
                  a.media_type === "image" ||
                  (step.kind !== "image_layer" && a.media_type === "video"),
              )
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))}
          </select>
        </label>
      )}
      {Object.entries(definition.parameters.properties).map(([name, schema]) =>
        field(name, schema),
      )}
      {["intro", "outro"].includes(step.kind) && (
        <p className="span-two">
          图片必须填写时长；视频留空使用全段，最长60秒。不会默认裁切不同画面比例。
        </p>
      )}
    </div>
  );
}
