import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, API_BASE } from './client';

// Tab ① Tải nhạc gốc — bài gốc tải từ nhaccuatui (file + link + lời).

export type SourceStatus =
  | 'queued' | 'running' | 'done' | 'lyrics_only' | 'login' | 'not_found' | 'failed' | 'stopped'
  | 'manual' | 'skipped_no_lyrics' | 'skipped_no_official' | 'excluded';

export interface CoverIssue {
  code: string;
  level: 'error' | 'warning';
  message: string;
}

// Kết quả kiểm tra lỗi 1 bản cover (so với chính bài gốc của nó).
export interface CoverQc {
  status: 'ok' | 'warning' | 'error' | 'pending';
  issues: CoverIssue[];
  manual_flag: boolean;   // người dùng bấm 🚩 Báo lỗi
  dismissed: boolean;     // người dùng bấm ✓ Nghe ổn
  compared_with?: 'original' | 'others';
}

export interface SourceCover {
  cover_song_id: number;
  track_id: number;
  filename: string;
  duration_seconds: number;
  qc: CoverQc | null;
  master?: CoverMaster;   // server cũ chưa có → undefined
  tempo_factor?: number;  // tempo đã bấm OK (1 = bản Suno gốc); server cũ → undefined
}

// Bản master (ffmpeg: EQ, nén, chuẩn độ to) của 1 bản cover.
export interface CoverMaster {
  state: 'queued' | 'running' | 'done' | 'failed' | 'none';
  file: string;           // tên file _master (rỗng = chưa có)
  preset: string;
  before: { lufs: number; tp: number } | null;
  after: { lufs: number; tp: number } | null;
  seconds: number | null;
  at: string;
  error: string;
}

export interface MasterSettings {
  enabled: boolean;       // tự master sau khi tải 2 bản từ Suno
  preset: string;
  output_format: 'wav' | 'mp3' | 'both';
  presets: { name: string; lufs: number; note: string }[];
  ffmpeg: { ok: boolean; version: string; error: string };
  running: boolean;
  config_file: string;
}

export interface SourceSong {
  id: number;
  position: number;
  query: string;
  title: string;
  artist: string;
  nct_url: string;
  source_provider?: 'nhaccuatui' | 'zingmp3' | 'ytmusic' | 'youtube' | '';
  has_file: boolean;
  official_source?: boolean; // false = file Remix/Cover cũ, bị chặn khỏi Suno
  file_name: string;
  // Bản WAV đã đổi tempo (giữ nguyên cao độ) dùng làm đầu vào bắt buộc cho Suno.
  tempo_factor: number;
  tempo_ready: boolean;
  tempo_file_name: string;
  quality: string;
  lyrics_text: string;
  lyric_line_count: number;
  // '' = lời từ nhaccuatui / bạn tự dán; còn lại = tool tự tìm trên mạng (Google…)
  lyrics_source: string;
  status: SourceStatus;
  error: string | null;
  suno_status: SunoStatus;
  suno_error: string | null;
  vocal_gender: '' | 'male' | 'female' | 'duet'; // '' = tool tự phân tích bài/ca sĩ gốc
  featured_instrument: string;             // '' = AI tự chọn; sentinel = tắt; còn lại = nhạc cụ chỉ định
  suno_created: boolean;   // đã Create trên Suno nhưng chưa tải về → thử lại chỉ tải
  suno_editor_bpm: number; // 0 = tự động theo thể loại; >0 = BPM tạo lại do người dùng chọn
  covers: SourceCover[];
}

// Bước Suno Cover (đợt B — tool tự làm trên Suno).
export type SunoStatus =
  | '' | 'redo'          // redo = bản cover cũ lỗi, chờ tạo lại
  | 'queued' | 'running' | 'done'
  | 'tested'             // 🧪 chạy thử xong (đã điền form, không bấm Create)
  | 'rejected'           // Suno từ chối bài gốc (bản quyền) → làm tay
  | 'failed' | 'stopped';

export interface SunoWorker {
  busy: boolean;
  paused?: boolean;
  mode: 'cover' | 'login' | null;
  current: number | null;
  message: string;
  pending: number[];
  login: { logged_in: boolean | null; checked_at: string | null };
  human: string;     // việc đang chờ BẠN làm trong cửa sổ Suno (captcha, xác nhận…)
  dry_run: boolean;
  // Suno hiện bảng cam «lyrics contain copyrighted material» sau Create: tool báo
  // popup, người dùng tự sửa lyrics + tự Create trên Suno, tool theo dõi bản mới.
  awaiting_lyrics?: { source_id: number; why: string; attempt: number; since: string | null } | null;
  // Bán thủ công: bạn tự chỉnh Tempo + Save as New Song và tự bấm Create.
  semi_manual?: boolean;
}

export interface NctWorker {
  busy: boolean;
  mode: 'download' | 'login' | null;
  current: number | null;
  message: string;
  pending: number[];
  login: { logged_in: boolean | null; checked_at: string | null };
}

const base = '/api/sources';

export const sourceAudioUrl = (id: number) => `${API_BASE}${base}/${id}/audio`;
export const tempoAudioUrl = (id: number) => `${API_BASE}${base}/${id}/tempo/audio`;
// `v` = tempo đang dùng → đổi tempo xong trình duyệt tải lại file, không phát bản cũ đã lưu tạm.
export const coverAudioUrl = (trackId: number, v = '') =>
  `${API_BASE}${base}/covers/${trackId}/audio${v ? `?v=${encodeURIComponent(v)}` : ''}`;
// `v` = thời điểm master xong → trình duyệt không phát lại bản master cũ đã lưu tạm.
export const masterAudioUrl = (trackId: number, v = '') =>
  `${API_BASE}${base}/covers/${trackId}/master/audio${v ? `?v=${encodeURIComponent(v)}` : ''}`;

export function useSources(projectId: number) {
  return useQuery<{
    items: SourceSong[]; worker: NctWorker; suno_worker?: SunoWorker; folder: string; qc_running?: boolean;
    master_running?: boolean;
  }>({
    queryKey: ['sources', projectId],
    queryFn: async () => (await api.get(`${base}/projects/${projectId}`)).data,
    // Đang tải / đang làm trên Suno / cửa sổ đăng nhập đang mở / đang kiểm tra bản cover
    // / đang master → hỏi lại mỗi 2 giây.
    refetchInterval: (q) => {
      const d = q.state.data;
      if (d?.worker?.busy || d?.suno_worker?.busy || d?.qc_running || d?.master_running) return 2000;
      return d?.items?.some((s) => s.covers.some((c) => c.qc?.status === 'pending')) ? 4000 : false;
    },
  });
}

function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ['sources', projectId] });
    qc.invalidateQueries({ queryKey: ['cover-songs', projectId] });
    qc.invalidateQueries({ queryKey: ['tracks', projectId] });
  };
}

export function useAddSources(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (text: string) =>
      (await api.post(`${base}/projects/${projectId}`, { text, start: true })).data as
        { added: number; ids: number[] },
    onSuccess: done,
  });
}

export function useUpdateSource(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: {
      id: number; query?: string; title?: string; artist?: string; lyrics_text?: string;
      vocal_gender?: '' | 'male' | 'female' | 'duet';
      featured_instrument?: string;
      // Bài Suno đã từ chối: xác nhận lyrics thay thế là tự sáng tác/được phép.
      authorized_lyrics?: boolean;
    }) => {
      const { id, ...body } = v;
      return (await api.put(`${base}/${id}`, body)).data as SourceSong;
    },
    onSuccess: done,
  });
}

export function useRetrySource(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (id: number) => (await api.post(`${base}/${id}/retry`)).data,
    onSuccess: done,
  });
}

export function useVerifyLyrics(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`${base}/${id}/verify-lyrics`)).data as { ok: boolean; message: string },
    onSuccess: done,
  });
}

/** Xuất WAV đã đổi tempo vào thư mục da_chinh_tempo của chính project. */
export function useExportTempo(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; tempoFactor: number }) =>
      (await api.post(`${base}/${v.id}/tempo`, {
        tempo_factor: v.tempoFactor,
      }, { timeout: 0 })).data as SourceSong,
    onSuccess: done,
  });
}

export function useStartAllSources(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/projects/${projectId}/start`)).data as { queued: number },
    onSuccess: done,
  });
}

export function useStopSources(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/stop`)).data,
    onSuccess: done,
  });
}

export function useDeleteSource(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (id: number) => (await api.delete(`${base}/${id}`)).data,
    onSuccess: done,
  });
}

export function useExcludeSource(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; restore?: boolean }) =>
      (await api.post(`${base}/${v.id}/${v.restore ? 'include' : 'exclude'}`)).data as {
        ok: boolean; removed_covers?: number; restored_covers?: number; recoverable?: boolean;
      },
    onSuccess: done,
  });
}

export function useImportCovers(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; files: File[] }) => {
      const form = new FormData();
      v.files.forEach((f) => form.append('files', f));
      return (
        await api.post(`${base}/${v.id}/covers`, form, {
          timeout: 0,
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      ).data as { added: { track_id: number; filename: string }[] };
    },
    onSuccess: done,
  });
}

// «Nhập bài cover tôi đã làm»: bản cover (1 hoặc nhiều file) + tên, ca sĩ, lời, (bài gốc).
export function useOwnCover(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { title: string; artist: string; lyrics: string; files: File[]; original: File | null }) => {
      const form = new FormData();
      form.append('title', v.title);
      form.append('artist', v.artist);
      form.append('lyrics', v.lyrics);
      v.files.forEach((f) => form.append('files', f));
      if (v.original) form.append('original', v.original);
      return (
        await api.post(`${base}/projects/${projectId}/own`, form, {
          timeout: 0,
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      ).data as { id: number; added: { track_id: number; filename: string }[] };
    },
    onSuccess: done,
  });
}

export function useRunQc(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/projects/${projectId}/qc`)).data,
    onSuccess: done,
  });
}

export function useMarkCover(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { trackId: number; flagged?: boolean; dismissed?: boolean }) => {
      const { trackId, ...body } = v;
      return (await api.post(`${base}/covers/${trackId}/mark`, body)).data;
    },
    onSuccess: done,
  });
}

// ---- master (ffmpeg) ----
export function useMasterCover(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { trackId: number; force?: boolean }) =>
      (await api.post(`${base}/covers/${v.trackId}/master`, { force: !!v.force })).data as
        { queued: number },
    onSuccess: done,
  });
}

// 🎚 OK ở thanh tempo: chuyển hẳn bản cover sang tempo mới (giữ cao độ).
export function useCoverTempo(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { trackId: number; factor: number }) =>
      (await api.post(`${base}/covers/${v.trackId}/tempo`, { factor: v.factor })).data as {
        changed: boolean; factor: number; duration_seconds: number;
        remastering?: boolean; lyrics_rescaled?: boolean;
      },
    onSuccess: done,
  });
}

export function useMasterProject(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (force: boolean) =>
      (await api.post(`${base}/projects/${projectId}/master`, { force })).data as { queued: number },
    onSuccess: done,
  });
}

export function useMasterSettings() {
  return useQuery<MasterSettings>({
    queryKey: ['master-settings'],
    queryFn: async () => (await api.get(`${base}/master/settings`)).data,
    retry: false,
  });
}

export function useSaveMasterSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: Partial<Pick<MasterSettings, 'enabled' | 'preset' | 'output_format'>>) =>
      (await api.put(`${base}/master/settings`, v)).data as MasterSettings,
    onSuccess: (data) => qc.setQueryData(['master-settings'], data),
  });
}

export function useRecheckFfmpeg() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/master/ffmpeg/check`)).data as MasterSettings,
    onSuccess: (data) => qc.setQueryData(['master-settings'], data),
  });
}

export function useRedoCover(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; trackIds: number[]; editorBpm?: number; tempoOnly?: boolean }) =>
      (await api.post(`${base}/${v.id}/redo`, {
        track_ids: v.trackIds, editor_bpm: v.editorBpm, tempo_only: !!v.tempoOnly,
      })).data as
        { removed: number; moved: number; folder: string; queued?: boolean; missing?: string | null },
    onSuccess: done,
  });
}

// ---- đợt B: tool tự làm cover trên Suno ----
export function useSunoStart(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { dryRun?: boolean; fresh?: boolean }) =>
      (await api.post(`${base}/projects/${projectId}/suno/start`, {
        dry_run: !!v.dryRun, fresh: !!v.fresh,
      })).data as
        { queued: number; skipped: number },
    onSuccess: done,
  });
}

export function useSunoOne(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; dryRun?: boolean; fresh?: boolean; editorBpm?: number; tempoOnly?: boolean; songLinks?: string[]; authorizedLyrics?: boolean }) =>
      (await api.post(`${base}/${v.id}/suno`, {
        dry_run: !!v.dryRun, fresh: !!v.fresh, editor_bpm: v.editorBpm,
        tempo_only: !!v.tempoOnly, song_links: v.songLinks,
        authorized_lyrics: !!v.authorizedLyrics,
      })).data as
        { queued: number },
    onSuccess: done,
  });
}

export function useSunoManualWatch(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`${base}/${id}/suno/manual-watch`, {
        authorized_lyrics: true,
      })).data as { queued: number; message: string },
    onSuccess: done,
  });
}

/** Bảng cam bản quyền: bỏ qua bài đang chờ bạn tự Create trên Suno. */
export function useSunoSkipLyrics(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post(`${base}/${id}/suno/copyright-skip`)).data as { ok: boolean },
    onSuccess: done,
  });
}

export function useSunoStop(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/suno/stop`)).data,
    onSuccess: done,
  });
}

export function useSunoPause(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/suno/pause`)).data,
    onSuccess: done,
  });
}

export function useSunoResume(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () => (await api.post(`${base}/suno/resume`)).data,
    onSuccess: done,
  });
}

export function useSunoWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (semiManual: boolean) =>
      (await api.put(`${base}/suno/workflow`, { semi_manual: semiManual })).data as
        { semi_manual: boolean },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sources'] });
      qc.invalidateQueries({ queryKey: ['suno-login'] });
    },
  });
}

// Trạng thái đăng nhập / cửa sổ Suno (không theo project — dùng ở Cài đặt).
export function useSunoLogin(enabled = true) {
  return useQuery<SunoWorker>({
    queryKey: ['suno-login'],
    queryFn: async () => (await api.get(`${base}/suno/login`)).data,
    enabled,
    refetchInterval: (q) => (q.state.data?.busy ? 2000 : false),
  });
}

export function useSunoLoginWindow() {
  const qc = useQueryClient();
  const done = () => {
    qc.invalidateQueries({ queryKey: ['suno-login'] });
    qc.invalidateQueries({ queryKey: ['sources'] });
  };
  const open = useMutation({
    mutationFn: async () => (await api.post(`${base}/suno/login/open`)).data as SunoWorker,
    onSuccess: done,
  });
  const close = useMutation({
    mutationFn: async () => (await api.post(`${base}/suno/login/close`)).data as SunoWorker,
    onSuccess: done,
  });
  return { open, close };
}

export function useMakeLyrics(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async () =>
      (await api.post(`${base}/projects/${projectId}/make-lyrics`)).data as
        { songs: number; filled: number; queued: number },
    onSuccess: done,
  });
}

// Trạng thái cửa sổ đăng nhập / hàng đợi tải (không theo project — dùng ở Cài đặt).
export function useNctLogin(enabled = true) {
  return useQuery<NctWorker>({
    queryKey: ['nct-login'],
    queryFn: async () => (await api.get(`${base}/login`)).data,
    enabled,
    refetchInterval: (q) => (q.state.data?.busy ? 2000 : false),
  });
}

export function useNctLoginWindow() {
  const qc = useQueryClient();
  const done = () => {
    qc.invalidateQueries({ queryKey: ['nct-login'] });
    qc.invalidateQueries({ queryKey: ['sources'] });
  };
  const open = useMutation({
    mutationFn: async () => (await api.post(`${base}/login/open`)).data as NctWorker,
    onSuccess: done,
  });
  const close = useMutation({
    mutationFn: async () => (await api.post(`${base}/login/close`)).data as NctWorker,
    onSuccess: done,
  });
  return { open, close };
}

// Giao diện mới nhưng server (cửa sổ start.bat) vẫn chạy bản cũ chưa có API này.
export const OLD_SERVER_MSG =
  '⚠️ Tool đang chạy bản cũ — hãy tắt cửa sổ đen «start.bat» rồi mở lại start.bat.';

export function errText(err: unknown, fallback = 'Có lỗi xảy ra'): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
  const d = e?.response?.data?.detail;
  if (e?.response?.status === 405 || d === 'API không tồn tại') return OLD_SERVER_MSG;
  return typeof d === 'string' ? d : e?.message ?? fallback;
}

export type SunoWavItem = {
  ok: boolean;
  input?: string;
  error?: string;
  song_id?: string;
  title?: string;
  path?: string;
  filename?: string;
  mode?: 'official_wav_exact' | 'compatible_pcm_from_public_source' | 'authorized_continuous_decrypt_pcm16';
  codec?: string;
  sample_rate?: number;
  channels?: number;
  duration_seconds?: number;
  lyrics_path?: string;
  image_path?: string;
  zip_path?: string;
  zip_filename?: string;
};

export type SunoWavResult = {
  folder: string;
  downloaded: number;
  items: SunoWavItem[];
};

/** Tải trực tiếp từ Suno/CDN Suno; URL không được gửi qua website bên thứ ba. */
export function useSunoWavDownloader(projectId: number) {
  return useMutation<SunoWavResult, unknown, { links: string; allowCompatible: boolean }>({
    mutationFn: async ({ links, allowCompatible }) =>
      (await api.post(`/api/projects/${projectId}/suno-wav/download`, {
        links,
        allow_compatible: allowCompatible,
      })).data,
  });
}
