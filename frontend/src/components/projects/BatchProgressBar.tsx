import {
  useBatchStatus, useCancelBatch, useResumeBatch, useSkipMissingLyrics,
} from '../../api/projects';
import { Link } from 'react-router-dom';

interface Props {
  batchKey: string;
  /** Người dùng đóng thanh tiến độ (chỉ ẩn khỏi UI, không dừng lô). */
  onDismiss: () => void;
}

const STATUS_STYLE: Record<string, string> = {
  running: 'border-accent/40 bg-accent/10 text-gray-200',
  completed: 'border-green-500/30 bg-green-500/10 text-green-200',
  cancelled: 'border-white/15 bg-white/5 text-gray-300',
  blocked: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
  failed: 'border-red-500/30 bg-red-500/10 text-red-200',
  interrupted: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
};

const STATUS_LABEL: Record<string, string> = {
  running: '⏳ Đang chạy',
  completed: '✅ Hoàn tất',
  cancelled: '⛔ Đã huỷ',
  blocked: '⚠️ Đã dừng — cần xử lý',
  failed: '❌ Lỗi',
  interrupted: '⏸ Bị ngắt (server khởi động lại)',
};

export default function BatchProgressBar({ batchKey, onDismiss }: Props) {
  const { data: run } = useBatchStatus(batchKey);
  const cancelBatch = useCancelBatch();
  const resumeBatch = useResumeBatch();
  const skipMissing = useSkipMissingLyrics();

  if (!run) return null;

  const running = run.status === 'running';
  const missingLyrics = run.missing_lyrics ?? [];
  // Tiếp tục được khi lô dừng/bị ngắt hoặc xong nhưng còn sản phẩm lỗi.
  const canResume = !running && missingLyrics.length === 0 &&
    (run.status !== 'completed' || run.failed > 0);
  const done = run.completed + run.failed;
  const percent = run.total > 0 ? Math.round((done / run.total) * 100) : 0;
  const current = run.projects.find((p) => p.project_id === run.current_project_id);
  const isSingles = run.params?.kind === 'cover_singles';
  // Mix Set & Track lẻ dùng chung luồng cover (thiếu lyrics, tiếp tục…).
  const isMixSet = run.params?.kind === 'cover_mixset' || isSingles;
  const coverLabel = isSingles ? 'Track lẻ' : 'Mix Set';
  const mixSetProjectId = missingLyrics[0]?.project_id ?? run.projects[0]?.project_id;

  return (
    <div
      className={`mb-4 rounded-xl border px-4 py-3 ${
        STATUS_STYLE[run.status] ?? STATUS_STYLE.running
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-medium">
          {isSingles ? '🎵 Track lẻ' : isMixSet ? '🎼 Mix Set' : '🏭 Lô sản xuất'} — {STATUS_LABEL[run.status] ?? run.status}
          {(!isMixSet || isSingles) && <> · {isSingles ? 'bài' : 'sản phẩm'} {Math.min(run.current_index + 1, run.total)}/{run.total}</>}
          {run.failed > 0 && ` · ${run.failed} lỗi`}
        </div>
        <div className="flex items-center gap-2">
          {running && (
            <button
              onClick={() => cancelBatch.mutate(batchKey)}
              disabled={cancelBatch.isPending}
              className="rounded-lg border border-red-500/30 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-950/30 disabled:opacity-40"
            >
              {cancelBatch.isPending ? 'Đang huỷ…' : `⛔ Huỷ ${isMixSet ? coverLabel : 'lô'}`}
            </button>
          )}
          {!running && isMixSet && missingLyrics.length > 0 && mixSetProjectId && (
            <Link
              to={`/projects/${mixSetProjectId}?tab=source&requiredLyrics=1`}
              className="rounded-lg border border-amber-400/40 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
            >
              📝 Bổ sung lyrics thủ công
            </Link>
          )}
          {!running && isMixSet && missingLyrics.length > 0 && (
            <button
              onClick={() => skipMissing.mutate(batchKey)}
              disabled={skipMissing.isPending}
              title="Đánh dấu bỏ qua các bài chưa có lời và tiếp tục bằng những bài đã có lyrics"
              className="rounded-lg border border-orange-400/40 bg-orange-500/10 px-2.5 py-1 text-xs font-medium text-orange-200 hover:bg-orange-500/20 disabled:opacity-40"
            >
              {skipMissing.isPending
                ? 'Đang bỏ qua…'
                : `⏭ Bỏ qua ${missingLyrics.length} bài & tiếp tục`}
            </button>
          )}
          {canResume && (
            <button
              onClick={() => resumeBatch.mutate(batchKey)}
              disabled={resumeBatch.isPending}
              title="Nối lại đúng bước còn thiếu, không chạy lại bài cover đã hoàn tất"
              className="rounded-lg border border-emerald-500/30 px-2.5 py-1 text-xs font-medium text-emerald-300 hover:bg-emerald-950/30 disabled:opacity-40"
            >
              {resumeBatch.isPending ? 'Đang tiếp tục…' : `▶ Tiếp tục ${isMixSet ? coverLabel : 'lô'}`}
            </button>
          )}
          {!running && (
            <button
              onClick={onDismiss}
              className="rounded-lg border border-white/15 px-2.5 py-1 text-xs text-gray-300 hover:bg-white/5"
            >
              Ẩn
            </button>
          )}
        </div>
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-black/30">
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>

      <p className="mt-2 text-xs opacity-80">{run.message}</p>
      {isMixSet && missingLyrics.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-400/30 bg-black/15 p-2 text-xs">
          <p className="font-semibold text-amber-100">
            {isSingles
              ? <>Thiếu lyrics {missingLyrics.length} bài — các bài này đang chờ, chưa chạy Suno:</>
              : <>Thiếu lyrics {missingLyrics.length} bài — Mix Set đã dừng, chưa chạy Suno:</>}
          </p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-amber-100/80">
            {missingLyrics.slice(0, 10).map((song) => (
              <li key={song.source_id}>{song.title || song.query}{song.artist ? ` — ${song.artist}` : ''}</li>
            ))}
          </ol>
          {missingLyrics.length > 10 && <p>…và {missingLyrics.length - 10} bài khác.</p>}
          <p className="mt-1">Lưu lời cho đủ tất cả bài; khi đó nút <b>▶ Tiếp tục {coverLabel}</b> mới xuất hiện.</p>
          <p className="mt-1 text-orange-200">
            Nếu 5 phút không có bài nào được bổ sung lyrics, tool sẽ tự bấm “Bỏ qua”
            và tiếp tục với các bài đã có lời. Mỗi lần lưu thêm lyrics, 5 phút được tính lại.
          </p>
        </div>
      )}
      {skipMissing.isError && (
        <p className="mt-1 text-xs text-red-300">
          Không thể bỏ qua:{' '}
          {(skipMissing.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            ?? (skipMissing.error as Error).message}
        </p>
      )}
      {resumeBatch.isError && (
        <p className="mt-1 text-xs text-red-300">
          Không tiếp tục được:{' '}
          {(resumeBatch.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            ?? (resumeBatch.error as Error).message}
        </p>
      )}
      {current && (
        <p className="mt-0.5 text-xs opacity-70">
          Đang làm: «{current.name}»
          {current.suno_phase ? ` — Suno: ${current.suno_phase}` : ''}
        </p>
      )}
      {run.status === 'blocked' && missingLyrics.length === 0 && (
        <p className="mt-1 text-xs">
          Xử lý bước đang kẹt (vd đăng nhập lại Suno / nạp credit) rồi bấm
          <b> ▶ Tiếp tục {isMixSet ? coverLabel : 'lô'}</b> — phần đã xong được bỏ qua,
          phần dở nối lại đúng chỗ dừng.
        </p>
      )}
    </div>
  );
}
