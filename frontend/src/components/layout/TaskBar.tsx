import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAutoRunToEnd, useGlobalTaskControl, useGlobalTasks, type GlobalTask } from '../../api/tasks';

const ICON: Record<string, string> = {
  suno: '🎼', 'suno-login': '🔑', 'suno-fix': '🩹', remux: '🎬', upload: '📤', upload_short: '📱', continue: '⏩',
  full: '🎞️', images: '🖼️', clips: '🎥', assemble: '🧩', mix: '🎚️', stem: '🪄',
  batch: '🏭',
  'auto-run': '🚀',
  'cover-suno': '🎤',
};

const STATUS: Record<string, { text: string; cls: string }> = {
  running: { text: 'Đang chạy', cls: 'bg-accent/25 text-accent' },
  pending: { text: 'Xếp hàng', cls: 'bg-sky-500/20 text-sky-300' },
  paused: { text: 'Tạm dừng', cls: 'bg-amber-500/20 text-amber-300' },
  completed: { text: 'Xong', cls: 'bg-green-500/20 text-green-300' },
  failed: { text: 'Lỗi', cls: 'bg-red-500/20 text-red-300' },
  cancelled: { text: 'Đã huỷ', cls: 'bg-white/10 text-gray-400' },
};

function ago(s?: number) {
  if (s == null) return '';
  return s < 60 ? 'vừa xong' : `${Math.round(s / 60)} phút trước`;
}

/** Thanh tác vụ luôn nằm dưới đáy — hiện việc đang chạy dù xem project nào.
 * Bấm vào 1 tác vụ → mở đúng project + tab. */
export default function TaskBar() {
  const { data, isError, error } = useGlobalTasks();
  const outdated = (error as { response?: { status?: number } } | null)?.response?.status === 404;
  const navigate = useNavigate();
  const location = useLocation();
  const [expanded, setExpanded] = useState(false);
  const pauseAll = useGlobalTaskControl('pause-all');
  const stopAll = useGlobalTaskControl('stop-all');
  const resumeAll = useGlobalTaskControl('resume-all');
  const autoRun = useAutoRunToEnd();

  const tasks = data?.tasks ?? [];
  const recent = data?.recent ?? [];
  const paused = tasks.some((t) => t.status === 'paused');
  const running = tasks.some((t) => t.status === 'running' || t.status === 'pending');
  const canResume = paused || !!data?.can_resume;
  const controlling = pauseAll.isPending || stopAll.isPending || resumeAll.isPending || autoRun.isPending;
  const routeProjectId = Number(location.pathname.match(/\/projects\/(\d+)/)?.[1] || 0) || undefined;
  const targetProjectId = routeProjectId ?? tasks.find((t) => t.project_id)?.project_id
    ?? recent.find((t) => t.project_id)?.project_id ?? undefined;
  const open = (t: GlobalTask) => {
    if (t.project_id != null) navigate(`/projects/${t.project_id}?tab=${t.tab}`);
  };

  const row = (t: GlobalTask, faded = false) => {
    const st = STATUS[t.status] ?? STATUS.running;
    const pct = t.percent;
    const text = t.status === 'failed' && t.error ? t.error : t.message;
    return (
      <button
        key={t.id}
        onClick={() => open(t)}
        title={`${t.label}\n#${t.project_id ?? '-'} ${t.project_name}\n${text}`}
        className={`flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-white/5 ${
          faded ? 'opacity-70' : ''
        }`}
      >
        <span className="shrink-0">{ICON[t.kind] ?? '⚙️'}</span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${st.cls}`}>
          {st.text}
        </span>
        <span className="shrink-0 font-medium text-gray-200">{t.label}</span>
        {t.project_id != null && (
          <span className="max-w-[16rem] shrink-0 truncate text-gray-400">
            · #{t.project_id} {t.project_name}
          </span>
        )}
        {pct != null && !['completed', 'cancelled', 'pending'].includes(t.status) && (
          <span className="flex shrink-0 items-center gap-1.5">
            <span className="h-1.5 w-24 overflow-hidden rounded bg-white/10">
              <span
                className={`block h-full ${t.status === 'failed' ? 'bg-red-500' : 'bg-accent'}`}
                style={{ width: `${Math.min(pct, 100)}%` }}
              />
            </span>
            <span className="tabular-nums text-gray-300">{Math.round(pct)}%</span>
          </span>
        )}
        {t.detail && <span className="shrink-0 text-gray-400">· {t.detail}</span>}
        <span className={`min-w-0 flex-1 truncate ${t.status === 'failed' ? 'text-red-300' : 'text-gray-500'}`}>
          {text}
        </span>
        {t.ago_seconds != null && (
          <span className="shrink-0 text-[10px] text-gray-600">{ago(t.ago_seconds)}</span>
        )}
      </button>
    );
  };

  const hasMore = tasks.length > 1 || recent.length > 0;

  return (
    <footer className="shrink-0 border-t border-white/10 bg-surface px-3 py-1.5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 space-y-0.5">
          {isError ? (
            <p className="px-2 py-1 text-xs text-red-300">
              {outdated
                ? '⚠️ Backend đang chạy bản cũ — khởi động lại backend (khi không có tác vụ nào chạy) để hiện thanh tác vụ.'
                : '⚠️ Không kết nối được backend — kiểm tra cửa sổ start.bat.'}
            </p>
          ) : tasks.length === 0 ? (
            expanded && recent.length ? null : (
              <p className="px-2 py-1 text-xs text-gray-500">
                💤 Không có tác vụ nào đang chạy
                {recent[0] && (
                  <>
                    {' · gần nhất: '}
                    <button className="text-gray-400 underline-offset-2 hover:underline"
                            onClick={() => open(recent[0])}>
                      {recent[0].label} #{recent[0].project_id} —{' '}
                      {STATUS[recent[0].status]?.text} ({ago(recent[0].ago_seconds)})
                    </button>
                  </>
                )}
              </p>
            )
          ) : (
            (expanded ? tasks : tasks.slice(0, 1)).map((t) => row(t))
          )}
          {expanded && recent.map((t) => row(t, true))}
        </div>
        {hasMore && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 rounded-md px-2 py-1 text-[11px] text-gray-400 hover:bg-white/5 hover:text-white"
            title="Xem tất cả tác vụ đang chạy / xếp hàng / vừa xong"
          >
            {expanded ? '▾ Thu gọn' : `▴ ${tasks.length > 1 ? `+${tasks.length - 1} tác vụ` : 'Vừa xong'}`}
          </button>
        )}
        {(tasks.length > 0 || canResume) && (
          <div className="flex shrink-0 items-center gap-1.5">
            {running && (
              <button disabled={controlling}
                onClick={() => pauseAll.mutate()}
                className="rounded-md border border-amber-500/40 bg-amber-950/40 px-2.5 py-1 text-[11px] font-medium text-amber-200 hover:bg-amber-900/60 disabled:opacity-40">
                ⏸ Tạm dừng
              </button>
            )}
            {canResume && (
              <button disabled={controlling}
                onClick={() => resumeAll.mutate()}
                className="rounded-md border border-emerald-500/40 bg-emerald-950/40 px-2.5 py-1 text-[11px] font-medium text-emerald-200 hover:bg-emerald-900/60 disabled:opacity-40">
                ▶ Tiếp tục
              </button>
            )}
            {tasks.length > 0 && (
              <button disabled={controlling}
                onClick={() => {
                  if (confirm('Dừng lại TẤT CẢ quy trình đang thực hiện? Phần đã làm được giữ lại để có thể Tiếp tục.'))
                    stopAll.mutate();
                }}
                className="rounded-md border border-red-500/40 bg-red-950/40 px-2.5 py-1 text-[11px] font-medium text-red-200 hover:bg-red-900/60 disabled:opacity-40">
                ⏹ Dừng lại
              </button>
            )}
          </div>
        )}
      </div>
      <div className="mt-1 flex items-center justify-end border-t border-white/5 pt-1">
        <button
          disabled={controlling || targetProjectId == null}
          onClick={() => autoRun.mutate(targetProjectId)}
          title="Giữ phần đã hoàn thành và tự nối từ checkpoint hiện tại đến final + nháp YouTube"
          className="rounded-md border border-violet-400/40 bg-violet-950/50 px-3 py-1 text-[11px] font-semibold text-violet-100 hover:bg-violet-900/70 disabled:opacity-40"
        >
          {autoRun.isPending ? '⏳ Đang kích hoạt…' : '🚀 Tự động chạy đến cuối'}
        </button>
      </div>
    </footer>
  );
}
