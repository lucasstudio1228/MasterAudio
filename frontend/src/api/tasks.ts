import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export interface GlobalTask {
  id: string;
  source: 'auto' | 'video' | 'mix' | 'stem' | 'batch' | 'cover-suno';
  project_id: number | null;
  project_name: string;
  kind: string;
  label: string;
  tab: 'suno' | 'audio' | 'video';
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  percent: number | null;
  message: string;
  error: string | null;
  detail: string;
  ago_seconds?: number;
}

export interface GlobalTasksResponse {
  tasks: GlobalTask[];
  recent: GlobalTask[];
  can_resume?: boolean;
}

/** Mọi tác vụ đang chạy/xếp hàng của cả tool (không phụ thuộc project đang xem). */
export function useGlobalTasks() {
  return useQuery<GlobalTasksResponse>({
    queryKey: ['global-tasks'],
    queryFn: async () => (await api.get('/api/tasks/active')).data,
    refetchInterval: (q) => ((q.state.data?.tasks.length ?? 0) > 0 ? 2000 : 5000),
    refetchIntervalInBackground: true,
    retry: false,
  });
}

export function useGlobalTaskControl(action: 'pause-all' | 'stop-all' | 'resume-all') {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post(`/api/tasks/${action}`)).data as
      { ok: boolean; changed: number },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['global-tasks'] }),
  });
}

export function useAutoRunToEnd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (projectId?: number) => (await api.post(
      '/api/tasks/auto-run', null,
      { params: projectId != null ? { project_id: projectId } : undefined },
    )).data as { ok: boolean; project_id: number; status: string },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['global-tasks'] }),
  });
}
