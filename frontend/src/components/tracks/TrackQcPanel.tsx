import { useEffect, useMemo, useRef, useState } from 'react';
import {
  trackAudioUrl, useAnalyzeTracks, useFlagTrack, usePatchMix,
  useRegenerateTracks, useRemuxVideo, useSetAutoRegen, useTrackQc,
} from '../../api/trackQc';
import type { QcSegment, QcTrack } from '../../types';

/** "12:30", "1:02:05", "12" (phút) → giây. */
function parseTime(s: string): number | null {
  const t = s.trim().replace(',', '.');
  if (!t) return null;
  const parts = t.split(':').map(Number);
  if (parts.some((x) => Number.isNaN(x))) return null;
  if (parts.length === 1) return parts[0] * 60;
  return parts.reduce((acc, x) => acc * 60 + x, 0);
}

function fmt(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = (s % 60).toString().padStart(2, '0');
  return h ? `${h}:${m.toString().padStart(2, '0')}:${r}` : `${m}:${r}`;
}

const STATUS: Record<string, { label: string; cls: string; bar: string }> = {
  error: { label: '❌ Lỗi', cls: 'bg-red-500/15 text-red-300', bar: 'bg-red-500' },
  warning: { label: '⚠️ Nên nghe lại', cls: 'bg-yellow-500/15 text-yellow-300', bar: 'bg-yellow-500' },
  ok: { label: '✅ OK', cls: 'bg-emerald-500/10 text-emerald-300', bar: 'bg-emerald-600/60' },
  replaced: { label: '🔁 Đã thay — chờ vá mix', cls: 'bg-sky-500/15 text-sky-300', bar: 'bg-sky-500' },
  missing: { label: '⛔ Bài đã gỡ', cls: 'bg-red-500/15 text-red-300', bar: 'bg-red-800' },
  unknown: { label: '… Chưa rà soát', cls: 'bg-white/5 text-gray-400', bar: 'bg-gray-600' },
};

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e as Error)?.message;

export default function TrackQcPanel({ projectId }: { projectId: number }) {
  const { data, isLoading } = useTrackQc(projectId);
  const analyze = useAnalyzeTracks(projectId);
  const flag = useFlagTrack(projectId);
  const regen = useRegenerateTracks(projectId);
  const patch = usePatchMix(projectId);
  const remux = useRemuxVideo(projectId);
  const setAuto = useSetAutoRegen(projectId);

  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [maxCreates, setMaxCreates] = useState(2);
  const [confirming, setConfirming] = useState(false);
  const [playing, setPlaying] = useState<{ tid: number; at: number } | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const tracks = data?.tracks ?? [];
  const tl = data?.timeline ?? null;
  const byId = useMemo(() => new Map(tracks.map((t) => [t.track_id, t])), [tracks]);
  const job = data?.job;
  const jobRunning = !!job && (job.status === 'pending' || job.status === 'running');
  const busy = jobRunning || !!data?.video_busy;

  // Bài lỗi (máy đo hoặc tự đánh dấu) mặc định được chọn để tạo lại.
  const errorIds = tracks.filter((t) => t.status === 'error' && t.is_suno).map((t) => t.track_id);
  const errorKey = errorIds.join(',');
  useEffect(() => { setSelected(new Set(errorIds)); }, [errorKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const a = audioRef.current;
    if (!a || !playing) return;
    a.src = trackAudioUrl(projectId, playing.tid);
    const seek = () => { a.currentTime = playing.at; a.play().catch(() => {}); };
    a.addEventListener('loadedmetadata', seek, { once: true });
    a.load();
  }, [playing, projectId]);

  // ── Tra thời điểm trong bản mix ──
  const lookup = useMemo(() => {
    if (!tl) return null;
    const a = parseTime(from);
    if (a === null) return null;
    const b = parseTime(to) ?? a;
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    return tl.segments.filter((s) => s.start <= hi && s.end >= lo);
  }, [tl, from, to]);

  const occurrences = (tid: number | null) =>
    tl && tid !== null ? tl.segments.filter((s) => s.track_id === tid) : [];

  const toggle = (tid: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(tid)) n.delete(tid); else n.add(tid);
      return n;
    });

  const nErr = tracks.filter((t) => t.status === 'error').length;
  const nWarn = tracks.filter((t) => t.status === 'warning').length;
  const shown = showAll ? tracks : tracks.filter((t) => t.status !== 'ok');
  const sel = [...selected].filter((id) => byId.get(id)?.is_suno);
  const freeCount = sel.filter((id) => (byId.get(id)?.free_spares ?? 0) > 0).length;
  const cpc = data?.credits_per_create ?? 20;

  if (isLoading) return <p className="text-sm text-gray-500">Đang tải báo cáo rà soát…</p>;

  const trackBadge = (t?: QcTrack, status?: string) => {
    const st = STATUS[status ?? t?.status ?? 'unknown'] ?? STATUS.unknown;
    return <span className={`rounded px-1.5 py-0.5 text-[11px] ${st.cls}`}>{st.label}</span>;
  };

  const segRow = (s: QcSegment, queryAt: number | null) => {
    const t = s.track_id !== null ? byId.get(s.track_id) : undefined;
    const others = occurrences(s.track_id).filter((o) => o.index !== s.index);
    const inTrack = queryAt !== null ? Math.max(0, queryAt - s.start) : 0;
    return (
      <div key={s.index} className="rounded-lg border border-white/5 bg-surface-2 p-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-gray-300">{fmt(s.start)}–{fmt(s.end)}</span>
          <span className="text-gray-400">đoạn #{s.index} «{s.name}»</span>
          {trackBadge(t, s.status === 'replaced' || s.status === 'missing' ? s.status : undefined)}
        </div>
        <div className="mt-1 text-gray-200">
          🎵 Bài: <b>{s.track_filename ?? s.source_file}</b>
          {t?.request_id && <span className="text-gray-500"> ({t.request_id})</span>}
          {queryAt !== null && <span className="text-gray-400"> · vị trí trong bài ≈ {fmt(inTrack)}</span>}
        </div>
        {s.issues.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-gray-400">
            {s.issues.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        )}
        {others.length > 0 && (
          <div className="mt-1 text-gray-500">
            Bài này còn phát ở: {others.map((o) => `${fmt(o.start)}–${fmt(o.end)}`).join(', ')}
            {' '}— thay bài sẽ sửa luôn các đoạn đó.
          </div>
        )}
        {t && (
          <div className="mt-2 flex flex-wrap gap-2">
            <button className="rounded bg-white/5 px-2 py-1 hover:bg-white/10"
              onClick={() => setPlaying({ tid: t.track_id, at: inTrack })}>
              ▶ Nghe {queryAt !== null ? `từ ${fmt(inTrack)}` : ''}
            </button>
            <button className="rounded bg-white/5 px-2 py-1 hover:bg-white/10"
              disabled={flag.isPending}
              onClick={() => flag.mutate({ track_id: t.track_id, flagged: !t.manual_flag })}>
              {t.manual_flag ? '↩ Bỏ đánh dấu lỗi' : '🚩 Đánh dấu bài này lỗi'}
            </button>
            {t.is_suno && (
              <label className="flex items-center gap-1 rounded bg-white/5 px-2 py-1">
                <input type="checkbox" checked={selected.has(t.track_id)}
                  onChange={() => toggle(t.track_id)} />
                Chọn để tạo lại Suno
              </label>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-gray-300">
          {data?.analyzed_at
            ? <>Kết quả: <b className="text-red-300">{nErr} bài lỗi</b>, <b className="text-yellow-300">{nWarn} bài nên nghe lại</b> / {tracks.length} bài</>
            : 'Chưa rà soát (tự chạy sau mỗi lần mix xong).'}
        </span>
        <button className="rounded-lg bg-white/5 px-3 py-1 text-xs hover:bg-white/10 disabled:opacity-50"
          disabled={analyze.isPending} onClick={() => analyze.mutate()}>
          {analyze.isPending ? 'Đang rà soát…' : '🔎 Rà soát lại'}
        </button>
        {analyze.error && <span className="text-xs text-red-400">{errText(analyze.error)}</span>}
      </div>

      {/* Tự tạo lại Suno ngay khi rà soát sau mix thấy bài lỗi */}
      {data && (
        <div className="space-y-1 rounded-lg border border-violet-500/20 bg-violet-500/5 p-3 text-xs">
          <label className="flex items-center gap-2 text-gray-200">
            <input type="checkbox" checked={data.auto_regen} disabled={setAuto.isPending}
              onChange={(e) => setAuto.mutate({ enabled: e.target.checked })} />
            <b>🤖 Tự tạo lại Suno khi phát hiện bài lỗi</b>
          </label>
          <p className="pl-6 text-gray-400">
            Sau mỗi lần mix xong: rà soát → bài Suno lỗi được thay tự động (bài dự phòng 0 credit
            trước, rồi Create tối đa {data.auto_limits.max_creates_per_track} lượt/bài ≈{' '}
            {data.auto_limits.max_creates_per_track * data.credits_per_create} credit, tối đa{' '}
            {data.auto_limits.max_tracks} bài/lượt) → tự «Vá mix» → mới dựng video bằng bản đã vá.
            Hết credit thì dừng, không tự mua.
          </p>
          {data.auto_fix && (
            <p className={`pl-6 ${data.auto_fix.status === 'failed' || data.auto_fix.status === 'cancelled'
              ? 'text-red-300' : data.auto_fix.status === 'done' ? 'text-emerald-300' : 'text-sky-300'}`}>
              {data.auto_active && data.auto_fix.status !== 'done' ? '⏳ ' : ''}
              Lượt tự động (mix #{data.auto_fix.mix_id}): {data.auto_fix.message}
            </p>
          )}
          {setAuto.error && <p className="pl-6 text-red-400">{errText(setAuto.error)}</p>}
        </div>
      )}

      {/* Tra thời điểm */}
      {tl ? (
        <div className="space-y-2 rounded-lg border border-white/5 p-3">
          <div className="text-xs text-gray-400">
            Nghe bản mix thấy lỗi ở phút nào? Nhập thời điểm (vd <code>12:00</code> đến <code>14:00</code>)
            — bản mix «{tl.mix_title}», dài {fmt(tl.total_seconds)}.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="12:00"
              className="w-24 rounded bg-surface-2 px-2 py-1 font-mono" />
            <span className="text-gray-500">đến</span>
            <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="14:00 (tuỳ chọn)"
              className="w-32 rounded bg-surface-2 px-2 py-1 font-mono" />
          </div>
          {/* Dòng thời gian — bấm 1 đoạn để tra */}
          <div className="flex h-5 w-full overflow-hidden rounded">
            {tl.segments.map((s) => (
              <button key={s.index}
                title={`${fmt(s.start)}–${fmt(s.end)} · ${s.track_filename ?? s.source_file} · ${STATUS[s.status]?.label ?? ''}`}
                style={{ width: `${((s.end - s.start) / tl.total_seconds) * 100}%` }}
                className={`${STATUS[s.status]?.bar ?? 'bg-gray-600'} border-r border-black/40 hover:opacity-70`}
                onClick={() => { setFrom(fmt(s.start + 1)); setTo(''); }} />
            ))}
          </div>
          <div className="flex justify-between text-[10px] text-gray-500">
            <span>0:00</span><span>{fmt(tl.total_seconds / 2)}</span><span>{fmt(tl.total_seconds)}</span>
          </div>
          {lookup && (lookup.length
            ? <div className="space-y-2">{lookup.map((s) => segRow(s, parseTime(from)))}</div>
            : <p className="text-xs text-gray-500">Không có đoạn nào ở thời điểm này.</p>)}
          {lookup && lookup.length > 1 && (
            <p className="text-[11px] text-gray-500">
              Khoảng {tl.crossfade_seconds}s ở ranh giới 2 bài là crossfade — nghe cả hai để chắc bài nào lỗi.
            </p>
          )}
        </div>
      ) : (
        <p className="text-xs text-gray-500">Chưa có bản mix hoàn tất — tra thời điểm sẽ có sau khi mix.</p>
      )}

      {/* Danh sách bài lẻ */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-gray-500">Bài lẻ</span>
          <label className="flex items-center gap-1 text-xs text-gray-400">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            Hiện cả bài OK
          </label>
        </div>
        {shown.length === 0 && <p className="text-xs text-gray-500">Không có bài nào bị lỗi. 🎉</p>}
        {shown.map((t) => {
          const occ = occurrences(t.track_id);
          return (
            <div key={t.track_id} className="rounded-lg bg-surface-2 p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                {t.is_suno && (
                  <input type="checkbox" checked={selected.has(t.track_id)}
                    onChange={() => toggle(t.track_id)} title="Chọn để tạo lại Suno" />
                )}
                {trackBadge(t)}
                {t.manual_flag && <span className="text-red-300">🚩 bạn đánh dấu</span>}
                <span className="flex-1 truncate text-gray-200" title={t.filename}>{t.filename}</span>
                <span className="text-gray-500">{fmt(t.duration)}</span>
                <button className="rounded bg-white/5 px-2 py-0.5 hover:bg-white/10"
                  onClick={() => setPlaying({ tid: t.track_id, at: 0 })}>▶</button>
                <button className="rounded bg-white/5 px-2 py-0.5 hover:bg-white/10"
                  onClick={() => flag.mutate({ track_id: t.track_id, flagged: !t.manual_flag })}>
                  {t.manual_flag ? '↩' : '🚩'}
                </button>
              </div>
              {t.issues.length > 0 && (
                <ul className="mt-1 list-disc pl-6 text-gray-400">
                  {t.issues.map((i, k) => <li key={k}>{i.message}</li>)}
                </ul>
              )}
              {occ.length > 0 && (
                <div className="mt-1 pl-6 text-gray-500">
                  Trong mix: {occ.map((o) => `${fmt(o.start)}–${fmt(o.end)}`).join(', ')}
                </div>
              )}
              {!t.is_suno && t.status === 'error' && (
                <div className="mt-1 pl-6 text-gray-500">Bài không do Suno tạo — xoá/đổi file thủ công.</div>
              )}
            </div>
          );
        })}
      </div>

      <audio ref={audioRef} controls className={playing ? 'w-full' : 'hidden'} />

      {/* Tạo lại Suno */}
      <div className="space-y-2 rounded-lg border border-white/5 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
            disabled={!sel.length || busy || regen.isPending}
            onClick={() => setConfirming(true)}>
            🎼 Tạo lại Suno ({sel.length} bài)
          </button>
          <label className="text-xs text-gray-400">
            Tối đa
            <select value={maxCreates} onChange={(e) => setMaxCreates(Number(e.target.value))}
              className="mx-1 rounded bg-surface-2 px-1">
              {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            lượt Create/bài
          </label>
          {busy && !jobRunning && <span className="text-xs text-yellow-300">Đang có tác vụ Video/Suno chạy — đợi xong.</span>}
        </div>
        {confirming && (
          <div className="rounded bg-yellow-500/10 p-2 text-xs text-yellow-200">
            Thay {sel.length} bài. {freeCount > 0 && `${freeCount} bài có bản dự phòng cùng prompt → thử trước, 0 credit. `}
            Nếu không đạt sẽ Create lại bằng đúng prompt của bài đó: tối đa {sel.length * maxCreates} lượt
            ≈ {sel.length * maxCreates * cpc} credit Suno (thường ít hơn). Không tự mua credit.
            Đừng đóng cửa sổ Cốc Cốc Suno trong lúc chạy.
            <div className="mt-2 flex gap-2">
              <button className="rounded bg-accent px-3 py-1 font-semibold text-white"
                onClick={() => {
                  setConfirming(false);
                  regen.mutate({ track_ids: sel, max_creates_per_track: maxCreates });
                }}>Xác nhận</button>
              <button className="rounded bg-white/10 px-3 py-1" onClick={() => setConfirming(false)}>Huỷ</button>
            </div>
          </div>
        )}
        {regen.error && <p className="text-xs text-red-400">{errText(regen.error)}</p>}
        {job && (
          <div className="text-xs">
            <div className="mb-1 flex justify-between text-gray-400">
              <span>{job.kind === 'suno-fix' ? 'Tạo lại Suno' : 'Thay nhạc trong video'} · {job.status}</span>
              <span>{Math.round(job.percent)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-white/5">
              <div className="h-full bg-accent transition-all" style={{ width: `${job.percent}%` }} />
            </div>
            <p className="mt-1 text-gray-300">{job.message}</p>
            {job.error && <p className="mt-1 whitespace-pre-wrap text-red-400">{job.error.split('\n')[0]}</p>}
          </div>
        )}
      </div>

      {/* Vá mix sau khi thay bài */}
      {tl && tl.outdated_segments > 0 && (
        <div className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3 text-xs text-sky-200">
          Bản mix «{tl.mix_title}» còn {tl.outdated_segments} đoạn dùng bài đã thay.
          «Vá mix» render lại <b>đúng thứ tự & tên đoạn cũ</b>, chỉ thay các đoạn đó (phần trước giữ y hệt,
          phần sau lệch vài giây nếu bài mới dài/ngắn hơn), độ dài không vượt bản cũ; video
          final.mp4 (nếu có) được thay nhạc, không dựng lại hình.
          <div className="flex gap-2">
            <button className="rounded bg-sky-600 px-3 py-1 font-semibold text-white disabled:opacity-40"
              disabled={patch.isPending || !!data?.mix_busy || busy}
              onClick={() => patch.mutate()}>
              {data?.mix_busy ? 'Đang render mix…' : '🩹 Vá mix'}
            </button>
          </div>
          {patch.error && <p className="text-red-400">{errText(patch.error)}</p>}
        </div>
      )}
      {tl?.mix_title.startsWith('Vá mix') && tl.outdated_segments === 0 && !data?.mix_busy && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
          Bản mix đang dùng là bản đã vá.
          <button className="rounded bg-white/5 px-2 py-1 hover:bg-white/10 disabled:opacity-40"
            disabled={busy || remux.isPending} onClick={() => remux.mutate()}>
            🎬 Thay nhạc trong final.mp4
          </button>
          <span>(tự chạy sau khi vá; bấm lại nếu lúc đó worker video bận). Bản nháp YouTube cũ vẫn là nhạc cũ.</span>
          {remux.error && <span className="text-red-400">{errText(remux.error)}</span>}
        </div>
      )}
    </div>
  );
}
