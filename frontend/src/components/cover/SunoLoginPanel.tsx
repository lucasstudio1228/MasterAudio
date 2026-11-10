import { useState } from 'react';
import { errText, useSunoLogin, useSunoLoginWindow, type SunoWorker } from '../../api/sources';

const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';

/** Nút mở / đóng cửa sổ Suno (profile riêng của tool) để bạn TỰ đăng nhập.
 *  Dùng ở Cài đặt và tab ② Suno Cover (tab ② truyền sẵn `worker` của nó). */
export default function SunoLoginPanel({ worker: external }: { worker?: SunoWorker }) {
  const own = useSunoLogin(!external);
  const worker = external ?? own.data;
  const { open, close } = useSunoLoginWindow();
  const [err, setErr] = useState<string | null>(null);
  const loggedIn = worker?.login?.logged_in;
  const loginOpen = worker?.mode === 'login';
  const running = !!worker?.busy && !loginOpen;
  return (
    <div className="rounded-lg border border-white/5 bg-surface-2/50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={loggedIn ? 'text-green-300' : 'text-orange-300'}>
          {loggedIn === true && '✅ Đã đăng nhập Suno'}
          {loggedIn === false && '⚠️ Chưa đăng nhập Suno — cần để tool tự làm cover'}
          {loggedIn == null && 'ℹ️ Chưa kiểm tra đăng nhập Suno'}
        </span>
        <span className="flex-1" />
        {!loginOpen ? (
          <button className={btn} disabled={open.isPending || running}
            title={running ? 'Tool đang làm trên Suno — đợi xong (hoặc bấm Dừng ở tab ②) rồi mới mở được' : ''}
            onClick={() => { setErr(null); open.mutate(undefined, { onError: (e) => setErr(errText(e)) }); }}>
            {open.isPending ? '⏳ Đang mở…' : '🔑 Mở Suno để đăng nhập'}
          </button>
        ) : (
          <button className={btn} disabled={close.isPending}
            onClick={() => close.mutate(undefined, { onError: (e) => setErr(errText(e)) })}>
            {close.isPending ? '⏳ Đang đóng…' : '✖ Đóng cửa sổ Suno'}
          </button>
        )}
      </div>
      {loginOpen && worker?.message && <p className="mt-2 text-xs text-blue-300">{worker.message}</p>}
      {running && !external && (
        <p className="mt-2 text-xs text-blue-300">⏳ Tool đang làm cover trên Suno — đợi xong rồi mới mở được cửa sổ đăng nhập.</p>
      )}
      {!external && own.isError && <p className="mt-2 text-xs text-red-400">{errText(own.error)}</p>}
      {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
      {(!external || !loggedIn) && !loginOpen && (
        <p className="mt-2 text-xs text-gray-500">
          Chỉ cần làm 1 lần: bấm nút → một cửa sổ trình duyệt riêng của tool mở suno.com → bạn TỰ đăng
          nhập (Google / Discord / email…) → bấm «Đóng cửa sổ». Tool không bao giờ nhập mật khẩu hộ bạn.
        </p>
      )}
    </div>
  );
}
