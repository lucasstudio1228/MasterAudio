import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, API_BASE } from './client';

export type CoverLanguage = 'vi' | 'en';
export type CoverStyle = 'ballad' | 'remix' | 'acoustic' | 'original';

/** Giá trị thật sự dùng khi chạy tự động — ô nào để «theo thể loại» thì tool tự suy. */
export interface CoverEffective {
  language: CoverLanguage;
  cover_style: CoverStyle;
  thumb_subtitle: string;
  auto: { language: boolean; cover_style: boolean; thumb_subtitle: boolean };
  suggested: { language: CoverLanguage; cover_style: CoverStyle };
  genre: string;
  reason: string;
}

export interface CoverSettings {
  project_id: number;
  /** '' = theo dòng nhạc của project */
  language: CoverLanguage | '';
  /** '' = theo thể loại của project */
  cover_style: CoverStyle | '';
  repeat_to_fill: boolean;
  show_tracklist: boolean;
  show_lyrics: boolean;
  show_waveform: boolean;
  thumb_subtitle: string;
  whisper_model: string;
  effective?: CoverEffective;
}

export interface LyricLine {
  text: string;
  start: number;
  end: number;
}

export type AlignStatus = 'none' | 'queued' | 'running' | 'done' | 'failed' | 'edited';

export interface CoverSong {
  id: number;
  track_id: number;
  position: number;
  title: string;
  artist: string;
  filename: string;
  duration_seconds: number;
  tempo_factor?: number;   // tempo bản cover đã chỉnh ở tab ② (1 = bản Suno gốc)
  lyrics_text: string;
  lyric_line_count: number;
  lines: LyricLine[];
  align_status: AlignStatus;
  align_error: string | null;
  quality: { lines: number; suspicious: number; ok: boolean };
}

export interface AlignState {
  busy: boolean;
  current: number | null;
  message: string;
  pending: number;
}

const base = '/api/cover';

// `v` = tempo bản cover → đổi tempo ở tab ② xong không phát lại bản cũ trình duyệt đã lưu tạm.
export const songAudioUrl = (songId: number, v = '') =>
  `${API_BASE}${base}/songs/${songId}/audio${v ? `?v=${encodeURIComponent(v)}` : ''}`;
export const songLrcUrl = (songId: number) => `${API_BASE}${base}/songs/${songId}/lrc`;

export function useCoverSettings(projectId: number) {
  return useQuery<CoverSettings>({
    queryKey: ['cover-settings', projectId],
    queryFn: async () => (await api.get(`${base}/projects/${projectId}/settings`)).data,
  });
}

export function useUpdateCoverSettings(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (patch: Partial<CoverSettings>) =>
      (await api.put(`${base}/projects/${projectId}/settings`, patch)).data as CoverSettings,
    onSuccess: (data) => qc.setQueryData(['cover-settings', projectId], data),
  });
}

export function useCoverSongs(projectId: number) {
  return useQuery<{ songs: CoverSong[]; align: AlignState }>({
    queryKey: ['cover-songs', projectId],
    queryFn: async () => (await api.get(`${base}/projects/${projectId}/songs`)).data,
    // Đang có bài chờ/đang căn lời → hỏi lại mỗi 2 giây.
    refetchInterval: (q) => (q.state.data?.align?.busy ? 2000 : false),
  });
}

function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ['cover-songs', projectId] });
}

export function useReorderSongs(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (songIds: number[]) =>
      (await api.put(`${base}/projects/${projectId}/order`, { song_ids: songIds })).data,
    onSuccess: done,
  });
}

export function useUpdateSong(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; title?: string; artist?: string; lyrics_text?: string }) => {
      const { id, ...body } = v;
      return (await api.put(`${base}/songs/${id}`, body)).data as CoverSong;
    },
    onSuccess: done,
  });
}

export function useSaveLines(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; lines: LyricLine[] }) =>
      (await api.put(`${base}/songs/${v.id}/lines`, { lines: v.lines })).data as CoverSong,
    onSuccess: done,
  });
}

export function useShiftLines(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (v: { id: number; seconds: number }) =>
      (await api.post(`${base}/songs/${v.id}/shift`, { seconds: v.seconds })).data as CoverSong,
    onSuccess: done,
  });
}

export function useAlignSong(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (songId: number) => (await api.post(`${base}/songs/${songId}/align`)).data,
    onSuccess: done,
  });
}

export function useAlignAll(projectId: number) {
  const done = useInvalidate(projectId);
  return useMutation({
    mutationFn: async (onlyMissing: boolean) =>
      (await api.post(`${base}/projects/${projectId}/align-all`, { only_missing: onlyMissing }))
        .data,
    onSuccess: done,
  });
}
