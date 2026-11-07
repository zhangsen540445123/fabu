export const hardwareReasons: Record<string, string> = {
  cpu_requested: "用户选择CPU兼容模式",
  nvenc_verified: "NVENC格式与解码探测通过；实际画质和速度以成片验证为准",
  driver_incompatible: "驱动与当前NVENC编码器不兼容",
  no_device: "未检测到可用的NVIDIA显卡",
  encoder_unavailable: "当前FFmpeg不具备可用的NVENC编码器",
  probe_failed: "GPU编码或输出验证未通过",
  inventory_unavailable: "无法可靠获取显卡与驱动信息",
};
export const mediaPhases: Record<string, string> = {
  download: "准备来源",
  input_validation: "校验源文件与处理素材",
  hardware_check: "检测硬件编码能力",
  cache_validation: "检查已有成片",
  encoding: "编码中",
  output_validation: "完整校验成片视频与音轨",
  total: "总耗时",
};
