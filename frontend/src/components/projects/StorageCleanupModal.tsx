import { useEffect, useMemo, useState } from 'react';
import { formatBytes, useCleanJunk, useStorageJunk } from '../../api/storage';

interface Props {
  open: boolean;
  onClose: () => void;
}

/** «🧹 Dọn dữ liệu thừa»: quét (chỉ đọc) → chọn nhóm/mục → dọn. Server luôn quét
 *  lại trước khi xoá, project đang chạy tác vụ tự được bỏ qua. */
export default function StorageCleanupModal({ open, onClose }: Props) {
  const junk = useStorageJunk(open);
  const clean = useCleanJunk();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);

  // Mặc định chọn mọi mục của lần quét mới.
  useEffect(() => {
    if (junk.data) {
      setSelected(new Set(junk.data.categories.flatMap((c) => c.items.map((i) => i.key))));
    }
  }, [junk.data]);

  useEffect(() => {
    if (open) clean.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const sizeOf = useMemo(() => {
    const map = new Map<string, number>();
    junk.data?.categories.forEach((c) => c.items.forEach((i) => map.set(i.key, i.bytes)));
    return map;
  }, [junk.data]);

  if (!open) return null;

  const selectedBytes = [...selected].reduce((sum, key) => sum + (sizeOf.get(key) ?? 0), 0);
  const scanError =
    (junk.error as any)?.response?.data?.detail ?? (junk.error as Error | null)?.message;
  const cleanError =
    (clean.error as any)?.response?.data?.detail ?? (clean.error as Error | null)?.message;

  function toggleCategory(keys: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (on ? next.add(k) : next.delete(k)));
      return next;
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl bg-surface p-6 shadow-xl">
        <h3 className="mb-1 text-lg font-semibold text-white">🧹 Dọn dữ liệu thừa</h3>
        <p className="mb-4 text-sm text-gray-400">
          Chỉ liệt kê dữ liệu chắc chắn không còn dùng. Project đang chạy tác vụ được bỏ
          qua. Thư mục <code>Projects/</code> (output tool cũ) không bao giờ bị đụng tới.
        </p>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {junk.isFetching && !junk.data && <p className="text-gray-400">Đang quét ổ đĩa…</p>}
          {scanError && <p role="alert" className="text-red-300">{scanError}</p>}
          {junk.data && (
            <div className="space-y-2">
              {junk.data.categories.map((cat) => {
                const keys = cat.items.map((i) => i.key);
                const chosen = keys.filter((k) => selected.has(k)).length;
                const empty = keys.length === 0;
                return (
                  <div key={cat.key} className="rounded-lg border border-white/5 bg-surface-2/40 p-3">
                    <label className={`flex items-start gap-3 ${empty ? 'opacity-50' : ''}`}>
                      <input
                        type="checkbox"
                        disabled={empty}
                        checked={!empty && chosen === keys.length}
                        ref={(el) => {
                          if (el) el.indeterminate = chosen > 0 && chosen < keys.length;
                        }}
                        onChange={(e) => toggleCategory(keys, e.target.checked)}
                        className="mt-1 h-4 w-4 accent-accent"
                      />
                      <span className="flex-1">
                        <span className="flex justify-between gap-2 text-sm font-medium text-white">
                          <span>{cat.title}</span>
                          <span className="whitespace-nowrap text-gray-300">
                            {empty ? 'không có' : formatBytes(cat.bytes)}
                          </span>
                        </span>
                        <span className="block text-xs text-gray-400">{cat.description}</span>
                      </span>
                    </label>
                    {!empty && (
                      <button
                        onClick={() => setExpanded(expanded === cat.key ? null : cat.key)}
                        className="ml-7 mt-1 text-xs text-accent hover:underline"
                      >
                        {expanded === cat.key ? 'Ẩn chi tiết' : `Xem ${keys.length} mục`}
                      </button>
                    )}
                    {expanded === cat.key && (
                      <ul className="ml-7 mt-2 space-y-1">
                        {cat.items.map((item) => (
                          <li key={item.key}>
                            <label className="flex items-start gap-2 text-xs text-gray-300">
                              <input
                                type="checkbox"
                                checked={selected.has(item.key)}
                                onChange={(e) => toggleCategory([item.key], e.target.checked)}
                                className="mt-0.5 accent-accent"
                              />
                              <span className="flex-1">
                                {item.label}
                                {item.detail && <span className="text-gray-500"> — {item.detail}</span>}
                              </span>
                              <span className="whitespace-nowrap">{formatBytes(item.bytes)}</span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
              {junk.data.busy_project_ids.length > 0 && (
                <p className="text-xs text-amber-300">
                  Bỏ qua project đang chạy tác vụ: #{junk.data.busy_project_ids.join(', #')}
                </p>
              )}
              {junk.data.untouched.map((u) => (
                <p key={u.label} className="text-xs text-gray-500">
                  Không tự xoá: {u.label} — {formatBytes(u.bytes)}
                </p>
              ))}
            </div>
          )}
          {clean.data && (
            <div role="status" className="mt-4 rounded-lg bg-emerald-900/30 p-3 text-sm text-emerald-200">
              Đã giải phóng {formatBytes(clean.data.freed_bytes)} ({clean.data.done.length} mục).
              {clean.data.errors.map((e) => (
                <p key={e} className="text-amber-300">{e}</p>
              ))}
              <p className="mt-1 text-xs text-gray-400">
                Lưu ý: WAV gộp bằng hardlink vẫn hiện đủ ở cả 2 thư mục — xem dung lượng
                trống của ổ đĩa (This PC) để thấy phần đã giải phóng.
              </p>
            </div>
          )}
          {cleanError && <p role="alert" className="mt-3 text-red-300">{cleanError}</p>}
        </div>

        <div className="mt-4 flex items-center justify-between gap-3">
          <span className="text-sm text-gray-300">
            Đã chọn: <b className="text-white">{formatBytes(selectedBytes)}</b>
          </span>
          <div className="flex gap-3">
            <button
              onClick={() => junk.refetch()}
              disabled={junk.isFetching || clean.isPending}
              className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2 disabled:opacity-50"
            >
              Quét lại
            </button>
            <button
              onClick={onClose}
              className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2"
            >
              Đóng
            </button>
            <button
              onClick={() => clean.mutate([...selected])}
              disabled={selected.size === 0 || clean.isPending || junk.isFetching}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              {clean.isPending ? 'Đang dọn…' : `🧹 Dọn ${formatBytes(selectedBytes)}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
