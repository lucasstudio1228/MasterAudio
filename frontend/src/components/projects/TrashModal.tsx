import { useEffect, useState } from 'react';
import {
  useEmptyTrash,
  usePurgeProject,
  useRestoreProject,
  useTrash,
} from '../../api/projects';
import { formatBytes } from '../../api/storage';
import type { TrashItem } from '../../types';

interface Props {
  open: boolean;
  onClose: () => void;
}

function errText(err: unknown): string | null {
  if (!err) return null;
  return (err as any)?.response?.data?.detail ?? (err as Error).message ?? String(err);
}

function daysLeftText(item: TrashItem): string {
  if (item.days_left <= 0) return 'sẽ xoá ở lượt dọn kế tiếp';
  if (item.days_left < 1) return `còn ${Math.max(1, Math.round(item.days_left * 24))} giờ`;
  return `còn ${item.days_left.toFixed(item.days_left < 2 ? 1 : 0)} ngày`;
}

/** «🗑 Thùng rác»: project đã xoá được giữ 7 ngày — khôi phục hoặc xoá vĩnh viễn ngay. */
export default function TrashModal({ open, onClose }: Props) {
  const trash = useTrash(open);
  const restore = useRestoreProject();
  const purge = usePurgeProject();
  const empty = useEmptyTrash();
  const [confirmPurge, setConfirmPurge] = useState<TrashItem | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      restore.reset();
      purge.reset();
      empty.reset();
      setNote(null);
      setConfirmPurge(null);
      setConfirmEmpty(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const items = trash.data?.items ?? [];
  const days = trash.data?.retention_days ?? 7;
  const busy = restore.isPending || purge.isPending || empty.isPending;
  const error =
    errText(trash.error) ?? errText(restore.error) ?? errText(purge.error) ?? errText(empty.error);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl bg-surface p-6 shadow-xl">
        <h3 className="mb-1 text-lg font-semibold text-white">🗑 Thùng rác</h3>
        <p className="mb-4 text-sm text-gray-400">
          Project đã xoá được giữ nguyên dữ liệu trong thư mục <code>Trash/</code> {days} ngày
          rồi tool mới tự xoá vĩnh viễn. Trong thời gian đó có thể khôi phục đầy đủ.
        </p>

        {error && <p role="alert" className="mb-3 text-sm text-red-300">{error}</p>}
        {note && <p role="status" className="mb-3 text-sm text-emerald-300">{note}</p>}

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {trash.isLoading ? (
            <p className="text-sm text-gray-400">Đang đọc Thùng rác…</p>
          ) : items.length === 0 ? (
            <p className="py-8 text-center text-sm text-gray-500">Thùng rác trống.</p>
          ) : (
            <ul className="space-y-2">
              {items.map((it) => (
                <li key={it.id} className="rounded-lg bg-surface-2/50 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-white">{it.name}</span>
                    <span className="text-xs text-gray-400">
                      {formatBytes(it.size_bytes)} · xoá lúc{' '}
                      {new Date(it.deleted_at).toLocaleString('vi-VN')} ·{' '}
                      <b className={it.days_left < 1 ? 'text-amber-300' : 'text-gray-300'}>
                        {daysLeftText(it)}
                      </b>
                    </span>
                    <div className="ml-auto flex gap-2">
                      <button
                        disabled={busy}
                        onClick={() =>
                          restore.mutate(it.id, {
                            onSuccess: () => setNote(`Đã khôi phục «${it.name}».`),
                          })
                        }
                        className="rounded-lg bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50"
                      >
                        ↩ Khôi phục
                      </button>
                      <button
                        disabled={busy}
                        onClick={() => setConfirmPurge(it)}
                        className="rounded-lg bg-surface-2 px-3 py-1 text-xs text-gray-300 hover:bg-red-600 hover:text-white disabled:opacity-50"
                      >
                        Xoá vĩnh viễn
                      </button>
                    </div>
                  </div>
                  {it.in_place.length > 0 && (
                    <p className="mt-1 text-xs text-amber-300/90">
                      {it.in_place.length} thư mục bị khoá lúc xoá nên còn nằm tại chỗ — vẫn được
                      khôi phục / dọn cùng project.
                    </p>
                  )}
                  {confirmPurge?.id === it.id && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 rounded bg-red-900/30 p-2 text-xs text-red-200">
                      Xoá vĩnh viễn «{it.name}» ngay ({formatBytes(it.size_bytes)})? Không thể hoàn
                      tác.
                      <button
                        disabled={busy}
                        onClick={() =>
                          purge.mutate(it.id, {
                            onSuccess: (r) =>
                              setNote(
                                `Đã xoá vĩnh viễn «${it.name}», giải phóng ${formatBytes(r?.freed_bytes)}.`,
                              ),
                            onSettled: () => setConfirmPurge(null),
                          })
                        }
                        className="ml-auto rounded bg-red-600 px-2 py-1 font-medium text-white hover:bg-red-700 disabled:opacity-50"
                      >
                        {purge.isPending ? 'Đang xoá…' : 'Xoá vĩnh viễn'}
                      </button>
                      <button
                        onClick={() => setConfirmPurge(null)}
                        className="rounded px-2 py-1 text-gray-300 hover:bg-surface-2"
                      >
                        Huỷ
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {items.length > 0 && (
            <span className="text-sm text-gray-400">
              {items.length} project · {formatBytes(trash.data?.total_bytes)}
            </span>
          )}
          {items.length > 0 && !confirmEmpty && (
            <button
              disabled={busy}
              onClick={() => setConfirmEmpty(true)}
              className="rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-300 hover:bg-red-600 hover:text-white disabled:opacity-50"
            >
              Dọn sạch Thùng rác
            </button>
          )}
          {confirmEmpty && (
            <span className="flex flex-wrap items-center gap-2 text-sm text-red-200">
              Xoá vĩnh viễn cả {items.length} project ngay?
              <button
                disabled={busy}
                onClick={() =>
                  empty.mutate(undefined, {
                    onSuccess: (r) => {
                      const failed = r.results.filter((x) => !x.ok);
                      setNote(
                        `Đã xoá vĩnh viễn ${r.deleted} project, giải phóng ${formatBytes(r.freed_bytes)}.` +
                          (failed.length
                            ? ` Bỏ qua ${failed.length}: ${failed
                                .map((x) => `«${x.name}» (${x.error})`)
                                .join('; ')}`
                            : ''),
                      );
                    },
                    onSettled: () => setConfirmEmpty(false),
                  })
                }
                className="rounded bg-red-600 px-3 py-1 font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {empty.isPending ? 'Đang xoá… (có thể mất vài phút)' : 'Xoá hết'}
              </button>
              <button
                onClick={() => setConfirmEmpty(false)}
                className="rounded px-2 py-1 text-gray-300 hover:bg-surface-2"
              >
                Huỷ
              </button>
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2"
          >
            Đóng
          </button>
        </div>
      </div>
    </div>
  );
}
