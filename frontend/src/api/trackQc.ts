import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, API_BASE } from './client';
import type { QcResponse } from '../types';

const base = (pid: number) => `/api/projects/${pid}/qc`;

export const trackAudioUrl = (pid: number, tid: number) =>
  `${API_BASE}${base(pid)}/tracks/${tid}/audio`;

/** Báo cáo rà soát + dòng thời gian mix. Poll khi đang thay bài / render mix. */
export function useTrackQc(projectId: number) {
  return useQuery<QcResponse>({
    queryKey: ['track-qc', projectId],
    queryFn: async () => (await api.get(base(projectId))).data,
    refetchInterval: (q) => {
      const d = q.state.data;
      const running = d?.job && (d.job.status === 'pending' || d.job.status === 'running');
      return running || d?.mix_busy || d?.auto_active ? 2000 : false;
    },
  });
}

function useQcPost<TBody = void>(projectId: number, path: string | ((b: TBody) => string),
                                 timeout = 30000) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: TBody) => {
      const p = typeof path === 'function' ? path(body) : path;
      return (await api.post(`${base(projectId)}/${p}`, body ?? {}, { timeout })).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['track-qc', projectId] });
      qc.invalidateQueries({ queryKey: ['tracks', projectId] });
      qc.invalidateQueries({ queryKey: ['mixes', projectId] });
    },
  });
}

export const useAnalyzeTracks = (pid: number) => useQcPost<void>(pid, 'analyze', 0);

export const useFlagTrack = (pid: number) =>
  useQcPost<{ track_id: number; flagged: boolean }>(
    pid, (b) => `tracks/${b.track_id}/flag`);

export const useRegenerateTracks = (pid: number) =>
  useQcPost<{ track_ids: number[]; max_creates_per_track: number }>(pid, 'regenerate');

export const useSetAutoRegen = (pid: number) =>
  useQcPost<{ enabled: boolean }>(pid, 'auto');

export const usePatchMix = (pid: number) => useQcPost<void>(pid, 'patch-mix');

export const useRemuxVideo = (pid: number) => useQcPost<void>(pid, 'remux');
