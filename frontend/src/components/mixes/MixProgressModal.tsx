import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createProgressStream, downloadUrl } from '../../api/client';
import { useCancelMix } from '../../api/mixes';
import type { MixProgressEvent } from '../../types';

interface Props {
  mixId: number;
  projectId: number;
  onClose: () => void;
}

const STEPS = [
  'Load tracks',
  'Playlist',
  'Names',
  'Render',
  'Metadata',
  'Done',
];

export default function MixProgressModal({ mixId, projectId, onClose }: Props) {
  const qc = useQueryClient();
  const cancelMix = useCancelMix(projectId);
  const [progress, setProgress] = useState<MixProgressEvent>({
    step: 0,
    step_name: 'Starting…',
    percent: 0,
  });
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const es = createProgressStream(
      mixId,
      (event) => {
        setProgress(event);
        setLog((prev) => [
          ...prev,
          `[${event.step}/6] ${event.step_name} — ${event.percent.toFixed(0)}%${
            event.message ? ` ${event.message}` : ''
          }`,
        ]);
      },
      () => {
        setDone(true);
        qc.invalidateQueries({ queryKey: ['mixes', projectId] });
        qc.invalidateQueries({ queryKey: ['project', projectId] });
      },
      () => setError('Connection lost'),
    );
    return () => es.close();
  }, [mixId, projectId, qc]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [log]);

  const cancelled = progress.step_name === 'cancelled';
  const failed = Boolean(error) || progress.step_name === 'failed';

  const handleCancel = () => {
    if (!confirm('Huỷ render nhạc? Phần đã tạo sẽ được giữ lại.')) return;
    setCancelling(true);
    cancelMix.mutate(mixId, {
      onError: () => setCancelling(false),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="flex w-full max-w-2xl gap-6 rounded-xl bg-surface p-6 shadow-xl">
        {/* Steps sidebar */}
        <ol className="w-40 shrink-0 space-y-2 text-sm">
          {STEPS.map((label, i) => {
            const n = i + 1;
            const active = progress.step === n && !done;
            const complete = done || progress.step > n;
            return (
              <li
                key={label}
                className={`flex items-center gap-2 ${
                  complete
                    ? 'text-green-400'
                    : active
                      ? 'text-accent'
                      : 'text-gray-500'
                }`}
              >
                <span>{complete ? '✅' : active ? '🔄' : '○'}</span>
                {label}
              </li>
            );
          })}
        </ol>

        {/* Main */}
        <div className="flex-1">
          <h3 className="mb-4 text-lg font-semibold text-white">
            {cancelled
              ? '⛔ Đã huỷ'
              : done
                ? '✅ Mix Complete'
                : error
                  ? '❌ Failed'
                  : 'Rendering Mix…'}
          </h3>

          <div className="mb-1 flex justify-between text-xs text-gray-400">
            <span>{progress.step_name}</span>
            <span>{progress.percent.toFixed(0)}%</span>
          </div>
          <div className="mb-4 h-3 overflow-hidden rounded-full bg-background">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${done ? 100 : progress.percent}%` }}
            />
          </div>

          <div
            ref={logRef}
            className="mb-4 h-40 overflow-auto rounded-lg bg-background p-2 font-mono text-xs text-gray-400"
          >
            {log.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
            {error && <div className="text-red-400">{error}</div>}
          </div>

          <div className="flex items-center justify-between gap-3">
            {/* Trái: khi ĐANG chạy → nút ẩn xuống thanh dưới để làm việc khác */}
            <div>
              {!done && !failed && !cancelled && (
                <button
                  onClick={onClose}
                  className="rounded-lg bg-surface-2 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-white/10"
                  title="Tiếp tục chạy nền, theo dõi ở thanh dưới màn hình"
                >
                  🔽 Ẩn xuống thanh dưới
                </button>
              )}
            </div>

            {/* Phải: huỷ (khi chạy) hoặc tải + đóng (khi xong) */}
            <div className="flex gap-3">
              {!done && !failed && !cancelled && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 text-sm font-medium text-red-300 hover:bg-red-950/60 disabled:opacity-50"
                >
                  {cancelling ? 'Đang huỷ…' : '✕ Huỷ'}
                </button>
              )}
              {done && !cancelled && (
                <a
                  href={downloadUrl(mixId, 'wav')}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
                >
                  ⬇ Download WAV
                </a>
              )}
              {(done || failed || cancelled) && (
                <button
                  onClick={onClose}
                  className="rounded-lg bg-surface-2 px-4 py-2 text-sm text-gray-200 hover:bg-white/10"
                >
                  Close
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
