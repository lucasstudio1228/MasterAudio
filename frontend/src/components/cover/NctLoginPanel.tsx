import { useState } from 'react';
import { errText, useNctLogin, useNctLoginWindow, type NctWorker } from '../../api/sources';

const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';

/** Nút mở / đóng cửa sổ nhaccuatui (profile riêng của tool) để bạn TỰ đăng nhập.
 *  Dùng ở Cài đặt và tab ① Tải nhạc gốc (tab ① truyền sẵn `worker` của nó). */
export default function NctLoginPanel({ worker: external }: { worker?: NctWorker }) {
  const own = useNctLogin(!external);
  const worker = external ?? own.data;
  const { open, close } = useNctLoginWindow();
  const [err, setErr] = useState<string | null>(null);
  const loggedIn = worker?.login?.logged_in;
  const loginOpen = worker?.mode === 'login';
  const downloading = !!worker?.busy && !loginOpen;
  return (
    <div className="rounded-lg border border-white/5 bg-surface-2/50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={loggedIn ? 'text-green-300' : 'text-orange-300'}>
          {loggedIn === true && '✅ Đã đăng nhập nhaccuatui'}
          {loggedIn === false && '⚠️ Chưa đăng nhập nhaccuatui — cần để tải file nhạc'}
          {loggedIn == null && 'ℹ️ Chưa kiểm tra đăng nhập nhaccuatui'}
        </span>
        <span className="flex-1" />
        {!loginOpen ? (
          <button className={btn} disabled={open.isPending || downloading}
            title={downloading ? 'Đợi tải xong (hoặc bấm Dừng ở tab ①) rồi mới mở được' : ''}
            onClick={() => { setErr(null); open.mutate(undefined, { onError: (e) => setErr(errText(e)) }); }}>
            {open.isPending ? '⏳ Đang mở…' : '🔑 Mở nhaccuatui để đăng nhập'}
          </button>
        ) : (
          <button className={btn} disabled={close.isPending}
            onClick={() => close.mutate(undefined, { onError: (e) => setErr(errText(e)) })}>
            {close.isPending ? '⏳ Đang đóng…' : '✖ Đóng cửa sổ nhaccuatui'}
          </button>
        )}
      </div>
      {loginOpen && worker?.message && <p className="mt-2 text-xs text-blue-300">{worker.message}</p>}
      {downloading && !external && (
        <p className="mt-2 text-xs text-blue-300">⏳ Tool đang tải nhạc — đợi xong rồi mới mở được cửa sổ đăng nhập.</p>
      )}
      {!external && own.isError && <p className="mt-2 text-xs text-red-400">{errText(own.error)}</p>}
      {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
      <p className="mt-2 text-xs text-gray-500">
        Chỉ cần làm 1 lần: bấm nút → một cửa sổ trình duyệt riêng của tool mở ra → bạn TỰ đăng nhập
        tài khoản nhaccuatui → bấm «Đóng cửa sổ». Tool không bao giờ nhập mật khẩu hộ bạn.
      </p>
    </div>
  );
}
