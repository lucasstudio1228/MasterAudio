import { useEffect } from 'react';

/**
 * Giữ MÀN HÌNH SÁNG phía trình duyệt khi `active` = true (bổ trợ cho cơ chế
 * chống-khoá cấp OS ở backend `keep_awake.py`). Dùng Screen Wake Lock API.
 * Best-effort: trình duyệt/hệ không hỗ trợ hoặc bị từ chối thì bỏ qua im lặng.
 * Tự xin lại lock khi tab được hiển thị lại (lock bị nhả khi tab ẩn/minimize).
 */
export function useWakeLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let sentinel: any = null;
    let released = false;

    const request = async () => {
      try {
        const nav: any = navigator;
        if (nav.wakeLock?.request) {
          sentinel = await nav.wakeLock.request('screen');
        }
      } catch {
        /* không hỗ trợ hoặc bị từ chối — bỏ qua */
      }
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !released) request();
    };

    request();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      released = true;
      document.removeEventListener('visibilitychange', onVisible);
      try {
        sentinel?.release?.();
      } catch {
        /* noop */
      }
    };
  }, [active]);
}
