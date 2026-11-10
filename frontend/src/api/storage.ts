import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { JunkCleanResult, JunkScan, ProjectDiskUsage } from '../types';

/** 1234567890 → "1.23 GB" (đơn vị thập phân như Windows Explorer hiển thị ổ đĩa). */
export function formatBytes(bytes: number | undefined | null): string {
  const value = bytes ?? 0;
  if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e10 ? 1 : 2)} GB`;
  if (value >= 1e6) return `${Math.round(value / 1e6)} MB`;
  if (value >= 1e3) return `${Math.round(value / 1e3)} KB`;
  return `${value} B`;
}

const STALE_BACKEND =
  'Backend đang chạy bản cũ, chưa có chức năng này — tắt rồi mở lại start.bat ' +
  '(khi không còn tác vụ nào đang chạy).';

/** Backend chưa khởi động lại sau khi cập nhật: API mới trả trang HTML
 *  (catch-all SPA) hoặc 404/405 → báo rõ thay vì vỡ giao diện. */
export async function newApi<T>(request: () => Promise<{ data: unknown }>): Promise<T> {
  let data: unknown;
  try {
    ({ data } = await request());
  } catch (err) {
    const status = (err as any)?.response?.status;
    if (status === 404 || status === 405) throw new Error(STALE_BACKEND);
    throw err;
  }
  if (data === null || typeof data !== 'object') throw new Error(STALE_BACKEND);
  return data as T;
}

/** Dung lượng thật trên đĩa của từng project (hardlink tính 1 lần). */
export function useProjectDiskUsage() {
  return useQuery<ProjectDiskUsage>({
    queryKey: ['storage-usage'],
    queryFn: () =>
      newApi<ProjectDiskUsage>(() => api.get('/api/storage/usage', { timeout: 120_000 })),
    staleTime: 60_000,
    retry: false,
  });
}

/** Chỉ quét (không xoá) — chạy khi mở hộp «Dọn dữ liệu thừa». */
export function useStorageJunk(enabled: boolean) {
  return useQuery<JunkScan>({
    queryKey: ['storage-junk'],
    enabled,
    queryFn: () =>
      newApi<JunkScan>(() => api.get('/api/storage/junk', { timeout: 300_000 })),
    staleTime: 0,
    retry: false,
  });
}

export function useCleanJunk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keys: string[]) =>
      newApi<JunkCleanResult>(() =>
        api.post('/api/storage/clean', { keys }, { timeout: 600_000 })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['storage-junk'] });
      qc.invalidateQueries({ queryKey: ['storage-usage'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}
