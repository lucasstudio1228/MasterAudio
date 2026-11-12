import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { newApi } from './storage';
import type {
  BatchCreate,
  BatchRun,
  BatchStyleOption,
  BulkDeleteResult,
  GenreOption,
  InstrumentOption,
  MixSetCreate,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectUpdate,
  ProjectsProgressResponse,
  TrashList,
} from '../types';

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: async () => (await api.get<Project[]>('/api/projects')).data,
  });
}

export function useProject(id: number | undefined) {
  return useQuery({
    queryKey: ['project', id],
    enabled: id !== undefined,
    queryFn: async () =>
      (await api.get<ProjectDetail>(`/api/projects/${id}`)).data,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ProjectCreate) =>
      (await api.post<Project>('/api/projects', data)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useUpdateProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ProjectUpdate) =>
      (await api.patch<Project>(`/api/projects/${id}`, data)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] });
      qc.invalidateQueries({ queryKey: ['project', id] });
      // đổi thể loại / dòng nhạc → cài đặt video cover «theo thể loại» đổi theo
      qc.invalidateQueries({ queryKey: ['cover-settings', id] });
    },
  });
}

// ---------- Sản xuất hàng loạt ----------
/** Phong cách ảnh cho form tạo lô (chưa có project → route riêng, cùng nguồn config.STYLES). */
export function useInstrumentCatalog() {
  return useQuery<{ instruments: InstrumentOption[]; genres: GenreOption[] }>({
    queryKey: ['instrument-catalog'],
    queryFn: async () => (await api.get('/api/projects/instruments')).data,
    staleTime: Infinity,
  });
}

export function useBatchStyles() {
  return useQuery<{ default: string; styles: BatchStyleOption[] }>({
    queryKey: ['batch-styles'],
    queryFn: async () =>
      (await api.get('/api/projects/batch/styles')).data,
    staleTime: Infinity,
  });
}

export function useCreateBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: BatchCreate) =>
      // AI nghĩ N ý tưởng trong request này (thường 30–120s) → không dùng timeout
      // 30s mặc định, nếu không UI báo lỗi trong khi server vẫn tạo lô.
      (await api.post<BatchRun>('/api/projects/batch', data, { timeout: 600_000 })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useCreateMixSet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: MixSetCreate) => {
      const single = data.kind === 'single';
      const body = new FormData();
      body.append('file', data.song_file);
      if (!single) body.append('name', data.name);
      body.append('music_style', data.music_style);
      body.append('music_line', data.music_line ?? '');
      if (data.channel_id != null) body.append('channel_id', String(data.channel_id));
      body.append('video_style', data.video_style ?? 'channel');
      body.append('theme', data.theme ?? '');
      if (!single)
        body.append('mix_duration_minutes', String(data.mix_duration_minutes ?? 120));
      body.append('creative_instrument', String(data.creative_instrument ?? true));
      body.append('intro_mode', data.intro_mode ?? 'thumbnail');
      body.append('cast_mode', data.cast_mode ?? 'character_pet');
      body.append('footage_scene_mode', data.footage_scene_mode ?? 'auto');
      body.append('audio_only', String(data.audio_only ?? false));
      // Track lẻ: mỗi dòng một project riêng (ảnh bìa + video khác nhau).
      return (await api.post<BatchRun>(single ? '/api/projects/singles' : '/api/projects/mixset', body, {
        timeout: 600_000,
      })).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

/** Thư mục audio đã xong (để nghe) và file video final (nếu đã render). */
export function useProjectFolders(projectId: number) {
  return useQuery<{ audio: string | null; video: string | null }>({
    queryKey: ['project-folders', projectId],
    queryFn: async () => (await api.get(`/api/projects/${projectId}/folders`)).data,
    refetchInterval: 15000,
  });
}

/** Mở thư mục trên máy tính (File Explorer) — backend chạy ngay trên máy này. */
export function useOpenProjectFolder(projectId: number) {
  return useMutation({
    mutationFn: async (what: 'audio' | 'video') => (await api.post(
      `/api/projects/${projectId}/open-folder`, null, { params: { what } },
    )).data as { ok: boolean; path: string },
  });
}

/** Lô đang chạy trên server (null nếu không có) — poll 15s. */
export function useActiveBatch() {
  return useQuery<{ batch_key: string | null }>({
    queryKey: ['batch-active'],
    queryFn: async () => (await api.get('/api/projects/batch/active')).data,
    refetchInterval: 15000,
  });
}

/** Poll tiến độ lô mỗi 5s khi còn đang chạy (khớp nhịp poll của orchestrator). */
export function useBatchStatus(batchKey: string | null | undefined) {
  return useQuery({
    queryKey: ['batch', batchKey],
    enabled: Boolean(batchKey),
    queryFn: async () =>
      (await api.get<BatchRun>(`/api/projects/batch/${batchKey}`)).data,
    refetchInterval: (query) =>
      query.state.data && query.state.data.status !== 'running' ? false : 5000,
  });
}

export function useCancelBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (batchKey: string) =>
      (await api.post<BatchRun>(`/api/projects/batch/${batchKey}/cancel`)).data,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['batch', data.batch_key] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useResumeBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (batchKey: string) =>
      (await api.post<BatchRun>(`/api/projects/batch/${batchKey}/resume`)).data,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['batch', data.batch_key] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useSkipMissingLyrics() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (batchKey: string) =>
      (await api.post<BatchRun>(
        `/api/projects/batch/${batchKey}/skip-missing-lyrics`,
      )).data,
    onSuccess: (data) => {
      qc.setQueryData(['batch', data.batch_key], data);
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

/** Tình trạng mọi project (đã xong / đang ở giai đoạn nào). Poll 4s khi có project
 *  đang chạy, 15s khi tất cả đứng yên. */
export function useProjectsProgress() {
  return useQuery<ProjectsProgressResponse>({
    queryKey: ['projects-progress'],
    queryFn: () =>
      newApi<ProjectsProgressResponse>(() =>
        api.get('/api/projects/progress', { timeout: 30_000 })),
    retry: false,
    refetchInterval: (query) => {
      const items = query.state.data?.projects ?? [];
      return items.some((p) => ['running', 'paused', 'queued'].includes(p.state))
        ? 4000
        : 15000;
    },
  });
}

function invalidateProjectLists(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['projects'] });
  qc.invalidateQueries({ queryKey: ['projects-progress'] });
  qc.invalidateQueries({ queryKey: ['project-trash'] });
  qc.invalidateQueries({ queryKey: ['storage-usage'] });
}

/** Mặc định: chuyển vào Thùng rác (giữ 7 ngày). */
export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    // Dời thư mục lớn + thử lại khi file bị khoá → có thể quá 30s mặc định.
    mutationFn: async (id: number) =>
      (await api.delete(`/api/projects/${id}`, { timeout: 180_000 })).data,
    onSuccess: () => invalidateProjectLists(qc),
  });
}

// ---------- Thùng rác ----------
export function useTrash(enabled = true) {
  return useQuery<TrashList>({
    queryKey: ['project-trash'],
    enabled,
    queryFn: () =>
      newApi<TrashList>(() => api.get('/api/projects/trash', { timeout: 120_000 })),
    retry: false,
    refetchInterval: 60_000,
  });
}

export function useRestoreProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.post<Project>(`/api/projects/trash/${id}/restore`, null,
                               { timeout: 180_000 })).data,
    onSettled: () => invalidateProjectLists(qc),
  });
}

/** Xoá vĩnh viễn NGAY 1 project trong Thùng rác. */
export function usePurgeProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.delete(`/api/projects/trash/${id}`, { timeout: 600_000 })).data as
        { freed_bytes?: number; warnings?: string[] },
    onSettled: () => invalidateProjectLists(qc),
  });
}

export function useEmptyTrash() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      newApi<BulkDeleteResult>(() =>
        api.post('/api/projects/trash/empty', null, { timeout: 900_000 })),
    onSettled: () => invalidateProjectLists(qc),
  });
}

/** Chuyển nhiều project vào Thùng rác — server xử lý lần lượt từng cái. */
export function useBulkDeleteProjects() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) =>
      newApi<BulkDeleteResult>(() =>
        api.post('/api/projects/bulk-delete', { ids }, { timeout: 900_000 })),
    onSettled: () => {
      invalidateProjectLists(qc);
      qc.invalidateQueries({ queryKey: ['storage-junk'] });
    },
  });
}

// ---------- Đổi tên project ----------
/** none = chỉ đổi tên · text = vẽ lại chữ thumbnail/intro + ghép lại video ·
 *  regen = tạo lại ảnh bìa (Gemini) + clip 00 (Flow) rồi như text. */
export type RenameRefresh = 'none' | 'text' | 'regen';

export interface RenameResult {
  old_name: string;
  name: string;
  changed: boolean;
  moved: boolean;
  job: string | null;
  project: Project;
}

export function useRenameProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: { name: string; refresh: RenameRefresh }) =>
      (await api.post<RenameResult>(`/api/projects/${id}/rename`, data)).data,
    // Tên đổi → đường dẫn media/ảnh/thumbnail đổi theo: làm mới mọi truy vấn.
    onSuccess: () => qc.invalidateQueries(),
  });
}
