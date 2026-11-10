import { useMixes, useCancelMix, usePauseMix, useResumeMix } from '../../api/mixes';
import {
  useVideoStatus,
  useCancelVideo,
  usePauseVideo,
  useResumeVideo,
} from '../../api/video';
import { useWakeLock } from '../../hooks/useWakeLock';

const VIDEO_KIND_LABEL: Record<string, string> = {
  images: 'Đang tạo ảnh',
  clips: 'Đang tạo clip',
  assemble: 'Đang ghép video',
  full: 'Đang dựng toàn bộ video',
  rebuild: 'Đang dựng lại video từ ảnh',
  upload: 'Đang đăng nháp YouTube',
  upload_short: 'Đang tạo & đăng nháp YouTube Short',
  continue: 'Đang chạy tiếp chu trình (→ ghép → nháp YouTube → Short)',
};

/**
 * Thanh trạng thái toàn cục (cố định đáy màn hình): hiển thị quy trình ĐANG
 * CHẠY (render nhạc HOẶC dựng video) kèm hoạt động + % hoàn thành + nút
 * Tạm dừng/Tiếp tục + nút Huỷ (huỷ TẤT CẢ tác vụ đang chạy).
 * Đồng thời giữ màn hình sáng (chống khoá) trong lúc có job chạy.
 * Không có job → không hiển thị gì.
 */
export default function WorkflowStatusBar({
  projectId,
  onReopenMix,
}: {
  projectId: number;
  /** Mở lại modal chi tiết của mix đang chạy (nếu người dùng đã ẩn nó). */
  onReopenMix?: (mixId: number) => void;
}) {
  const { data: videoSt } = useVideoStatus(projectId);
  const { data: mixes } = useMixes(projectId);
  const cancelVideo = useCancelVideo(projectId);
  const cancelMix = useCancelMix(projectId);
  const pauseVideo = usePauseVideo(projectId);
  const resumeVideo = useResumeVideo(projectId);
  const pauseMix = usePauseMix(projectId);
  const resumeMix = useResumeMix(projectId);

  const ACTIVE = ['running', 'pending', 'paused'];

  const vjob = videoSt?.job;
  const videoActive = !!vjob && ACTIVE.includes(vjob.status);

  const activeMixes = (mixes ?? []).filter((m) => ACTIVE.includes(m.status));
  const activeMix = activeMixes[0];
  const mixActive = Boolean(activeMix);

  const active = videoActive || mixActive;
  useWakeLock(active);

  if (!active) return null;

  // Ưu tiên hiển thị job video nếu cả hai cùng chạy (hiếm); nếu không thì mix.
  const isVideo = videoActive;
  const paused = isVideo
    ? vjob!.status === 'paused'
    : activeMix!.status === 'paused';

  const label = isVideo
    ? (VIDEO_KIND_LABEL[vjob!.kind] ?? 'Đang chạy video')
    : 'Đang render nhạc';
  const percent = isVideo
    ? vjob!.percent
    : (activeMix!.progress_percent ?? 0);
  const message = isVideo
    ? (vjob!.message || '')
    : (activeMix!.progress_step_name
        ? `Bước ${activeMix!.progress_step ?? 0}/6 · ${activeMix!.progress_step_name}`
        : '');

  const cancelling = cancelVideo.isPending || cancelMix.isPending;
  const pausing = isVideo
    ? pauseVideo.isPending || resumeVideo.isPending
    : pauseMix.isPending || resumeMix.isPending;

  // Số tác vụ đang chạy (video + các mix) để hỏi xác nhận cho đúng.
  const totalActive = (videoActive ? 1 : 0) + activeMixes.length;

  const onCancel = () => {
    const msg =
      totalActive > 1
        ? `Huỷ TẤT CẢ ${totalActive} tác vụ đang chạy? Phần đã tạo sẽ được giữ lại.`
        : 'Huỷ quy trình đang chạy? Phần đã tạo sẽ được giữ lại.';
    if (!confirm(msg)) return;
    if (videoActive) cancelVideo.mutate();
    activeMixes.forEach((m) => cancelMix.mutate(m.id));
  };

  const onPauseResume = () => {
    if (isVideo) {
      if (paused) resumeVideo.mutate();
      else pauseVideo.mutate();
    } else {
      if (paused) resumeMix.mutate(activeMix!.id);
      else pauseMix.mutate(activeMix!.id);
    }
  };

  return (
    <div className="fixed bottom-10 left-0 right-0 z-40 border-t border-white/10 bg-surface/95 px-4 py-2.5 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-4">
        <span className="shrink-0 text-lg" aria-hidden>
          <span className={paused ? '' : 'animate-pulse'}>
            {isVideo ? '🎬' : '🎵'}
          </span>
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate font-medium text-gray-200">
              {paused && <span className="text-yellow-300">⏸ Tạm dừng · </span>}
              {label}
              {message && (
                <span className="ml-2 font-normal text-gray-500">— {message}</span>
              )}
            </span>
            <span className="shrink-0 tabular-nums text-gray-400">
              {Math.round(percent)}%
            </span>
          </div>
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background">
            <div
              className={`h-full transition-all ${paused ? 'bg-yellow-500/70' : 'bg-accent'}`}
              style={{ width: `${Math.min(Math.max(percent, 2), 100)}%` }}
            />
          </div>
        </div>
        {!isVideo && activeMix && onReopenMix && (
          <button
            onClick={() => onReopenMix(activeMix.id)}
            className="shrink-0 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-gray-200 transition-colors hover:bg-white/10"
            title="Mở lại bảng chi tiết"
          >
            ⤢ Mở
          </button>
        )}
        <button
          onClick={onPauseResume}
          disabled={pausing || cancelling}
          className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-950/30 px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-950/60 disabled:opacity-50"
        >
          {pausing ? '…' : paused ? '▶ Tiếp tục' : '⏸ Tạm dừng'}
        </button>
        <button
          onClick={onCancel}
          disabled={cancelling}
          className="shrink-0 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-1.5 text-xs font-medium text-red-300 transition-colors hover:bg-red-950/60 disabled:opacity-50"
        >
          {cancelling ? 'Đang huỷ…' : totalActive > 1 ? `✕ Huỷ tất cả (${totalActive})` : '✕ Huỷ'}
        </button>
      </div>
    </div>
  );
}
