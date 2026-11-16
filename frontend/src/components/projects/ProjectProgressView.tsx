import type { ProgressStep, ProjectProgress } from '../../types';

/** Màu từng ô bước: xong = xanh, đang chạy = accent, lỗi = đỏ, chờ xử lý = vàng. */
const STEP_CLS: Record<string, string> = {
  done: 'bg-emerald-500/80 text-white',
  skipped: 'bg-surface-2 text-gray-500 line-through',
  running: 'bg-accent text-white animate-pulse',
  queued: 'bg-accent/40 text-white',
  paused: 'bg-amber-500/70 text-white',
  waiting: 'bg-amber-500/40 text-amber-100',
  stopped: 'bg-amber-500/40 text-amber-100',
  blocked: 'bg-amber-500/80 text-white',
  error: 'bg-red-600/80 text-white',
  pending: 'bg-surface-2 text-gray-500',
};

const STATE_ICON: Record<string, string> = {
  done: '✅', running: '⏳', paused: '⏸', queued: '🕒', blocked: '⚠️',
  error: '❌', stopped: '⏹', new: '🆕', unknown: '❔',
};

const SUMMARY_CLS: Record<string, string> = {
  done: 'text-emerald-300',
  running: 'text-accent',
  queued: 'text-gray-300',
  paused: 'text-amber-300',
  blocked: 'text-amber-300',
  stopped: 'text-amber-200/90',
  error: 'text-red-300',
  new: 'text-gray-400',
  unknown: 'text-gray-400',
};

function stepTitle(s: ProgressStep): string {
  const state: Record<string, string> = {
    done: 'đã xong', running: 'đang chạy', paused: 'tạm dừng', queued: 'xếp hàng',
    waiting: 'dừng giữa chừng', stopped: 'đã dừng', blocked: 'chờ xử lý',
    error: 'lỗi', pending: 'chưa làm', skipped: 'bỏ qua',
  };
  return `${s.label}: ${state[s.state] ?? s.state}${s.detail ? ` — ${s.detail}` : ''}`;
}

interface Props {
  progress?: ProjectProgress;
  /** true = backend chưa có API (chưa khởi động lại) hoặc lỗi đọc. */
  unavailable?: boolean;
}

/** Tình trạng project: các ô Nhạc → Mix + 1 dòng tóm tắt. */
export default function ProjectProgressView({ progress, unavailable }: Props) {
  if (!progress) {
    return (
      <p className="mb-4 text-xs text-gray-500">
        {unavailable ? 'Chưa đọc được tình trạng (khởi động lại backend).' : 'Đang đọc tình trạng…'}
      </p>
    );
  }
  const pct = progress.percent;
  return (
    <div className="mb-4">
      <div className="mb-1.5 flex gap-1">
        {progress.steps.map((s) => (
          <div
            key={s.key}
            title={stepTitle(s)}
            className={`flex-1 truncate rounded px-1 py-0.5 text-center text-[10px] font-medium ${
              STEP_CLS[s.state] ?? STEP_CLS.pending
            }`}
          >
            {s.label}
          </div>
        ))}
      </div>
      <p
        className={`line-clamp-2 text-xs ${SUMMARY_CLS[progress.state] ?? 'text-gray-300'}`}
        title={progress.summary}
      >
        {STATE_ICON[progress.state] ?? '•'} {progress.summary}
        <span className="ml-1 text-gray-500">
          ({progress.done_steps}/{progress.total_steps} bước)
        </span>
      </p>
      {pct !== null && pct !== undefined && progress.state !== 'done' && (
        <div className="mt-1 h-1 overflow-hidden rounded bg-surface-2">
          <div
            className="h-full bg-accent transition-all"
            style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
          />
        </div>
      )}
    </div>
  );
}
