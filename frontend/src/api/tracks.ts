import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { API_BASE, api } from './client';
import type { ScanResult, Track } from '../types';

export interface TrackRecoveryInfo {
  trash_count: number;
  orphan_count: number;
  total: number;
  items: {
    id: number; filename: string; file_exists: boolean; deleted_at: string;
    source_id?: number | null;   // bản cover thuộc bài gốc nào (null = track thường)
  }[];
  orphan_files: string[];
}

export function useTracks(projectId: number | undefined) {
  return useQuery({
    queryKey: ['tracks', projectId],
    enabled: projectId !== undefined,
    queryFn: async () =>
      (await api.get<Track[]>(`/api/projects/${projectId}/tracks`)).data,
  });
}

function invalidate(qc: ReturnType<typeof useQueryClient>, projectId: number) {
  qc.invalidateQueries({ queryKey: ['tracks', projectId] });
  qc.invalidateQueries({ queryKey: ['project', projectId] });
  qc.invalidateQueries({ queryKey: ['projects'] });
  qc.invalidateQueries({ queryKey: ['track-recovery', projectId] });
  // Tab Suno Cover / Lời bài hát đọc danh sách bản cover từ các query này.
  qc.invalidateQueries({ queryKey: ['sources', projectId] });
  qc.invalidateQueries({ queryKey: ['cover-songs', projectId] });
}

export function useTrackRecovery(projectId: number) {
  return useQuery({
    queryKey: ['track-recovery', projectId],
    queryFn: async () =>
      (await api.get<TrackRecoveryInfo>(`/api/projects/${projectId}/tracks/recovery`)).data,
  });
}

export function useRestoreAllTracks(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<{ restored: number; discovered: number; missing_files: number; total: number }>(
        `/api/projects/${projectId}/tracks/recovery/restore-all`,
      )).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useRestoreSelectedTracks(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { trash_ids: number[]; orphan_files: string[] }) =>
      (await api.post<{ restored: number; discovered: number; missing_files: number; total: number }>(
        `/api/projects/${projectId}/tracks/recovery/restore`, body,
      )).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function recoveryAudioUrl(projectId: number, trashId: number) {
  return `${API_BASE}/api/projects/${projectId}/tracks/recovery/${trashId}/audio`;
}

export function recoveryOrphanAudioUrl(projectId: number, name: string) {
  return `${API_BASE}/api/projects/${projectId}/tracks/recovery-orphan/audio?name=${encodeURIComponent(name)}`;
}

export function useScanFolder(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (folderPath: string) =>
      (
        await api.post<ScanResult>(`/api/projects/${projectId}/tracks/scan`, {
          folder_path: folderPath,
        })
      ).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useAddFile(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (filePath: string) =>
      (
        await api.post<Track>(`/api/projects/${projectId}/tracks/add-file`, {
          file_path: filePath,
        })
      ).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useUploadTrack(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      const res = await api.post<Track>(
        `/api/projects/${projectId}/tracks/upload`,
        form,
        {
          // File dài có thể to → tắt timeout mặc định của axios cho request này.
          timeout: 0,
          headers: { 'Content-Type': 'multipart/form-data' },
        },
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useDeleteTrack(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (trackId: number) =>
      (await api.delete(`/api/projects/${projectId}/tracks/${trackId}`)).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useDeleteAllTracks(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.delete(`/api/projects/${projectId}/tracks`)).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}
