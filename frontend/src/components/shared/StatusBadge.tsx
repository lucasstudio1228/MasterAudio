import type { MixStatus } from '../../types';

type Badge = { label: string; icon: string; cls: string };

const CONFIG: Record<MixStatus, Badge> = {
  pending: { label: 'Pending', icon: '🕐', cls: 'bg-yellow-500/15 text-yellow-300' },
  running: { label: 'Running', icon: '🔄', cls: 'bg-blue-500/15 text-blue-300' },
  paused: { label: 'Tạm dừng', icon: '⏸', cls: 'bg-amber-500/15 text-amber-300' },
  completed: { label: 'Completed', icon: '✅', cls: 'bg-green-500/15 text-green-300' },
  failed: { label: 'Failed', icon: '❌', cls: 'bg-red-500/15 text-red-300' },
  cancelled: { label: 'Đã huỷ', icon: '⛔', cls: 'bg-gray-500/15 text-gray-300' },
};

// Fallback an toàn: trạng thái lạ (từ backend mới) không được làm sập UI.
const FALLBACK: Badge = { label: 'Unknown', icon: '•', cls: 'bg-gray-500/15 text-gray-300' };

export default function StatusBadge({ status }: { status: MixStatus }) {
  const c = CONFIG[status] ?? FALLBACK;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${c.cls}`}
    >
      <span>{c.icon}</span>
      {c.label}
    </span>
  );
}
