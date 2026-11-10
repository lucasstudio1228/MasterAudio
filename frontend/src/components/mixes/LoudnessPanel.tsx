import { useState } from 'react';
import { useLoudness, useSetMixGain } from '../../api/mixes';
import type { LoudnessSong } from '../../types';

interface Props {
  projectId: number;
  balance: boolean;
  onBalanceChange: (v: boolean) => void;
}

const fmt = (v: number | null | undefined, digits = 1) =>
  v == null ? '—' : v.toFixed(digits);
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`;

/** Bước «Cân bằng âm lượng» của tab ④ Mix: đo độ to từng bài (EBU R128) và
 *  cho chỉnh tay ±dB từng bài. Bản Mix kế tiếp tự áp dụng khi bật cân bằng. */
export default function LoudnessPanel({ projectId, balance, onBalanceChange }: Props) {
  const report = useLoudness(projectId);
  const setGain = useSetMixGain(projectId);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const data = report.data;
  const error = (report.error as any)?.response?.data?.detail ?? (report.error ? 'Không đo được.' : '');

  function saveGain(song: LoudnessSong) {
    const raw = draft[song.track_id];
    if (raw === undefined) return;
    const value = Math.max(-12, Math.min(12, Number(raw)));
    setDraft((d) => {
      const { [song.track_id]: _, ...rest } = d;
      return rest;
    });
    if (!Number.isFinite(value) || value === song.manual_gain_db) return;
    setGain.mutate(
      { trackId: song.track_id, gainDb: value },
      { onSuccess: () => void report.refetch() },
    );
  }

  const spreadCls = (v: number | null) =>
    v == null ? 'text-gray-400' : v <= 1 ? 'text-green-300' : v <= 2 ? 'text-amber-300' : 'text-red-300';

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Đo độ to từng bài theo chuẩn <b>EBU R128</b>: độ to trung bình, <b>độ to đoạn to</b>{' '}
        (điệp khúc — thứ tai nghe thấy khi chuyển bài) và dải động. Khi Mix, tool đưa đoạn to
        của mọi bài về cùng một mức, rồi chặn đỉnh ở −1 dBFS bằng limiter (không hạ cả bài).
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-200">
          <input type="checkbox" checked={balance}
            onChange={(e) => onBalanceChange(e.target.checked)} />
          Cân bằng âm lượng khi Mix (khuyên dùng)
        </label>
        <button
          type="button"
          onClick={() => void report.refetch()}
          disabled={report.isFetching}
          className="rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-50"
        >
          {report.isFetching ? '⏳ Đang đo… (~0.5 giây/bài)' : data ? '🔄 Đo lại' : '🔍 Phân tích âm lượng'}
        </button>
      </div>
      {!balance && (
        <p className="text-xs text-amber-300/80">
          ⚠️ Đang tắt: Mix chỉ chuẩn độ to trung bình như cách cũ, các bài có thể to nhỏ không đều.
        </p>
      )}
      {error && <p className="text-xs text-red-300">{error}</p>}

      {data && (
        <>
          <p className="text-sm text-gray-300">
            Chênh lệch đoạn to giữa bài to nhất và nhỏ nhất:{' '}
            <b className={spreadCls(data.spread_before)}>{fmt(data.spread_before)} LU</b>
            {' → '}sau cân bằng{' '}
            <b className={spreadCls(balance ? data.spread_after : data.spread_before)}>
              {fmt(balance ? data.spread_after : data.spread_before)} LU
            </b>
            <span className="text-xs text-gray-500">
              {' '}(dưới 1 LU tai gần như không phân biệt được · mục tiêu Mix {data.target_lufs} LUFS)
            </span>
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-gray-500">
                <tr>
                  <th className="py-1 pr-2">#</th>
                  <th className="py-1 pr-2">Bài</th>
                  <th className="py-1 pr-2" title="Độ to trung bình cả bài">Trung bình</th>
                  <th className="py-1 pr-2" title="Độ to đoạn to (điệp khúc)">Đoạn to</th>
                  <th className="py-1 pr-2" title="Chênh lệch đoạn nhỏ ↔ đoạn to trong bài">Dải động</th>
                  <th className="py-1 pr-2">Đỉnh</th>
                  <th className="py-1 pr-2" title="Đoạn to lệch so với trung vị các bài">Lệch</th>
                  <th className="py-1 pr-2" title="Tool tự tăng/giảm khi Mix">Tự động</th>
                  <th className="py-1 pr-2" title="Bạn chỉnh thêm theo tai (−12…+12 dB)">Chỉnh tay</th>
                  <th className="py-1 pr-2" title="Đoạn to sau khi Mix">Sau Mix</th>
                </tr>
              </thead>
              <tbody className="text-gray-200">
                {data.songs.map((s, i) => (
                  <tr key={s.track_id} className={`border-t border-white/5 ${s.flag ? 'bg-amber-500/10' : ''}`}>
                    <td className="py-1 pr-2 text-gray-500">{i + 1}</td>
                    <td className="max-w-[220px] truncate py-1 pr-2" title={s.audio_file}>
                      {s.flag && '⚠️ '}{s.title}
                      {s.mastered && <span className="ml-1 text-[10px] text-green-300/80">master</span>}
                      {s.before.error && <span className="ml-1 text-red-300">{s.before.error}</span>}
                    </td>
                    <td className="py-1 pr-2">{fmt(s.before.integrated)}</td>
                    <td className="py-1 pr-2">{fmt(s.before.loud)}</td>
                    <td className="py-1 pr-2">{fmt(s.before.lra)}</td>
                    <td className="py-1 pr-2">{fmt(s.before.peak_db)}</td>
                    <td className={`py-1 pr-2 ${s.flag ? 'text-amber-300' : ''}`}>{signed(s.deviation_lu)}</td>
                    <td className="py-1 pr-2">{balance ? signed(s.auto_gain_db) : '—'}</td>
                    <td className="py-1 pr-2">
                      <input
                        type="number"
                        step={0.5}
                        min={-12}
                        max={12}
                        disabled={!s.song_id || setGain.isPending}
                        title={s.song_id ? 'dB — Enter hoặc rời ô để lưu' : 'Bài chưa có trong danh sách cover'}
                        className="w-16 rounded border border-white/10 bg-background px-1 py-0.5 text-xs text-white outline-none focus:border-accent disabled:opacity-40"
                        value={draft[s.track_id] ?? String(s.manual_gain_db)}
                        onChange={(e) => setDraft((d) => ({ ...d, [s.track_id]: e.target.value }))}
                        onBlur={() => saveGain(s)}
                        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                      />
                    </td>
                    <td className="py-1 pr-2">
                      {balance ? fmt(s.after_loud) : fmt(s.before.loud)}
                      {balance && s.heavy_limit && (
                        <span className="ml-1 text-amber-300" title={`Limiter phải nén ~${s.limit_db.toFixed(1)} dB ở đỉnh`}>
                          🗜️
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-gray-500">
            Đơn vị LUFS / LU / dBFS. ⚠️ = bài lệch quá {data.flag_lu} LU so với các bài khác.
            🗜️ = bài phải tăng nhiều nên limiter nén đỉnh hơn 3 dB — nếu nghe bị bẹt, giảm «Chỉnh tay»
            của bài đó 1–2 dB. Chỉnh tay áp dụng cho lần Mix kế tiếp.
          </p>
        </>
      )}
    </div>
  );
}
