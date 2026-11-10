import { useState } from 'react';
import { useSources, useSunoSkipLyrics } from '../../api/sources';

const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';

/**
 * Suno hiện bảng cam «lyrics contain copyrighted material» sau Create → popup CHỈ
 * BÁO cho người dùng. Người dùng tự sửa lyrics và tự bấm Create trong cửa sổ Suno;
 * tool chỉ theo dõi để nhận diện + tải 2 bản mới. Tool không điền/sửa lyrics.
 */
export default function RejectedLyricsDialog({ projectId }: { projectId: number }) {
  const { data } = useSources(projectId);
  const skip = useSunoSkipLyrics(projectId);
  const [hiddenKey, setHiddenKey] = useState('');
  const wait = data?.suno_worker?.awaiting_lyrics;
  const key = wait ? `${wait.source_id}:${wait.attempt}:${wait.since}` : '';
  if (!wait || key === hiddenKey) return null;
  const s = (data?.items ?? []).find((x) => x.id === wait.source_id);
  const name = s ? (s.title || s.query) : `#${wait.source_id}`;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-2xl border border-orange-400/70 bg-surface p-5 shadow-2xl">
        <h3 className="text-lg font-bold text-orange-200">⛔ Suno báo lyrics có bản quyền</h3>
        <div className="mt-2 rounded-lg border border-orange-400/40 bg-orange-500/10 p-2 text-sm text-orange-100">
          Bài <b>{name}</b> — Suno: «{wait.why || 'Your lyrics contain copyrighted material.'}»
        </div>
        <p className="mt-3 text-sm text-gray-200">
          Bạn hãy <b>tự sửa lyrics</b> (nội dung tự sáng tác hoặc được phép sử dụng) và
          <b> tự bấm Create</b> ngay trong cửa sổ Suno. Tool đang theo dõi — thấy 2 bản mới là
          tự nhận diện và tải về. Tool không tự điền hay sửa lyrics của bạn.
        </p>
        <p className="mt-2 text-xs text-gray-400">
          Tool chờ tối đa 30 phút. Không muốn làm bài này nữa → «Bỏ qua bài này»: bài được đánh
          dấu «Suno từ chối», giữ nguyên dữ liệu, tool sang bài tiếp theo.
        </p>
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          <button className={btn} disabled={skip.isPending}
            onClick={async () => {
              try { await skip.mutateAsync(wait.source_id); } catch { /* đã thôi chờ */ }
              setHiddenKey(key);
            }}>
            Bỏ qua bài này
          </button>
          <button className="rounded-lg bg-orange-500 px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
            onClick={() => setHiddenKey(key)}>
            Đã hiểu — tôi tự Create trên Suno
          </button>
        </div>
      </div>
    </div>
  );
}
