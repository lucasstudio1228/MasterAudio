import axios from 'axios';
import type { MixProgressEvent } from '../types';

// Production: SPA được backend phục vụ cùng origin → dùng URL tương đối ('')
// để gọi API cùng host, tránh CORS dù mở bằng localhost, 127.0.0.1 hay IP LAN.
// Dev (Vite :5184): trỏ thẳng backend :8200 (CORS đã cho phép :5184).
// Có thể override bằng biến môi trường VITE_API_BASE khi build.
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (import.meta.env.DEV ? 'http://localhost:8200' : '');

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
});

export function downloadUrl(mixId: number, fileType: string): string {
  return `${API_BASE}/api/mixes/${mixId}/download/${fileType}`;
}

/** SSE helper – dùng native EventSource (axios không hỗ trợ SSE). */
export function createProgressStream(
  mixId: number,
  onProgress: (event: MixProgressEvent) => void,
  onDone: () => void,
  onError: (err: Event) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}/api/mixes/${mixId}/progress`);
  es.addEventListener('progress', (e) =>
    onProgress(JSON.parse((e as MessageEvent).data)),
  );
  es.addEventListener('done', () => {
    es.close();
    onDone();
  });
  es.addEventListener('error', (e) => {
    // EventSource tự reconnect khi lỗi mạng; chỉ báo khi thực sự đóng.
    if (es.readyState === EventSource.CLOSED) onError(e);
  });
  return es;
}
