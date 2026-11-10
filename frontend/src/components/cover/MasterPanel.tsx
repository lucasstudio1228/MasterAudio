import { useState } from 'react';
import {
  errText,
  masterAudioUrl,
  useMasterCover,
  useMasterProject,
  useMasterSettings,
  useRecheckFfmpeg,
  useSaveMasterSettings,
  type MasterSettings,
  type SourceCover,
} from '../../api/sources';

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btnSm = 'rounded-md bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';
const select = 'rounded-md border border-white/10 bg-surface-2 px-2 py-1 text-sm text-gray-200 disabled:opacity-40';

const FORMATS: { value: MasterSettings['output_format']; label: string }[] = [
  { value: 'wav', label: 'WAV 24-bit 48 kHz (nên chọn)' },
  { value: 'mp3', label: 'MP3 320 kbps' },
  { value: 'both', label: 'Cả WAV và MP3' },
];

const lufs = (v?: number | null) => (v == null ? '?' : v.toFixed(1));

// ---------------------------------------------------------------- dòng master của 1 bản cover
export function CoverMasterLine({ projectId, cover }: { projectId: number; cover: SourceCover }) {
  const run = useMasterCover(projectId);
  const [err, setErr] = useState<string | null>(null);
  const m = cover.master;
  if (!m) return null;                          // server cũ chưa có chức năng master
  const busy = m.state === 'queued' || m.state === 'running' || run.isPending;
  const go = (force: boolean) => {
    setErr(null);
    run.mutate({ trackId: cover.track_id, force }, { onError: (e) => setErr(errText(e)) });
  };

  return (
    <div className="mt-1 rounded border border-white/5 bg-black/10 px-2 py-1">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {m.state === 'queued' && (
          <span className="rounded bg-violet-600/25 px-2 py-0.5 text-violet-200">⏳ Chờ master…</span>
        )}
        {m.state === 'running' && (
          <span className="rounded bg-violet-600/40 px-2 py-0.5 text-violet-100 animate-pulse">🎚 Đang master…</span>
        )}
        {m.state === 'done' && (
          <span className="rounded bg-violet-600/30 px-2 py-0.5 text-violet-100"
            title={`File: ${m.file}${m.at ? ` · lúc ${m.at}` : ''}${m.seconds ? ` · mất ${m.seconds}s` : ''}`}>
            🎚 Đã master{m.preset && ` · ${m.preset}`}
          </span>
        )}
        {m.state === 'failed' && (
          <span className="rounded bg-red-600/30 px-2 py-0.5 text-red-200">⚠ Master lỗi</span>
        )}
        {m.state === 'none' && <span className="text-gray-500">Chưa master</span>}
        {m.state === 'done' && m.before && m.after && (
          <span className="text-gray-400" title="Độ to (LUFS) và đỉnh (True Peak) trước → sau khi master">
            độ to {lufs(m.before.lufs)} → <b className="text-gray-200">{lufs(m.after.lufs)} LUFS</b>
            {' · '}đỉnh {lufs(m.after.tp)} dBTP
          </span>
        )}
        <span className="flex-1" />
        {!busy && (
          <button className={btnSm} disabled={run.isPending}
            title={m.state === 'done'
              ? 'Master lại theo preset đang chọn — bản master cũ được thay khi bản mới xong'
              : 'Master bản cover này bằng ffmpeg (file gốc giữ nguyên)'}
            onClick={() => go(m.state === 'done')}>
            {m.state === 'done' ? '↻ Master lại' : '🎚 Master'}
          </button>
        )}
      </div>
      {m.state === 'failed' && m.error && (
        <p className="mt-0.5 break-words text-xs text-red-300">{m.error}</p>
      )}
      {err && <p className="mt-0.5 text-xs text-red-400">{err}</p>}
      {m.file && (
        <audio controls preload="none" src={masterAudioUrl(cover.track_id, m.at)} className="mt-1 h-8 w-full"
          title="Nghe bản master" />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- khung cài đặt master
export default function MasterPanel({ projectId, covers }: { projectId: number; covers: SourceCover[] }) {
  const { data, error, isLoading } = useMasterSettings();
  const save = useSaveMasterSettings();
  const recheck = useRecheckFfmpeg();
  const runAll = useMasterProject(projectId);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const onErr = (e: unknown) => setErr(errText(e));

  const withMaster = covers.filter((c) => c.master);
  const done = withMaster.filter((c) => c.master?.state === 'done').length;
  const working = withMaster.filter((c) => c.master?.state === 'queued' || c.master?.state === 'running').length;
  const todo = withMaster.filter((c) => c.master?.state === 'none' || c.master?.state === 'failed').length;

  const set = (v: Parameters<typeof save.mutate>[0]) => {
    setErr(null);
    save.mutate(v, { onError: onErr });
  };
  const masterAll = (force: boolean) => {
    if (force && !window.confirm(
      `Master lại TẤT CẢ ${withMaster.length} bản cover theo preset «${data?.preset}»?\n`
      + 'Bản master cũ được thay bằng bản mới. File gốc vẫn giữ nguyên.',
    )) return;
    setErr(null);
    setNote(null);
    runAll.mutate(force, {
      onError: onErr,
      onSuccess: (r) => setNote(r.queued ? `Đã xếp ${r.queued} bản vào hàng master.` : 'Không có bản nào cần master.'),
    });
  };

  if (isLoading) return null;
  return (
    <div className={card}>
      <h2 className={h2}>🎚 Master (làm đẹp + chuẩn độ to)</h2>
      {error || !data ? (
        <p className="text-sm text-orange-300">{errText(error)}</p>
      ) : (
        <div className="space-y-3 text-sm">
          {!data.ffmpeg.ok ? (
            <div className="rounded-lg bg-orange-600/20 p-2 text-orange-200">
              <p>⚠ Chưa tìm thấy ffmpeg nên tool chưa master được (bản cover vẫn dùng bình thường).</p>
              {data.ffmpeg.error && <p className="mt-1 text-xs opacity-80">{data.ffmpeg.error}</p>}
              <p className="mt-1 text-xs">Cài ffmpeg theo HUONG_DAN_CAI_DAT.md (Bước 8) rồi bấm:</p>
              <button className={`${btnSm} mt-1`} disabled={recheck.isPending}
                onClick={() => recheck.mutate(undefined, { onError: onErr })}>
                🔄 Kiểm tra lại ffmpeg
              </button>
            </div>
          ) : (
            <p className="text-xs text-gray-500" title={data.ffmpeg.version}>✅ ffmpeg sẵn sàng</p>
          )}

          <label className="flex items-center gap-2">
            <input type="checkbox" checked={data.enabled} disabled={save.isPending}
              onChange={(e) => set({ enabled: e.target.checked })} />
            <span className="text-gray-200">Tự master ngay sau khi tải 2 bản từ Suno</span>
          </label>

          <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2">
            <span className="text-gray-400">Kiểu master</span>
            <select className={select} value={data.preset} disabled={save.isPending}
              onChange={(e) => set({ preset: e.target.value })}>
              {data.presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} — {p.lufs} LUFS{p.note && ` · ${p.note}`}
                </option>
              ))}
            </select>
            <span className="text-gray-400">Lưu thành</span>
            <select className={select} value={data.output_format} disabled={save.isPending}
              onChange={(e) => set({ output_format: e.target.value as MasterSettings['output_format'] })}>
              {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </div>

          {withMaster.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-300">
                🎚 {done}/{withMaster.length} bản đã master
                {working > 0 && <span className="text-violet-300"> · đang chạy {working}</span>}
              </span>
              <span className="flex-1" />
              <button className={btnPrimary} disabled={!data.ffmpeg.ok || runAll.isPending || todo === 0}
                onClick={() => masterAll(false)}>
                🎚 Master các bản chưa master{todo > 0 && ` (${todo})`}
              </button>
              <button className={btnSm} disabled={!data.ffmpeg.ok || runAll.isPending || working > 0}
                title="Đổi kiểu master xong → bấm để làm lại mọi bản theo kiểu mới"
                onClick={() => masterAll(true)}>
                ↻ Master lại tất cả
              </button>
            </div>
          )}
          {note && <p className="text-xs text-blue-300">{note}</p>}
          {err && <p className="text-xs text-red-400">{err}</p>}
          <p className="text-xs text-gray-500">
            Bản master lưu ở <code>ban_cover\mastered\</code>, file gốc giữ nguyên. Tab ④ Mix tự dùng bản master
            khi có. Đổi kiểu master chỉ áp cho lần master sau — bấm <b>↻ Master lại</b> để làm lại bản cũ.
          </p>
        </div>
      )}
    </div>
  );
}
