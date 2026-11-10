export interface Project {
  id: number;
  name: string;
  description: string;
  created_at: string;
  track_count?: number;
  mix_count?: number;
  video_idea?: string;
  auto_video?: boolean;
  video_style?: string;
  instrument?: string;
  music_style?: string;
  music_line?: string;     // dòng nhạc (VN / US-UK / Chinese…) — tự chọn kênh
  auto_upload?: boolean;
  music_source?: string;   // "local" | "suno"
  suno_idea?: string;      // ý tưởng/nhạc cụ cho STEP 0 (Suno)
  batch_id?: string | null; // nhóm project cùng 1 lô sản xuất hàng loạt
  kind?: ProjectKind;       // "mixset" (nhiều bài 1 video) | "single" (Track lẻ)
  mix_duration_minutes?: number;  // tổng thời lượng mix = độ dài video
  mix_crossfade_seconds?: number;
  channel_id?: number | null;          // kênh YouTube người dùng chọn (null = tự tra)
  effective_channel_id?: number | null; // kênh sẽ dùng (đã chọn, hoặc tự khớp)
  effective_channel_name?: string;
  intro_mode?: IntroMode;   // intro 00:00: "thumbnail" (mặc định) | "font"
  cast_mode?: CastMode;     // "character_pet" (mặc định) | "scenery" (chỉ cảnh vật)
  footage_scene_mode?: FootageSceneMode;
  creative_instrument?: boolean;
}

// Nhóm project — thư mục media/Mix Set hoặc media/Track lẻ.
export type ProjectKind = "mixset" | "single";

// Intro: ảnh thumbnail động mở đầu từ 00:00 (mặc định) hoặc chữ font có sẵn.
export type IntroMode = "thumbnail" | "font";
// Video: nhân vật + linh thú/thú nuôi (mặc định) hoặc chỉ cảnh vật.
export type CastMode = "character_pet" | "scenery";
export type FootageSceneMode = "auto" | "character_screen" | "screen_only";

// ---------- Sản xuất hàng loạt (batch) ----------
export interface BatchCreate {
  count: number;
  instrument: string;       // nhạc cụ — giữ nguyên cả lô (bắt buộc)
  music_style: string;      // thể loại nhạc — giữ nguyên cả lô (bắt buộc)
  music_line?: string;      // dòng nhạc cả lô (VN / US-UK…) — để tự chọn kênh
  channel_name?: string;
  channel_id?: number | null; // kênh đăng nháp — kênh nào cũng nhận nhạc cụ nào
  video_style?: string;     // "channel" (theo phong cách kênh) hoặc key ảnh (2d/3d/…/real)
  theme?: string;           // chủ đề tuỳ chọn
  target_tracks?: number | null;
  max_create_actions?: number | null;
  mix_duration_minutes?: number;  // tổng thời lượng mix/video mỗi project
  intro_mode?: IntroMode;
  cast_mode?: CastMode;
  footage_scene_mode?: FootageSceneMode;
}

// Một file danh sách → một project Mix Set cover chạy end-to-end,
// hoặc (kind "single") mỗi bài một project Track lẻ — ảnh bìa + video riêng.
export interface MixSetCreate {
  kind?: ProjectKind;
  name: string;              // bỏ qua khi kind "single" (tên = từng dòng)
  song_file: File;
  music_style: string;
  music_line?: string;
  channel_id?: number | null;
  video_style?: string;
  theme?: string;
  mix_duration_minutes?: number;
  creative_instrument?: boolean;  // AI tự chọn thêm nhạc cụ cho từng bài (mặc định bật)
  intro_mode?: IntroMode;
  cast_mode?: CastMode;
  footage_scene_mode?: FootageSceneMode;
}

export interface InstrumentOption {
  key: string;
  label: string;     // nhãn tiếng Anh (lưu vào project)
  vi: string;        // nhãn hiển thị tiếng Việt
  portable: boolean;
}

export interface GenreOption {
  key: string;
  label: string;
  bpm: [number, number];
  beatless: boolean;
}

export interface BatchStyleOption {
  key: string;
  icon: string;
  label: string;
  desc: string;
}

export interface BatchProjectItem {
  project_id: number;
  name: string;
  suno_phase?: string | null;
  suno_message?: string | null;
}

export interface MissingLyricsItem {
  source_id: number;
  project_id: number;
  query: string;
  title: string;
  artist: string;
}

export type BatchStatus = 'running' | 'completed' | 'cancelled' | 'blocked' | 'failed';

export interface BatchRun {
  batch_key: string;
  total: number;
  completed: number;
  failed: number;
  status: BatchStatus;
  current_index: number;
  current_project_id?: number | null;
  message: string;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  projects: BatchProjectItem[];
  missing_lyrics: MissingLyricsItem[];
}

export interface AppSettings {
  api_base_url: string;
  api_model: string;
  has_api_key: boolean;
}

export interface AppSettingsUpdate {
  api_key?: string;
  clear_api_key?: boolean;
  api_base_url?: string;
  api_model?: string;
}

export interface ApiTestRequest {
  api_key?: string;
  api_base_url?: string;
  api_model?: string;
}

export interface ApiTestResult {
  ok: boolean;
  model: string;
  base_url: string;
  latency_ms?: number | null;
  reply?: string | null;
  error_type?: string | null;
  error?: string | null;
  retryable: boolean;
}

// ── Thumbnail AI (ảnh + chữ qua API, mặc định ShopAIKey) ──────────
export interface ThumbAIModelChoice {
  key: string;
  label: string;
  group: string;
  price_usd?: number | null;
  protocol: string;
  note: string;
}

export interface ThumbAISettings {
  base_url: string;
  model: string;
  enabled: boolean;
  verify: boolean;
  has_api_key: boolean;
  protocol: string;
  models: ThumbAIModelChoice[];
  engine: ThumbEngine;
  engines: { key: ThumbEngine; label: string; note: string }[];
  chatgpt_logged_in?: boolean | null;
  chatgpt_checked_at?: string | null;
}

export type ThumbEngine = 'chatgpt' | 'api' | 'pil';

export interface ChatGPTWindowStatus {
  open: boolean;
  status?: string | null;
  message: string;
  logged_in?: boolean | null;
  checked_at?: string | null;
}

export interface ThumbAISettingsUpdate {
  api_key?: string;
  clear_api_key?: boolean;
  base_url?: string;
  model?: string;
  enabled?: boolean;
  verify?: boolean;
  engine?: ThumbEngine;
}

export interface ThumbAITestRequest {
  api_key?: string;
  base_url?: string;
  model?: string;
}

export interface ThumbAITestResult {
  ok: boolean;
  base_url: string;
  model: string;
  model_available?: boolean | null;
  image_models: string[];
  model_count: number;
  latency_ms?: number | null;
  error?: string | null;
}

export interface ThumbAIStatus {
  engine: 'chatgpt' | 'ai' | 'pil';
  note: string;
  default_engine?: ThumbEngine;
  chatgpt_logged_in?: boolean | null;
  chatgpt_checked_at?: string | null;
  ai_ready: boolean;
  ai_active: boolean;
  model: string;
  model_label: string;
  price_usd?: number | null;
  last_ai?: {
    model: string; created_at: string; attempts: number; seconds: number;
    engine?: string; label?: string;
    verified?: boolean | null; read_title?: string | null;
  } | null;
}

export interface Track {
  id: number;
  project_id: number;
  filename: string;
  filepath: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  format: string;
  is_lossy: boolean;
  subtype?: string;
  added_at: string;
}

export type MixStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';

export interface Mix {
  id: number;
  project_id: number;
  title: string;
  status: MixStatus;
  created_at: string;
  completed_at?: string;
  duration_minutes: number;
  crossfade_seconds: number;
  sample_rate: number;
  bit_depth: number;
  output_dir?: string;
  total_duration_seconds?: number;
  track_count?: number;
  error_message?: string;
  progress_percent?: number;
  progress_step?: number;
  progress_step_name?: string;
}

export interface ProjectDetail extends Project {
  tracks: Track[];
  mixes: Mix[];
}

export interface ScanResult {
  added: number;
  skipped_duplicates: number;
  skipped_errors: number;
  error_files: string[];
  total_tracks: number;
  warning_lossy: boolean;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  instrument?: string;     // nhạc cụ chủ đạo — khoá nhân vật/cảnh/Styles Suno
  music_style?: string;    // thể loại nhạc (lofi, zen, sleep…)
  music_line?: string;     // dòng nhạc (VN / US-UK / Chinese…) — tự chọn kênh
  video_style?: string;    // "channel" = theo phong cách riêng của kênh; key = cố định
  channel_id?: number | null; // kênh YouTube đăng nháp (null = tự chọn theo nhạc cụ)
  intro_mode?: IntroMode;
  cast_mode?: CastMode;
  footage_scene_mode?: FootageSceneMode;
  creative_instrument?: boolean;
  kind?: ProjectKind;      // đổi nhóm → thư mục media được chuyển theo
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  video_idea?: string;
  auto_video?: boolean;
  video_style?: string;
  instrument?: string;
  music_style?: string;
  music_line?: string;
  auto_upload?: boolean;
  music_source?: string;
  suno_idea?: string;
  mix_duration_minutes?: number;
  mix_crossfade_seconds?: number;
  channel_id?: number | null;
  intro_mode?: IntroMode;
  cast_mode?: CastMode;
  footage_scene_mode?: FootageSceneMode;
  creative_instrument?: boolean;
}

export interface MixCreate {
  duration_minutes: number;
  crossfade_seconds: number;
  sample_rate: number;
  bit_depth: number;
  title?: string;
  /** Đo + cân bằng độ to giữa các bài (mặc định bật ở backend). */
  loudness_balance?: boolean;
}

export interface LoudnessStats {
  integrated: number | null;   // LUFS trung bình cả bài
  loud: number | null;         // LUFS «đoạn to» (phân vị 90 short-term)
  lra: number | null;          // dải động (LU)
  peak_db: number | null;
  duration: number;
  error?: string;
}

export interface LoudnessSong {
  track_id: number;
  song_id: number | null;
  title: string;
  artist: string;
  audio_file: string;
  mastered: boolean;
  before: LoudnessStats;
  auto_gain_db: number;
  manual_gain_db: number;
  gain_db: number;
  deviation_lu: number | null;
  flag: boolean;
  after_integrated: number | null;
  after_loud: number | null;
  limit_db: number;
  heavy_limit: boolean;
}

export interface LoudnessReport {
  project_id: number;
  target_lufs: number;
  ceiling_db: number;
  flag_lu: number;
  spread_before: number | null;
  spread_after: number | null;
  songs: LoudnessSong[];
}

export interface MixProgressEvent {
  step: number;
  step_name: string;
  percent: number;
  message?: string;
}

export type StemStatus =
  | "not_started" | "pending" | "running"
  | "completed"   | "failed";

export interface StemVolumes {
  other:  number;   // Âm nhạc — 0.0 → 2.0
  bass:   number;   // Âm trầm
  drums:  number;   // Trống
  vocals: number;   // Giọng hát
}

export interface StemDenoise {
  other:  number;   // Độ mạnh khử noise thủ công — 0.0 → 1.0
  bass:   number;
  drums:  number;
  vocals: number;
}

export interface TrackStemInfo {
  track_stem_id?:     number;
  status:             StemStatus;
  error_message?:     string;
  volumes:            StemVolumes;
  denoise?:           StemDenoise;
  waveform_data?:     Record<string, number[]> | null;
  lufs_gain_applied?: number;
}

export interface ProjectStemStatus {
  total:            number;
  counts: {
    pending:   number;
    running:   number;
    completed: number;
    failed:    number;
  };
  is_processing:    boolean;
  pending_in_queue: number;
}

// ── Video ──────────────────────────────────────────────────────
export interface VideoParamsInfo {
  image_count:    number;   // 3: bìa + bảng nhân vật chính + bảng linh thú
  // Nhãn vai trò từng ảnh, khoá là chỉ số ảnh dạng chuỗi ("0" | "1" | "2").
  image_roles?:   Record<string, string>;
  clip_seconds:   number;
  rest_clips:     number;
  total_clips:    number;
  t_window:       number;
  model:          string;
  clip_engine?:   string;   // "flow" (mặc định) | "gemini" | "muse"
  clip_engine_label?: string;
  image_engine?:  string;   // "chatgpt" (mặc định, dự phòng Muse) | "muse" | "gemini"
  image_engine_label?: string;
  image_fallback_label?: string | null;
  clip_engines?:  { key: string; label: string }[];
  aspect_ratio:   string;
  flow_mode:      string;
  blend_seconds:  number;
  slow_speed:     number;
  cast_mode?:     CastMode;   // theo bộ ảnh đã chuẩn bị (prompts.json), chưa có → theo project
  intro_mode?:    IntroMode;
  // true = 1 ảnh bìa → clip 00 intro (ảnh bìa sống dậy) + clip 01 loop đến hết nhạc.
  single_image?:  boolean;
}

export type VideoJobStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';

export interface VideoJobInfo {
  kind:    string;   // images | clips | assemble | full
  status:  VideoJobStatus;
  percent: number;
  message: string;
  error?:  string | null;
}

export interface VideoStatus {
  images:         string[];
  image_count:    number;
  image_indices?: number[];
  clips:          string[];
  clip_count:     number;
  clip_indices?:  number[];
  final_exists:   boolean;
  final_path?:    string | null;
  thumbnail_path?: string | null;
  thumbnail_mtime?: number | null;
  thumbnail_ai?:  ThumbAIStatus | null;
  audio_path?:    string | null;
  audio_duration?: number | null;
  footage_exists?: boolean;
  footage_path?: string | null;
  footage_name?: string | null;
  footage_duration?: number | null;
  footage_list?: { name: string; duration: number | null }[];
  footage_scene_mode?: FootageSceneMode;
  job?:           VideoJobInfo | null;
  params:         VideoParamsInfo;
  // Trạng thái phiên "Tạo video thủ công" (null = chưa chạy lần nào).
  manual?: {
    active: boolean; phase: string; current: number | null; prompt: string;
    ingredients: number[]; blocked: string | null; total: number;
    done: number[]; todo: number[]; message: string;
  } | null;
}

// ── Suno (STEP 0 — tạo nhạc nền) ───────────────────────────────
export interface SunoPreset {
  key:        string;
  label:      string;
  styles:     string;      // gửi sang Suno (English)
  exclusions: string;
}

export interface SunoDefaults {
  source:                          string;   // "suno" | "local"
  preset:                          string;
  target_tracks:                   number;
  preferred_model:                 string;
  allow_model_fallback:            boolean;
  instrumental:                    boolean;
  max_mode:                        boolean;
  preferred_duration_seconds_min:  number;
  preferred_duration_seconds_max:  number;
  minimum_duration_seconds:        number;
  max_create_actions:              number;
  max_generation_credits:          number;
  max_new_song_downloads:          number;
  auto_continue_workflow:          boolean;
}

export interface SunoConfigResponse {
  defaults: SunoDefaults;
  presets:  SunoPreset[];
}

// Phase khớp backend.video.suno_service (PREFLIGHT..COMPLETED + blocking states).
export type SunoPhase =
  | 'PREFLIGHT' | 'GENERATING' | 'DOWNLOADING' | 'VALIDATING'
  | 'READY_FOR_IMPORT' | 'IMPORTED' | 'DOWNSTREAM_RUNNING' | 'COMPLETED'
  | 'WAITING_FOR_LOGIN' | 'WAITING_FOR_HUMAN' | 'SUBMISSION_UNCERTAIN'
  | 'INSUFFICIENT_GENERATION_CREDITS' | 'INSUFFICIENT_DOWNLOAD_ALLOWANCE'
  | 'UI_CHANGED' | 'FAILED' | 'PAUSED' | 'CANCELLED';

export interface SunoBatchStatus {
  batch_id:             number;
  project_id:           number;
  phase:                SunoPhase;
  message?:             string | null;
  dry_run:              boolean;
  credits_remaining?:   number | null;
  preset?:              string | null;
  model?:               string | null;
  target_tracks:        number;
  generated_candidates: number;
  selected_tracks:      number;
  downloaded_tracks:    number;
  validated_tracks:     number;
  imported_tracks:      number;
  create_actions_used:  number;
  staging_dir?:         string | null;
}

export interface SunoJobInfo {
  kind:    string;   // "suno"
  status:  VideoJobStatus;
  percent: number;
  message: string;
  error?:  string | null;
}

// Kết quả tự kiểm tra selector khi mở UI Suno (open-browser + self-check).
export interface SunoSelectorGroup {
  name:    string;
  status:  'ok' | 'healed' | 'missing' | 'skipped' | 'conditional';
  matched: string | null;
  kind:    'page' | 'conditional' | 'interactive';
}

export interface SunoSelectorCheck {
  groups:           SunoSelectorGroup[];
  discovered:       Record<string, string[]>;
  summary: {
    page_ok:    number;
    page_total: number;
    healed:     string[];
    missing:    string[];
  };
  checked_at:        string;
  logged_in?:        boolean;
  saved_overrides?:  string[];
  save_error?:       string;
}

export interface SunoStatusResponse {
  batch:           SunoBatchStatus | null;
  job:             SunoJobInfo | null;
  selector_check?: SunoSelectorCheck | null;
}

// Body override khi bấm Start/Dry-run — mọi trường tuỳ chọn (bỏ trống = mặc định).
export interface SunoStartBody {
  preset?:                         string;
  // Styles/Exclusions (AI viết từ ý tưởng hoặc người dùng tự sửa). Bỏ trống = preset.
  styles?:                         string;
  exclusions?:                     string;
  suno_idea?:                      string;
  target_tracks?:                  number;
  preferred_model?:                string;
  allow_model_fallback?:           boolean;
  max_mode?:                       boolean;
  preferred_duration_seconds_min?: number;
  preferred_duration_seconds_max?: number;
  minimum_duration_seconds?:       number;
  max_create_actions?:             number;
  max_generation_credits?:         number;
  max_new_song_downloads?:         number;
  auto_continue_workflow?:         boolean;
}

// Kết quả AI viết Styles từ ý tưởng (POST /suno/generate-styles).
export interface SunoGenerateStylesBody {
  idea:    string;
  preset?: string;
  save?:   boolean;
}

export interface SunoChannelMatch {
  matched:      boolean;
  instrument:   string;
  music_style:  string;
  channel_name: string;
  confidence:   string;
  warning:      string | null;
}

export interface SunoGenerateStylesResponse {
  source:     'ai' | 'fallback';
  styles:     string;
  exclusions: string;
  note:       string | null;
  channel:    SunoChannelMatch;
}

// ── Rà soát track lẻ + tạo lại Suno + vá mix ──
export interface QcIssue {
  code: string;
  level: 'error' | 'warning';
  message: string;
}

export type QcStatus = 'ok' | 'warning' | 'error' | 'unknown';

export interface QcTrack {
  track_id: number;
  filename: string;
  duration: number;
  status: QcStatus;
  issues: QcIssue[];
  manual_flag: boolean;
  is_suno: boolean;
  request_id: string | null;
  free_spares: number;
}

export interface QcSegment {
  index: number;
  name: string;
  start: number;
  end: number;
  source_file: string;
  track_id: number | null;
  track_filename: string | null;
  status: QcStatus | 'replaced' | 'missing';
  issues: string[];
}

export interface QcTimeline {
  mix_id: number;
  mix_title: string;
  total_seconds: number;
  crossfade_seconds: number;
  segments: QcSegment[];
  outdated_segments: number;
}

export interface QcJob {
  kind: 'suno-fix' | 'remux';
  status: string;
  message: string;
  percent: number;
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface QcReplaced {
  old_track_id: number;
  new_track_id: number;
  old_filename: string;
  new_filename: string;
  source: 'spare' | 'create';
  creates: number;
  old_duration: number;
  new_duration: number;
  at: string;
}

export interface QcResponse {
  analyzed_at: string | null;
  tracks: QcTrack[];
  replaced: QcReplaced[];
  timeline: QcTimeline | null;
  job: QcJob | null;
  video_busy: boolean;
  mix_busy: boolean;
  credits_per_create: number;
  auto_regen: boolean;
  auto_fix: QcAutoFix | null;
  auto_active: boolean;
  auto_limits: { max_tracks: number; max_creates_per_track: number };
}

/** Lượt TỰ tạo lại Suno sau khi rà soát (1 lượt / bản mix). */
export interface QcAutoFix {
  mix_id: number;
  status: 'off' | 'skipped' | 'running' | 'patching' | 'done' | 'failed' | 'cancelled';
  message: string;
  track_ids?: number[];
  replaced?: number;
  failed?: number;
  at: string;
}

// ---------- Xoá nhiều project + dọn dữ liệu thừa ----------
export interface BulkDeleteItem {
  id: number;
  name: string;
  ok: boolean;
  busy?: boolean;
  error?: string;
  warnings?: string[];
  cleanup_pending?: string[];
  freed_bytes?: number;
  trashed?: boolean;
}

export interface BulkDeleteResult {
  results: BulkDeleteItem[];
  deleted: number;
  failed: number;
  freed_bytes: number;
  /** true = đã chuyển vào Thùng rác (mặc định), false = xoá vĩnh viễn. */
  trashed?: boolean;
  retention_days?: number;
}

export interface ProjectDiskUsage {
  projects: Record<string, number>;
  total_bytes: number;
}

export interface JunkItem {
  key: string;
  label: string;
  bytes: number;
  project_id: number | null;
  detail: string;
}

export interface JunkCategory {
  key: string;
  title: string;
  description: string;
  bytes: number;
  items: JunkItem[];
}

export interface JunkScan {
  categories: JunkCategory[];
  total_bytes: number;
  busy_project_ids: number[];
  untouched: { label: string; bytes: number }[];
}

export interface JunkCleanResult {
  freed_bytes: number;
  done: string[];
  errors: string[];
}

// ---------- Tình trạng project (Nhạc → Mix → Ảnh → Clip → Video → Nháp YT → Short) ----------
export type ProgressStepState =
  | 'pending' | 'done' | 'running' | 'paused' | 'queued' | 'waiting'
  | 'blocked' | 'error' | 'stopped' | 'skipped';

export interface ProgressStep {
  key: string;
  label: string;
  state: ProgressStepState;
  detail: string;
  percent?: number;
}

export interface ProjectProgress {
  project_id: number;
  /** new | running | paused | queued | stopped | blocked | error | done | unknown */
  state: string;
  stage: string;
  stage_label: string;
  summary: string;
  percent: number | null;
  done_steps: number;
  total_steps: number;
  steps: ProgressStep[];
}

export interface ProjectsProgressResponse {
  steps: { key: string; label: string }[];
  projects: ProjectProgress[];
}

// ---------- Thùng rác (giữ 7 ngày rồi mới xoá vĩnh viễn) ----------
export interface TrashItem {
  id: number;
  name: string;
  description?: string | null;
  deleted_at: string;
  purge_at: string | null;
  days_left: number;
  size_bytes: number;
  trash_dir: string | null;
  in_place: string[];
}

export interface TrashList {
  retention_days: number;
  items: TrashItem[];
  total_bytes: number;
}
