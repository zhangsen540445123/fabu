# Fabu Media Pipeline Implementation Plan — 下载、处理、预设与预览

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地实现YouTube URL到已验证成片，并用列表界面编排首版处理模块与保存可复用预设。

**Architecture:** 固定入口探测→结构化步骤验证→媒体计划→过滤器/辅助文件→统一渲染→独立校验；预览和正式输出共用编译器。没有任何一步自行调用发布适配器。

**Tech Stack:** Python 3.12、yt-dlp、FFmpeg/ffprobe、React/TypeScript、pytest、Vitest/Playwright。

**Spec:** `D:\auto-deploy\docs\superpowers\specs\2026-11-07-fabu-v1-design.md`，重点第4/5/10节。

## Global Constraints

继承`D:\auto-deploy\docs\superpowers\plans\2026-11-07-fabu-v1-roadmap.md`的全部约束/契约。默认MP4/HEVC/libx265 fast/CRF20/30fps/yuv420p/Lanczos，保留受支持的原声布局/采样率。附加编辑全关；不覆盖源、不自动加黑边、不暗中裁切；无真实上传/投稿。短样片通过不代表全片或其他比例通过。

## Review Focus

- 中文/emoji/冒号/引号及字幕/字体路径不能注入过滤器：M07。
- Worker重启不会消费未完成渲染文件：M03/M09；跨平台重复由P03覆盖。
- SAR、90/270旋转、奇数尺寸、裁切与布局组合：M01/M04/M05。
- 预约窗口由P03/P06覆盖；媒体不得失败后回退原片或擅自缩短处理来赶时间：M03。
- 预览与正式计划一致、变更素材/配置使旧确认无效，磁盘不足/坏源暂停：M03/M09。

---

## 文件与模型边界

`D:\auto-deploy\src\fabu\media\probe.py`负责MediaInfo；`D:\auto-deploy\src\fabu\media\models.py`由M03定义MediaPlan/OutputProfile/OutputManifest，M04加入InputBundle并消费F05的StepSpec；compiler只生成计划不启动进程；renderer不自行宣布产物可发布；validator唯一生成已验证OutputManifest。过滤器构建按geometry/time/layers/composition拆分，拒绝通用任意脚本节点。

### Task 1: M01 — ffprobe探测与几何规则

**依赖：** F02/F05。**Files：** `D:\auto-deploy\src\fabu\media\probe.py`、`D:\auto-deploy\src\fabu\media\geometry.py`、`D:\auto-deploy\tests\media\test_probe.py`、`D:\auto-deploy\tests\media\fixtures\probe_cases.json`。

**Interfaces：** 定义MediaInfo/Size；`probe_media(path: Path, runner: Callable) -> MediaInfo`；`fit_output(info: MediaInfo, crop_ratio: Fraction | None = None) -> Size`。只允许本地文件，不能让ffprobe任意取网；attached-picture不当主视频轨，多个无法明确选择的主轨暂停。

- [x] RED：`test_geometry_matrix`断言16:9→(2560,1440)、4:3→(1920,1440)、9:16→(1440,2560)、1:1→(1440,1440)；1280×720+90度旋转输出(1440,2560)；720×576 SAR16/15得到4:3。`test_hdr_or_ambiguous_tracks_pause`拒绝未支持HDR/多音轨/不明颜色。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_probe.py' -q`观察RED。
- [x] 实现rotation/display matrix/SAR规范化和偶数尺寸舍入：统一比例因子，最近偶数，约束不越目标框；极窄输入产生小于2像素的边时拒绝，不拉伸；非直角/动态分辨率暂停。
- [x] 音频保留44.1/48kHz、单/双声道；无声要求显式处理，多声道/多轨不丢弃。默认色彩支持必须有证据，未知不简单贴BT.709标签。
- [x] GREEN：同命令PASS，并用带四角文字的合成旋转/SAR短素材做`media`集成测试；没有可用ffprobe不得略过实机验收。
- [x] 提交`feat: probe media and preserve display geometry`，只stageFiles及必要包文件。

### Task 2: M02 — YouTube下载与原缩略图

**依赖：** F02/F05、M01。**Files：** `D:\auto-deploy\src\fabu\media\source.py`、`D:\auto-deploy\src\fabu\media\thumbnail.py`、`D:\auto-deploy\tests\media\test_source.py`、`D:\auto-deploy\tests\media\test_thumbnail.py`。

**Interfaces：** `parse_youtube_url(url: str) -> str`返回video_id；`download_source(video_id: str, workspace: Path, runner: Callable) -> SourceManifest`；`fetch_thumbnail(video_id: str, transport: Callable) -> AssetRef`。网络/工具依赖可替换为fake。

- [ ] RED：`test_watch_short_and_shortlink_same_id`断言三个格式得同ID；`test_reject_playlist_live_and_arbitrary_host`拒绝频道/播放列表/直播/file/localhost URL；`test_audio_required`静音下载不算成功；`test_thumbnail_placeholder_falls_back`返回真实最大有效图而非占位图；全失败PAUSED且不提取视频帧。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_source.py' 'D:\auto-deploy\tests\media\test_thumbnail.py' -q`观察RED。
- [ ] 检查已锁定yt-dlp版本的实际运行依赖，把“可启动”和“能下载YouTube”分开验证；实现结构化格式读取/单条下载，优先目标附近的可用清晰度和30fps候选，保留选用format证据；分轨需要合并，格式失效有限重选并重验，不自动读取cookies/绕过访问限制。
- [ ] 原封面仅从已验证YouTube图像地址获取，校验重定向目标、HTTP/解码/真实尺寸/占位图；设置超时、响应大小上限和图片像素上限，不允许用户传入任意远程封面地址。标题只作数据，不构成路径。
- [ ] GREEN：同命令PASS；实施时仅在下载范围获得授权后用用户示例做真实下载检查，记录含音频/尺寸/封面且不上传。下载环境阻断时保留失败证据，不切到浏览器会话抓取。
- [ ] 提交`feat: download a single video and original thumbnail`，仅Files；实际源文件保留仓库外。

### Task 3: M03 — 基础转码与成片验证（S2）

**依赖：** M01–M02、F04。**Files：** `D:\auto-deploy\src\fabu\media\models.py`、`D:\auto-deploy\src\fabu\media\baseline.py`、`D:\auto-deploy\src\fabu\media\renderer.py`、`D:\auto-deploy\src\fabu\media\validator.py`、`D:\auto-deploy\tests\media\test_baseline.py`、`D:\auto-deploy\tests\media\test_render_recovery.py`。

**Interfaces：** 定义OutputProfile/MediaPlan/RenderCommand/OutputManifest；`build_baseline_plan(source: MediaInfo) -> MediaPlan`；`render(plan: MediaPlan, workspace: Path, cancel: Event) -> Path`返回未验证临时产物；`validate_output(candidate: Path, plan: MediaPlan) -> OutputManifest`；`promote_output(manifest: OutputManifest, destination: Path) -> OutputManifest`同卷原子提升并更新路径。

- [x] RED：`test_baseline_preserves_approved_profile`断言CRF20/fast/30fps/yuv420p且无附加效果；`test_failed_render_cannot_be_published`断言退出失败、磁盘不足、截断文件均无正式OutputManifest；`test_source_hash_unchanged`断言前后摘要相同。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_baseline.py' 'D:\auto-deploy\tests\media\test_render_recovery.py' -q`观察RED。
- [x] 实现串行转码、真实进度与空间预检/持续检查；暂按已有基准CPU线程限制pools6/frame-threads2，不默启GPU。音频与画面按探测结果，不用固定44100强转所有源。
- [x] 独立全解码并检查帧率/偶数宽高/SAR/旋转/色彩/音轨。工程初值：视频时长误差≤2帧，AAC端点误差≤2帧+2×1024/采样率秒；这只验时间线边界，不证明内容同步，另用闪光/点击合成素材测试同步。失败不提升、不回退原片。
- [x] GREEN：同命令PASS；实际生成同基准区间03:00–03:15（450视频帧），与原验收样片对照；再用完整源视频验证时长/解码/原音与资源占用。编码时长/体积只报告实测，不外推样片压缩比例。
- [x] 提交`feat: render and validate baseline media outputs`，限定Files；S2交付是本地成片，不是自动发布。

### Task 4: M04 — 步骤模型、顺序校验与计划编译

**依赖：** F05、M03。**Files：** 修改`D:\auto-deploy\src\fabu\media\models.py`、`D:\auto-deploy\src\fabu\presets.py`；新建`D:\auto-deploy\src\fabu\media\compiler.py`、`D:\auto-deploy\src\fabu\media\registry.py`、`D:\auto-deploy\tests\media\test_compiler.py`。

**Interfaces：** 定义InputBundle，复用F05的StepSpec信封并登记类型化参数；`validate_steps(steps: list[StepSpec]) -> list[ValidationIssue]`；`compile_plan(bundle: InputBundle, preset: PresetVersion) -> MediaPlan`；`build_baseline_plan`逻辑复用M03而非再写一套。

- [x] RED：`test_all_disabled_matches_baseline`断言输出配置/几何/时间与基础计划一致；`test_invalid_order_is_rejected_not_sorted`断言翻转位于文字之后被拒绝且原顺序未被偷偷修改；`test_unknown_step_or_nan_is_rejected`拒绝未注册kind、NaN/Infinity；`test_digest_tracks_semantics`断言参数/素材/引擎变动改digest，改显示名称不改。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_compiler.py' -q`观察RED。
- [x] 实现有限内置登记表、参数schema、作用域main/full、前置依赖和唯一输出节点；crop/flip/color排序可以表达，时间段→变速→作用域图层→拼接→整片层→输出的边界不随意交换。
- [x] 编译生成确定计划、期望时长/尺寸/音频与受控中间文件需求；不在编译器内启动进程，不接受raw FFmpeg/shell。空间估算必须包含辅助素材与预览。
- [x] GREEN：同命令PASS；M03基础回归不变；拒绝会导致重复输出、跨作用域拖动或裁切尺寸无效的组合。
- [x] 提交`feat: compile validated media pipelines`，限定Files；下游新增模块只扩展登记表明确类型。

### Task 5: M05 — 裁切、翻转、调色、画布布局

**依赖：** M04。**Files：** `D:\auto-deploy\src\fabu\media\filters_geometry.py`、`D:\auto-deploy\tests\media\test_geometry_filters.py`，修改`D:\auto-deploy\src\fabu\media\registry.py`。

**Interfaces：** `compile_geometry(steps: list[StepSpec], input_size: Size) -> GeometryResult`；结果含video_filters/output_size/coordinate_map，由compiler消费。crop归一化矩形相对当前已处理画面，布局可选16:9/9:16/1:1，主体等比容纳，上/下各至多一静态图。

- [x] RED：`test_crop_uses_post_rotation_coordinates`检查90度非对称素材裁切区域正确；`test_explicit_crop_changes_output_ratio`不恢复原比例；`test_layout_does_not_stretch_subject`保持圆形不成椭圆；`test_color_defaults_are_identity`亮度0、对比度1、饱和度1无额外效果。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_geometry_filters.py' -q`观察RED。
- [x] 实现crop/hflip/基础色调/布局，参数数值有限且有界；首次工程范围亮度[-1,1]、对比度[0,2]、饱和度[0,3]，越界拒绝不钳制；布局明确显示留白和主体缩小。
- [x] 将合成四角标记/圆形/彩条生成固定短片验证滤镜效果；测试flip前后裁切的差异是用户顺序决定，不默认改正内容。
- [x] GREEN：同命令PASS和`media`帧图检查通过；无布局/裁切情况下保持第4节默认无新增边框。
- [x] 提交`feat: add explicit frame editing and canvas layouts`，限定Files。

### Task 6: M06 — 截取、变速与音频效果

**依赖：** M04。**Files：** `D:\auto-deploy\src\fabu\media\filters_time.py`、`D:\auto-deploy\tests\media\test_time_filters.py`，修改登记表。

**Interfaces：** `compile_timing(info: MediaInfo, steps: list[StepSpec]) -> TimingResult`；`map_main_time(source_ms: int, trim_start_ms: int, speed: Fraction) -> int`；TimingResult含main_duration_ms/video_filters/audio_filters。仅一连续片段，速度0.5–2.0，main/full音量/淡入淡出按设计作用域。

- [x] RED：`test_trim_speed_expected_duration`断言原片10–30秒截取后1.25倍=16000ms；`test_audio_and_video_share_timeline`同步闪光/点击在变速后误差≤1视频帧+1AAC帧；`test_bad_time_windows_rejected`拒绝结束越界、零长度、淡入淡出越界。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_time_filters.py' -q`观察RED。
- [x] 实现视频/音频同时截取和时间戳归零、保持音高的音频变时长；30fps规范化不额外改总速度；无音轨时按输入门槛暂停，不合成主片声音。
- [x] 音量/淡入淡出默认关闭，main/full同时启用显示叠加效果；左右声道不因水平翻转被交换；不自动降噪、标准化或混入音乐。
- [x] GREEN：同命令PASS并验证0.5/1/2倍、44.1/48kHz、单/双声道和长片末尾；确认片头片尾速度不受主片速度影响由M08组合测覆盖。
- [x] 提交`feat: add synchronized timing and audio controls`，限定Files。

### Task 7: M07 — 图片/文字图层与导入字幕

**依赖：** M04–M06、F05。**Files：** `D:\auto-deploy\src\fabu\media\filters_layers.py`、`D:\auto-deploy\src\fabu\media\subtitles.py`、`D:\auto-deploy\tests\media\test_layers.py`、`D:\auto-deploy\tests\media\test_subtitles.py`，修改登记表。

**Interfaces：** `compile_layers(steps: list[StepSpec], canvas: Size, scope_duration_ms: int, assets: dict[str,AssetRef]) -> LayerResult`；`parse_srt(path: Path, scope_duration_ms: int) -> list[Cue]`；LayerResult含filters/受控text文件，Cue记录start/end/text。不提供原始滤镜字段。

- [x] RED：`test_layer_order_and_scope`断言后层覆盖前层且main层不出现在片头；`test_literal_text_cannot_inject_filter`检查含`':;[]\\`及中文emoji的文本不改变过滤图；`test_srt_time_uses_edited_scope`按编辑后时间轴校验，越界拒绝；`test_missing_font_and_bad_utf8_fail_preflight`不静默替代。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_layers.py' 'D:\auto-deploy\tests\media\test_subtitles.py' -q`观察RED。
- [x] 实现PNG/JPEG静态层、文字层位置/尺寸/透明度[0,1]/时间/层次，文本写入受控UTF-8文件；所有素材/字体通过AssetRef，不直接信任用户路径或远程URL。
- [x] SRT仅UTF-8、有效递增区间、明确main/full计时；支持多行中文，字体存在性和所需能力提前检查；无自动ASR/翻译。SSRF/脚本能力不能经字幕样式或素材引用引入。
- [x] GREEN：同命令PASS；渲染并检查图层起止前/中/后帧、透明度和层级，特殊文字必须可见且没有副作用；兼容浏览器代理不替代正式画质样片。
- [x] 提交`feat: add safe layers and imported subtitles`，限定Files。

### Task 8: M08 — 片头片尾与整片合成

**依赖：** M05–M07。**Files：** `D:\auto-deploy\src\fabu\media\composition.py`、`D:\auto-deploy\tests\media\test_composition.py`，修改compiler/registry明确整片阶段。

**Interfaces：** `compose_segments(main: MediaPlan, intro: AssetRef | None, outro: AssetRef | None, params: dict, full_steps: list[StepSpec]) -> MediaPlan`；使用M01探测和已有滤镜模块，不重新手写另套转码参数。

- [x] RED：`test_total_duration_with_intro_speed_outro`断言2秒片头+上述16000ms主片+3秒片尾=21000ms；`test_full_layers_include_intro_main_layers_do_not`检查作用域；`test_aspect_mismatch_requires_explicit_fit`拒绝默认裁切；`test_silent_auxiliary_does_not_truncate_audio`验证主片没有被最短音轨截断。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_composition.py' -q`观察RED。
- [x] 实现各一个静态图/本地视频的硬切拼接，图片时长显式指定；辅助视频接受同样的色彩/旋转/音频门槛，几何差异只有用户显式同意留白适配时才能容纳。
- [x] 允许辅助段静音补齐时间，统一到主片受支持音频参数；主片图层在合成前、全片层在合成后，不强制多轮有损导出。
- [x] GREEN：同命令PASS并检查拼接边界前后帧/声音；M03–M07全部回归通过；全片和边界预览时间一致的用例留M09。
- [x] 提交`feat: compose intros outros and scoped effects`，限定Files。

### Task 9: M09 — 预览确认、缓存与处理快照

**依赖：** M08、F04–F05。**Files：** `D:\auto-deploy\src\fabu\media\preview.py`、`D:\auto-deploy\src\fabu\media\service.py`、`D:\auto-deploy\src\fabu\migrations\003_preview_approvals.sql`、`D:\auto-deploy\tests\media\test_preview.py`，修改presets。

**Interfaces：** `build_preview(plan: MediaPlan, windows: list[tuple[int,int]]) -> PreviewManifest`；`approve_preview(manifest: PreviewManifest) -> PreviewApproval`；`approval_matches(preset: PresetVersion, assets: dict[str,AssetRef], engine_compat_version: str) -> bool`；`process_job(job: JobSnapshot) -> OutputManifest`。审批绑定预设/素材/输出/引擎，不把样例源摘要要求应用于所有后续源。

- [x] RED：`test_preview_is_same_plan_window`比较正文/片头/图层出现边界与全片对应帧；`test_changed_asset_or_output_invalidates_approval`断言旧确认不能放行；`test_new_valid_source_reuses_approval`换合规源仍True；`test_new_source_out_of_range_pauses`越界不自动截短；`test_unapproved_output_cannot_enter_publish`拦截发布。运行`& 'D:\auto-deploy\.venv\Scripts\python.exe' -m pytest 'D:\auto-deploy\tests\media\test_preview.py' -q`观察RED。
- [x] 实现默认正文15秒、可选区间，额外覆盖片头片尾和定时层关键边界；取样基于最终成片时间轴，不能先截源片导致字幕错时。代理视频带“观看代理”标识，正式HEVC样片保留。
- [x] 缓存键=源/素材摘要+计划+引擎版本+窗口；读取前检查产物摘要/存在性。编辑预设生成新版本；任务开始冻结快照；失败恢复重跑未完成阶段，不把旧cache或半文件提升为新输出。
- [x] GREEN：同命令PASS；处理中修改全局预设不影响当前任务；磁盘不足、预览过期、缺库素材可解释暂停，不自动上传。
- [x] 提交`feat: version previews and gate approved processing presets`，限定Files。

### Task 10: M10 — 流水线编辑器与本地处理闭环（S4）

**依赖：** M09、F06。**Files：** `D:\auto-deploy\src\fabu\api\media.py`、`D:\auto-deploy\src\fabu\api\presets.py`、`D:\auto-deploy\frontend\src\pages\PipelinePage.tsx`、`D:\auto-deploy\frontend\src\components\StepList.tsx`、`D:\auto-deploy\frontend\src\components\StepParameters.tsx`、`D:\auto-deploy\frontend\src\components\PreviewPanel.tsx`、`D:\auto-deploy\frontend\src\pages\NewJobPage.tsx`、`D:\auto-deploy\frontend\src\__tests__\pipeline.test.tsx`、`D:\auto-deploy\frontend\tests\pipeline.spec.ts`、`D:\auto-deploy\tests\test_media_api.py`。

**Interfaces：** 认证API `/api/presets`、`/api/presets/{id}/versions`、`/api/assets`、`/api/previews`、`/api/previews/{id}/approve`；GET媒体仅按资源ID验证归属。接口返回后端ValidationIssue，前端不自行解释另一套顺序；JobSpec选择preset_version_id。

- [x] RED：UI测试`toggle_reorder_save_and_confirm`断言勾选加入、非法拖动给原因且不静默重排、保存新版本需新预览；`base_preset_preserves_audio_and_frame`断言所有附加项关闭；`topics_do_not_select_media_preset`断言改标题不换处理模板；API测试拒绝直接伪造approved字段。运行前端test及Python`test_media_api.py`观察RED。
- [x] 实现列表勾选/可拖动或上下移、参数与作用域、素材选择/预设复制、样片播放器/确认、任务当前阶段/错误。单次执行以冻结快照显示；不显示合并过滤器不存在的逐节点进度。
- [x] 新任务页面输入URL/标题/预设，选择“仅本地处理”或尚未解锁的发布模式；已有有效预设不每次再确认，待确认任务明确暂停原因。默认原封面，与视频布局图分离。
- [ ] GREEN：前端test/typecheck/build与`pipeline.spec.ts`端到端通过；用本机真实授权样例演示URL→源→预览→确认→完整成片，发布调用计数必须为0。
- [x] 提交`feat: add the configurable media pipeline editor`，限定Files；S4可独立交付本地处理，但完整项目仍需P系列门槛。

M10状态说明：实现与合成素材浏览器验收通过，真实YouTube URL→下载链路仍受M02登录验证阻断；上面的完整GREEN验收未达成，不标整项完成。
