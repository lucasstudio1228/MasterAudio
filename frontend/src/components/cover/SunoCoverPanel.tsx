import { useEffect, useRef, useState, type RefObject } from 'react';
import {
  coverAudioUrl,
  errText,
  OLD_SERVER_MSG,
  sourceAudioUrl,
  tempoAudioUrl,
  useMarkCover,
  useExcludeSource,
  useCoverTempo,
  useRedoCover,
  useRunQc,
  useSources,
  useSunoOne,
  useSunoPause,
  useSunoResume,
  useSunoStart,
  useSunoWorkflow,
  useSunoStop,
  useSunoWavDownloader,
  useUpdateSource,
  type SourceCover,
  type SourceSong,
  type SunoStatus,
  type SunoWorker,
} from '../../api/sources';
import {
  recoveryAudioUrl,
  useDeleteTrack,
  useRestoreSelectedTracks,
  useTrackRecovery,
} from '../../api/tracks';
import MasterPanel, { CoverMasterLine } from './MasterPanel';
import OwnCoverPanel from './OwnCoverPanel';
import SunoLoginPanel from './SunoLoginPanel';
import { CoverImportButton } from './SourcePanel';

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnSm = 'rounded-md bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';

const CREDITS_PER_SONG = 20;   // Suno: 1 lượt Create (ra 2 bản) ≈ 20 credit

// Thông số tool tự đặt trên Suno (theo file workflow của người dùng — phần không lách bộ lọc).
const SUNO_SETTINGS: [string, string][] = [
  ['Chế độ', 'Advanced · model v6'],
  ['Cách làm', 'Đính file bài gốc → Cover'],
  ['Tên bài', 'Đúng tên bài gốc, KHÔNG kèm ca sĩ'],
  ['Lyrics', 'Lời bài hát (tab ①)'],
  ['Vocal Gender', 'Theo ca sĩ gốc (AI đoán — sửa được ở từng bài)'],
  ['Nhạc cụ sáng tạo', 'Mặc định bật → API AI tự chọn phù hợp; có thể tắt/chọn tay từng bài'],
  ['Duration', 'Auto'],
  ['Max Mode', 'Bật'],
  ['Weirdness', '10%'],
  ['Style Influence', '75%'],
  ['Audio Influence', '50%'],
  ['Variety', 'Normal'],
  ['Tải về', 'Lấy trực tiếp 2 ID Suno → giải mã asset liên tục → WAV PCM16 (không thu realtime/không qua Studio)'],
];

const SUNO_BADGE: Partial<Record<SunoStatus, { label: string; cls: string }>> = {
  queued: { label: '⏳ Chờ làm trên Suno', cls: 'bg-blue-600/25 text-blue-200' },
  running: { label: '🎛 Đang làm trên Suno…', cls: 'bg-blue-600/40 text-blue-100 animate-pulse' },
  tested: { label: '🧪 Chạy thử OK', cls: 'bg-teal-600/30 text-teal-200' },
  rejected: { label: '⛔ Suno báo bản quyền', cls: 'bg-red-700/40 text-red-200' },
  failed: { label: '❌ Lỗi khi làm trên Suno', cls: 'bg-red-600/30 text-red-200' },
  stopped: { label: '⏸ Đã dừng', cls: 'bg-gray-600/40 text-gray-200' },
  redo: { label: '⏳ Chờ tạo lại trên Suno', cls: 'bg-orange-600/30 text-orange-200' },
};

// Bài «Tạo cover trên Suno» sẽ làm (khớp với server: suno/start).
const RETRY_STATES: SunoStatus[] = ['redo', 'failed', 'stopped', 'queued', 'running'];
// Suno chỉ nhận bản WAV đã xuất qua bước chỉnh tempo, không dùng thẳng file gốc.
const ready = (s: SourceSong) => s.status !== 'skipped_no_lyrics'
  && s.has_file && s.tempo_ready && s.lyric_line_count > 0;
const wantsSuno = (s: SourceSong, w?: SunoWorker) =>
  s.status !== 'excluded' && ready(s) && s.suno_status !== 'rejected'
  && w?.current !== s.id && !w?.pending.includes(s.id)
  && (s.covers.length === 0 || RETRY_STATES.includes(s.suno_status));

function CopyBtn({ text, label }: { text: string; label: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button className={btnSm} disabled={!text}
      onClick={() => navigator.clipboard?.writeText(text).then(() => {
        setOk(true);
        setTimeout(() => setOk(false), 1500);
      })}>
      {ok ? '✓ Đã chép' : label}
    </button>
  );
}

const mmss = (sec: number) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`;

function SunoWavDownloader({ projectId }: { projectId: number }) {
  const [links, setLinks] = useState('');
  const download = useSunoWavDownloader(projectId);
  const result = download.data;
  return (
    <div className={card}>
      <h2 className={h2}>⬇ Tải gói WAV từ link Suno</h2>
      <p className="text-xs text-gray-400">
        Dán link/ID bài Suno của bạn, mỗi dòng một bài (tối đa 20). Tool kết nối thẳng
        Suno/CDN Suno và tạo ZIP gồm WAV + ảnh + lyrics, không gửi cookie/link qua website khác.
      </p>
      <textarea className="mt-2 h-28 w-full rounded-lg border border-white/10 bg-surface-2 p-2 text-xs text-gray-100"
        value={links} onChange={(e) => setLinks(e.target.value)}
        placeholder={'https://suno.com/song/…\nhttps://suno.com/s/…'} />
      <p className="mt-2 text-xs text-gray-300">
        Tool dùng quyền audio do chính player Suno cấp, tải asset liên tục rồi giải mã một lần sang
        WAV PCM16; không phát/thu realtime nên không tạo khe ngắt giữa các chunk. Không EQ, master
        hay đổi tempo. Chỉ dùng cho bài bạn sở hữu hoặc được phép lưu.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <button className={btnPrimary} disabled={download.isPending || !links.trim()}
          onClick={() => download.mutate({ links, allowCompatible: true })}>
          {download.isPending ? '⏳ Đang tải, giải mã và đóng ZIP…' : '⬇ Tải WAV + ZIP'}
        </button>
      </div>
      {download.isError && <p className="mt-2 text-xs text-red-400">{errText(download.error)}</p>}
      {result && (
        <div className="mt-3 space-y-1 text-xs">
          <p className="text-gray-300">Đã tải {result.downloaded}/{result.items.length} · {result.folder}</p>
          {result.items.map((item, index) => item.ok ? (
            <p key={index} className="text-green-300">
              ✓ {item.filename} · WAV giải mã liên tục
              {item.codec && ` · ${item.codec}`}{item.sample_rate && ` · ${item.sample_rate} Hz`}
              {item.zip_filename && ` · ZIP: ${item.zip_filename}`}
            </p>
          ) : (
            <p key={index} className="text-red-400">✗ {item.input}: {item.error}</p>
          ))}
        </div>
      )}
    </div>
  );
}

const CUSTOM_INSTRUMENT = '__custom__';
const NO_CREATIVE_INSTRUMENT = '__no_creative_instrument__';
const INSTRUMENT_OPTIONS = [
  { value: 'Saxophone', label: 'Saxophone' },
  { value: 'Flute', label: 'Sáo' },
  { value: 'Bass', label: 'Bass' },
  { value: 'Piano', label: 'Piano' },
  { value: 'Acoustic Guitar', label: 'Guitar acoustic' },
  { value: 'Electric Guitar', label: 'Guitar điện' },
  { value: 'Violin', label: 'Violin' },
  { value: 'Cello', label: 'Cello' },
  { value: 'Trumpet', label: 'Trumpet' },
] as const;

// ---------------------------------------------------------------- thanh chỉnh tempo 1 bản cover
const TEMPO_MIN = 0.5;
const TEMPO_MAX = 1.5;
const clampTempo = (x: number) => Math.min(TEMPO_MAX, Math.max(TEMPO_MIN, Math.round(x * 100) / 100));
const tempoLabel = (x: number) => {
  const pct = Math.round((x - 1) * 100);
  return pct === 0 ? 'tốc độ gốc' : pct > 0 ? `nhanh hơn ${pct}%` : `chậm hơn ${-pct}%`;
};

// Kéo thanh → mọi trình phát trong bản cover này phát ngay ở tốc độ mới (giữ cao độ)
// để nghe thử. OK → server xuất hẳn file tempo mới; Lời bài hát / Mix / Video dùng file đó.
function CoverTempoBar({ projectId, source, cover, rowRef }: {
  projectId: number; source: SourceSong; cover: SourceCover;
  rowRef: RefObject<HTMLDivElement | null>;
}) {
  const setTempo = useCoverTempo(projectId);
  const applied = cover.tempo_factor ?? 1;
  const [pending, setPending] = useState(applied);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { setPending(applied); }, [applied]);

  // Nghe thử: file đang ở tempo «applied» → phát nhanh/chậm thêm pending/applied lần.
  const rate = pending / applied;
  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const set = (a: HTMLAudioElement) => {
      a.preservesPitch = true;
      a.defaultPlaybackRate = rate;
      a.playbackRate = rate;
    };
    row.querySelectorAll('audio').forEach(set);
    // Đổi file (bản master xong, tempo mới) làm trình phát quên tốc độ → đặt lại.
    const onMedia = (e: Event) => {
      if (e.target instanceof HTMLAudioElement) set(e.target);
    };
    row.addEventListener('loadedmetadata', onMedia, true);
    row.addEventListener('play', onMedia, true);
    return () => {
      row.removeEventListener('loadedmetadata', onMedia, true);
      row.removeEventListener('play', onMedia, true);
    };
  }, [rate, rowRef]);

  const dirty = Math.abs(pending - applied) > 1e-6;
  const masterBusy = cover.master?.state === 'queued' || cover.master?.state === 'running';
  const sunoBusy = source.suno_status === 'queued' || source.suno_status === 'running';
  const locked = setTempo.isPending || masterBusy || sunoBusy;
  const newDuration = cover.duration_seconds > 0 ? cover.duration_seconds * applied / pending : 0;

  return (
    <div className="mt-1 rounded border border-cyan-400/25 bg-cyan-500/5 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-cyan-100">🎚 Tempo bài hát</span>
        <button className={btnSm} disabled={locked || pending <= TEMPO_MIN}
          title="Chậm hơn 1%" onClick={() => setPending((x) => clampTempo(x - 0.01))}>−</button>
        <input type="range" min={TEMPO_MIN} max={TEMPO_MAX} step={0.01} value={pending}
          disabled={locked} className="min-w-32 flex-1 accent-cyan-400"
          aria-label="Tempo bài hát"
          onChange={(e) => { setNote(null); setPending(clampTempo(Number(e.target.value))); }} />
        <button className={btnSm} disabled={locked || pending >= TEMPO_MAX}
          title="Nhanh hơn 1%" onClick={() => setPending((x) => clampTempo(x + 0.01))}>+</button>
        <span className="w-36 tabular-nums text-cyan-100">
          {pending.toFixed(2)}× <span className="text-cyan-300/70">({tempoLabel(pending)})</span>
        </span>
        <button className={btnSm} disabled={locked || pending === 1}
          title="Về đúng tốc độ bản Suno tải về" onClick={() => setPending(1)}>↺ 1.00×</button>
        {dirty && (
          <button className={btnSm} disabled={setTempo.isPending}
            title="Bỏ thay đổi, quay về tempo đang dùng" onClick={() => setPending(applied)}>Huỷ</button>
        )}
        <button className="rounded-md bg-cyan-600 px-3 py-1 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-40"
          disabled={!dirty || locked}
          title={masterBusy ? 'Bản này đang master — đợi xong'
            : sunoBusy ? 'Bài này đang làm trên Suno — đợi xong'
              : 'Xác nhận: chuyển hẳn bài này sang tempo mới cho Lời bài hát và Mix'}
          onClick={() => {
            setErr(null);
            setNote(null);
            setTempo.mutate({ trackId: cover.track_id, factor: pending }, {
              onError: (e) => setErr(errText(e)),
              onSuccess: (r) => setNote(r.changed
                ? `✅ Đã chuyển sang tempo ${r.factor.toFixed(2)}×`
                  + (r.duration_seconds ? ` · dài ${mmss(r.duration_seconds)}` : '')
                  + (r.lyrics_rescaled ? ' · lời đã căn được giãn theo tempo mới' : '')
                  + (r.remastering ? ' · đang master lại' : '')
                : 'Tempo không đổi.'),
            });
          }}>
          {setTempo.isPending ? '⏳ Đang đổi…' : 'OK'}
        </button>
      </div>
      <p className="mt-0.5 text-[11px] text-cyan-200/70">
        {setTempo.isPending ? 'Đang xuất file tempo mới (giữ nguyên cao độ giọng)…'
          : dirty ? `Đang nghe thử ${pending.toFixed(2)}× (bấm ▶ ở trên để nghe) — bấm OK để xác nhận đổi bài sang tempo mới`
            + (newDuration ? ` (bài sẽ dài khoảng ${mmss(newDuration)}).` : '.')
            : applied !== 1 ? `Bài đang dùng tempo ${applied.toFixed(2)}× — Lời bài hát và Mix dùng bản này. Bản Suno gốc vẫn được cất (↺ 1.00× rồi OK để quay về).`
              : 'Kéo thanh để nghe thử nhanh/chậm hơn (giữ nguyên cao độ giọng), ưng thì bấm OK.'}
      </p>
      {note && <p className="text-[11px] text-green-300">{note}</p>}
      {err && <p className="text-[11px] text-red-400">{err}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- 1 bản cover + kết quả kiểm tra lỗi
function CoverRow({ projectId, source, cover, redoBpm, settingsPending }:
  { projectId: number; source: SourceSong; cover: SourceCover; redoBpm?: string; settingsPending?: boolean }) {
  const mark = useMarkCover(projectId);
  const redo = useRedoCover(projectId);
  const trash = useDeleteTrack(projectId);
  const [err, setErr] = useState<string | null>(null);
  const qc = cover.qc;
  const status = qc?.status ?? 'pending';
  const bad = status === 'error';
  const errors = qc?.issues.filter((i) => i.level === 'error') ?? [];
  const notes = qc?.issues.filter((i) => i.level === 'warning') ?? [];
  const onErr = (e: unknown) => setErr(errText(e));
  const busy = mark.isPending || redo.isPending || trash.isPending || !!settingsPending;
  const sunoBusy = source.suno_status === 'queued' || source.suno_status === 'running';
  const bpm = Number(redoBpm);
  const validBpm = Number.isFinite(bpm) && bpm >= 20 && bpm <= 300;
  const rowRef = useRef<HTMLDivElement>(null);
  const tempo = cover.tempo_factor ?? 1;
  const instrumentLabel = source.featured_instrument === NO_CREATIVE_INSTRUMENT
    ? 'không thêm nhạc cụ sáng tạo'
    : source.featured_instrument || 'AI tự chọn nhạc cụ phù hợp';

  return (
    <div ref={rowRef} className={`rounded-md border px-2 py-1.5 ${bad ? 'border-red-500/60 bg-red-600/10' : 'border-white/5'}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="min-w-0 flex-1 truncate text-gray-200" title={cover.filename}>
          🎤 {cover.filename}
          {cover.duration_seconds > 0 && <span className="text-gray-500"> · {mmss(cover.duration_seconds)}</span>}
        </span>
        {tempo !== 1 && (
          <span className="rounded bg-cyan-600/25 px-2 py-0.5 text-cyan-100"
            title="Bài đã được chuyển sang tempo này (bấm OK ở thanh tempo)">
            🎚 tempo {tempo.toFixed(2)}×
          </span>
        )}
        {status === 'pending' && (
          <span className="rounded bg-blue-600/25 px-2 py-0.5 text-blue-200 animate-pulse">⏳ Đang kiểm tra…</span>
        )}
        {bad && <span className="rounded bg-red-600 px-2 py-0.5 font-semibold text-white">🔴 Lỗi</span>}
        {(status === 'ok' || status === 'warning') && (
          <span className="rounded bg-green-600/25 px-2 py-0.5 text-green-200"
            title={qc?.compared_with === 'original'
              ? 'Đã so với bài gốc'
              : 'Chưa có file bài gốc — so với các bản cover khác trong project'}>
            ✅ Ổn{qc?.dismissed && ' (bạn đã nghe)'}
          </span>
        )}
      </div>
      <audio controls preload="none" src={coverAudioUrl(cover.track_id, String(tempo))} className="mt-1 h-8 w-full" />
      <CoverMasterLine projectId={projectId} cover={cover} />
      {cover.tempo_factor !== undefined && (
        <CoverTempoBar projectId={projectId} source={source} cover={cover} rowRef={rowRef} />
      )}
      {bad && (
        <ul className="mt-1 space-y-0.5 text-xs text-red-200">
          {qc?.manual_flag && <li>• Bạn đã báo bản này bị lỗi.</li>}
          {errors.map((i) => <li key={i.code}>• {i.message}</li>)}
        </ul>
      )}
      {!bad && notes.length > 0 && (
        <p className="mt-1 text-xs text-gray-500">Lưu ý (không phải lỗi): {notes.map((i) => i.message).join(' · ')}</p>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {bad && (
          <button className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-40"
            disabled={busy || sunoBusy || !validBpm}
            title={sunoBusy ? 'Bài này đang làm trên Suno — đợi xong'
              : !validBpm ? 'Nhập Tempo từ 20 đến 300 ở ô Tempo phía trên trước khi tạo lại'
                : 'Tạo lại với Tempo hiện tại và áp dụng lựa chọn Nhạc cụ sáng tạo nếu đã thay đổi'}
            onClick={() => {
              if (!window.confirm(
                `Tạo lại «${source.title || source.query}» trên Suno?\n\n`
                + `• Bản lỗi «${cover.filename}» được bỏ khỏi project — file KHÔNG bị xoá, chỉ cất vào thư mục ban_cover\\_loi.\n`
                + `• Tool chạy lại từ Editor và bắt buộc đặt Tempo = ${bpm}.\n`
                + `• Nhạc cụ sáng tạo: ${instrumentLabel}. Nếu vừa thay đổi, tool viết lại Styles để áp dụng lựa chọn này.\n`
                + '• Giữ nguyên lyrics, giọng, các slider và BPM trong prompt; Tempo chỉ được đặt trong Suno Editor.\n'
                + `• Suno ra 2 bản mới, tốn khoảng ${CREDITS_PER_SONG} credit.\n`
                + '• Thiếu file bài gốc hoặc lời thì bài chỉ được đánh dấu «chờ tạo lại».',
              )) return;
              setErr(null);
              redo.mutate({
                id: source.id, trackIds: [cover.track_id], editorBpm: bpm, tempoOnly: true,
              }, { onError: onErr });
            }}>
            🔁 Tạo lại · Tempo {validBpm ? bpm : ''} + nhạc cụ
          </button>
        )}
        {bad && !qc?.manual_flag && (
          <button className={btnSm} disabled={busy} title="Bạn nghe thấy bình thường → tắt báo đỏ cho bản này"
            onClick={() => mark.mutate({ trackId: cover.track_id, dismissed: true }, { onError: onErr })}>
            ✓ Nghe ổn
          </button>
        )}
        {qc?.manual_flag && (
          <button className={btnSm} disabled={busy}
            onClick={() => mark.mutate({ trackId: cover.track_id, flagged: false }, { onError: onErr })}>
            ↩ Bỏ báo lỗi
          </button>
        )}
        {!bad && (
          <button className={`${btnSm} text-gray-400`} disabled={busy}
            title="Bạn nghe thấy lỗi mà tool không phát hiện → đánh dấu đỏ để tạo lại"
            onClick={() => mark.mutate({ trackId: cover.track_id, flagged: true }, { onError: onErr })}>
            🚩 Báo lỗi
          </button>
        )}
        <button className={`${btnSm} ml-auto text-gray-400 hover:text-red-300`}
          disabled={busy || sunoBusy}
          title={sunoBusy ? 'Bài này đang làm trên Suno — đợi xong'
            : 'Không dùng bản này → chuyển vào thùng rác (file vẫn giữ, khôi phục được ở «Thùng rác» bên dưới)'}
          onClick={() => {
            if (!window.confirm(
              `Chuyển «${cover.filename}» vào thùng rác?\n\n`
              + '• Bản này không còn được dùng để master / ghép mix / làm video.\n'
              + '• File trên máy KHÔNG bị xoá — bấm «♻ Khôi phục» trong Thùng rác để lấy lại.',
            )) return;
            setErr(null);
            trash.mutate(cover.track_id, { onError: onErr });
          }}>
          🗑 Xoá bản này
        </button>
        {err && <span className="text-xs text-red-400">{err}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- điều khiển tự làm trên Suno
function SunoControls({ projectId, items, worker }:
  { projectId: number; items: SourceSong[]; worker?: SunoWorker }) {
  const start = useSunoStart(projectId);
  const stop = useSunoStop(projectId);
  const pause = useSunoPause(projectId);
  const resume = useSunoResume(projectId);
  const workflow = useSunoWorkflow();
  const semi = !!worker?.semi_manual;
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const onErr = (e: unknown) => setErr(errText(e));
  const loginOpen = worker?.mode === 'login';
  const running = !!worker?.busy && !loginOpen;
  const paused = !!worker?.paused;
  const todo = items.filter((s) => wantsSuno(s, worker));
  // Tạo mới/tạo lại được phép thay thế cả bài đã loại; server sẽ kích hoạt lại
  // nguồn nhưng không khôi phục các cover cũ trong thùng rác.
  const freshTodo = items.filter((s) => ready(s)
    && s.suno_status !== 'rejected' && worker?.current !== s.id
    && !worker?.pending.includes(s.id));
  const notReady = items.filter((s) => !ready(s) && s.covers.length === 0 && s.suno_status !== 'rejected');
  const left = (worker?.pending.length ?? 0) + (worker?.current ? 1 : 0);

  const run = (dry: boolean, fresh = false) => {
    const count = fresh ? freshTodo.length : todo.length;
    if (!dry && !window.confirm(
      `${fresh ? 'TẠO MỚI/TẠO LẠI TỪ ĐẦU' : 'TIẾP TỤC'} cover trên Suno cho ${count} bài.\n\n`
      + `• Mỗi bài Suno ra 2 bản, tốn khoảng ${CREDITS_PER_SONG} credit → tổng tối đa khoảng ${count * CREDITS_PER_SONG} credit.\n`
      + (fresh ? '• Toàn bộ bài đủ dữ liệu sẽ chạy lại từ bước Editor; bản cover đã tải vẫn được giữ để so sánh.\n' : '• Tool giữ trạng thái đã lưu và chỉ chạy tiếp phần còn dở, không Create lại khi đã có ID Suno.\n')
      + (semi ? '• Chế độ BÁN THỦ CÔNG: mỗi bài tool mở Suno Editor → BẠN chỉnh Tempo + bấm Save as New Song → tool điền lyrics/style/thông số → BẠN bấm Create → tool tải về, master và sang bài sau. Mỗi lần cần bạn, tool kêu «bíp».\n' : '')
      + '• Suno hiện captcha hoặc hỏi xác nhận quyền dùng bản ghi âm → tool kêu «bíp» và đưa cửa sổ Suno lên: bạn TỰ bấm, tool chờ rồi làm tiếp.\n'
      + '• Đừng tắt cửa sổ Suno khi tool đang chạy.\n\n'
      + 'Lần đầu nên bấm «🧪 Chạy thử» trước.',
    )) return;
    setErr(null);
    setNote(null);
    start.mutate({ dryRun: dry, fresh }, {
      onError: onErr,
      onSuccess: (r) => setNote(r.queued
        ? (dry ? '🧪 Đang chạy thử 1 bài — tool điền form Suno rồi dừng, KHÔNG bấm Create.'
          : `Đã xếp ${r.queued} bài vào hàng đợi Suno.`)
        : 'Không có bài nào cần làm.'),
    });
  };

  if (!worker) {
    return <p className="rounded-lg bg-orange-600/20 p-2 text-sm text-orange-200">{OLD_SERVER_MSG}</p>;
  }
  return (
    <div className="space-y-3">
      <SunoLoginPanel worker={worker} />

      <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-white/10 bg-surface-2 p-2 text-sm text-gray-200">
        <input type="checkbox" className="mt-0.5" checked={semi} disabled={workflow.isPending}
          onChange={(e) => workflow.mutate(e.target.checked, { onError: onErr })} />
        <span>
          <b>✋ Bán thủ công</b> — tool mở Suno Editor, <b>bạn</b> chỉnh Tempo + bấm «Save as New Song»;
          tool điền lyrics, style, thông số; <b>bạn</b> bấm Create; tool tải về, master và sang bài sau.
          {running && <span className="text-xs text-gray-400"> (đổi chế độ áp dụng từ bài kế tiếp)</span>}
        </span>
      </label>

      <div className="flex flex-wrap items-center gap-2">
        {!running ? (
          <>
            <button className={btnPrimary} disabled={start.isPending || loginOpen || freshTodo.length === 0}
              title={loginOpen ? 'Đóng cửa sổ đăng nhập Suno trước' : ''}
              onClick={() => run(false, true)}>
              ▶ Tạo mới / tạo lại cover trên Suno{freshTodo.length > 0 && ` (${freshTodo.length} bài)`}
            </button>
            <button className="rounded-lg border border-amber-600/40 bg-amber-900/60 px-3 py-1.5 text-sm font-medium text-amber-100 hover:bg-amber-800/70 disabled:opacity-40"
              disabled={start.isPending || loginOpen || todo.length === 0}
              title="Giữ các bước/ID Suno đã lưu và chỉ làm tiếp phần đang dở"
              onClick={() => run(false, false)}>
              ▶ Tiếp tục chạy cover trên Suno{todo.length > 0 && ` (${todo.length} bài)`}
            </button>
            <button className={btn} disabled={start.isPending || loginOpen || todo.length === 0}
              title="Tool điền đủ form Suno cho 1 bài rồi DỪNG, không bấm Create — không tốn credit"
              onClick={() => run(true, false)}>
              🧪 Chạy thử (không bấm Create)
            </button>
          </>
        ) : (
          <>
            <button className="rounded-lg border border-amber-500/40 bg-amber-950/40 px-3 py-1.5 text-sm font-medium text-amber-100 hover:bg-amber-900/60 disabled:opacity-40"
              disabled={pause.isPending || resume.isPending}
              onClick={() => (paused ? resume : pause).mutate(undefined, { onError: onErr })}>
              {paused ? '▶ Tiếp tục' : '⏸ Tạm dừng'}
            </button>
            <button className="rounded-lg bg-red-600/80 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-40"
              disabled={stop.isPending}
              onClick={() => stop.mutate(undefined, { onError: onErr })}>
              ⏹ Dừng lại{left > 0 && ` (còn ${left} bài)`}
            </button>
          </>
        )}
      </div>

      {worker.human && (
        <div className="rounded-lg border border-red-500 bg-red-600/20 p-3 text-sm font-medium text-red-100 animate-pulse">
          {worker.human}
        </div>
      )}
      {running && worker.message && (
        <p className="rounded-lg bg-blue-600/15 p-2 text-sm text-blue-100">
          {worker.dry_run && '🧪 '}{worker.message}
        </p>
      )}
      {note && !running && <p className="text-xs text-blue-300">{note}</p>}
      {err && <p className="text-xs text-red-400">{err}</p>}
      {notReady.length > 0 && (
        <p className="text-xs text-orange-300">
          {notReady.length} bài chưa đủ file gốc + bản tempo đã xuất + lời nên chưa làm trên Suno được.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 1 bài gốc
/** Thùng rác các bản cover đã xoá của 1 bài — nghe thử và khôi phục. */
function CoverTrash({ projectId, sourceId }: { projectId: number; sourceId: number }) {
  const { data } = useTrackRecovery(projectId);
  const restore = useRestoreSelectedTracks(projectId);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const items = (data?.items ?? []).filter((i) => i.source_id === sourceId);
  if (items.length === 0) return null;
  const run = (ids: number[]) => {
    setErr(null);
    restore.mutate({ trash_ids: ids, orphan_files: [] }, {
      onSuccess: (r) => {
        if (r.missing_files > 0) setErr(`${r.missing_files} bản không còn file trên máy nên không khôi phục được.`);
      },
      onError: (e) => setErr(errText(e)),
    });
  };
  return (
    <div className="mt-2 pl-8">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button className={`${btnSm} text-gray-400`} onClick={() => setOpen(!open)}>
          🗑 Thùng rác ({items.length}) {open ? '▾' : '▸'}
        </button>
        {open && items.length > 1 && (
          <button className={btnSm} disabled={restore.isPending}
            onClick={() => run(items.filter((i) => i.file_exists).map((i) => i.id))}>
            ♻ Khôi phục tất cả
          </button>
        )}
        {err && <span className="text-red-400">{err}</span>}
      </div>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {items.map((i) => (
            <div key={i.id} className="rounded-md border border-dashed border-white/10 px-2 py-1.5 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-gray-400" title={i.filename}>🗑 {i.filename}</span>
                {i.file_exists ? (
                  <button className={btnSm} disabled={restore.isPending}
                    title="Đưa bản này trở lại danh sách bản cover"
                    onClick={() => run([i.id])}>
                    ♻ Khôi phục
                  </button>
                ) : (
                  <span className="text-red-400">File đã mất trên máy</span>
                )}
              </div>
              {i.file_exists && (
                <audio controls preload="none" src={recoveryAudioUrl(projectId, i.id)} className="mt-1 h-8 w-full" />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceRow({ projectId, s, index, worker }:
  { projectId: number; s: SourceSong; index: number; worker?: SunoWorker }) {
  const one = useSunoOne(projectId);
  const exclude = useExcludeSource(projectId);
  const update = useUpdateSource(projectId);
  const [err, setErr] = useState<string | null>(null);
  const onErr = (e: unknown) => setErr(errText(e));
  const active = worker?.current === s.id || !!worker?.pending.includes(s.id);
  const loginOpen = worker?.mode === 'login';
  const badge = SUNO_BADGE[s.suno_status];
  const done = s.covers.length > 0 && !['redo', 'failed', 'stopped', 'rejected', 'tested'].includes(s.suno_status);
  const excluded = s.status === 'excluded';
  const rejected = s.suno_status === 'rejected';
  const canSuno = !!worker && !active && !loginOpen && ready(s);
  const instrumentIsEnabled = s.featured_instrument !== NO_CREATIVE_INSTRUMENT;
  const isPresetInstrument = INSTRUMENT_OPTIONS.some((x) => x.value === s.featured_instrument);
  const [instrumentEnabled, setInstrumentEnabled] = useState(instrumentIsEnabled);
  const [instrumentMode, setInstrumentMode] = useState(
    instrumentIsEnabled && s.featured_instrument
      ? (isPresetInstrument ? s.featured_instrument : CUSTOM_INSTRUMENT) : '',
  );
  const [customInstrument, setCustomInstrument] = useState(
    instrumentIsEnabled && s.featured_instrument && !isPresetInstrument ? s.featured_instrument : '',
  );
  const [redoBpm, setRedoBpm] = useState(s.suno_editor_bpm ? String(s.suno_editor_bpm) : '');

  useEffect(() => {
    const enabled = s.featured_instrument !== NO_CREATIVE_INSTRUMENT;
    const preset = INSTRUMENT_OPTIONS.some((x) => x.value === s.featured_instrument);
    setInstrumentEnabled(enabled);
    setInstrumentMode(enabled && s.featured_instrument
      ? (preset ? s.featured_instrument : CUSTOM_INSTRUMENT) : '');
    setCustomInstrument(enabled && s.featured_instrument && !preset ? s.featured_instrument : '');
  }, [s.featured_instrument]);

  useEffect(() => {
    setRedoBpm(s.suno_editor_bpm ? String(s.suno_editor_bpm) : '');
  }, [s.id, s.suno_editor_bpm]);

  const go = (opts: { fresh?: boolean; dryRun?: boolean; editorBpm?: number; tempoOnly?: boolean; songLinks?: string[]; authorizedLyrics?: boolean }, msg: string | null) => {
    if (msg && !window.confirm(msg)) return;
    setErr(null);
    one.mutate({ id: s.id, ...opts }, { onError: onErr });
  };
  const name = s.title || s.query;

  return (
    <li className="rounded-lg border border-white/5 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="w-6 text-right text-gray-500">{index + 1}.</span>
        <span className="min-w-0 flex-1 truncate text-gray-100">
          {name}{s.artist && <span className="text-gray-400"> – {s.artist}</span>}
        </span>
        {s.status === 'manual' && <span className="text-xs text-gray-400">✍️ tự nhập</span>}
        {excluded && <span className="rounded bg-red-900/50 px-2 py-0.5 text-xs text-red-200">🚫 đã loại khỏi Mix Set</span>}
        {badge && <span className={`rounded px-2 py-0.5 text-xs ${badge.cls}`}>{badge.label}</span>}
        {s.covers.length > 0
          ? <span className="text-xs text-green-300">🎤 {s.covers.length} bản cover</span>
          : !badge && <span className="text-xs text-gray-500">chưa có cover</span>}
      </div>
      {worker?.current === s.id && worker.message && (
        <p className="mt-1 pl-8 text-xs text-blue-300">{worker.message}</p>
      )}
      {s.suno_error && !active && (
        <p className={`mt-1 whitespace-pre-wrap break-words pl-8 text-xs ${
          s.suno_status === 'tested' ? 'text-teal-200/80'
            : s.suno_status === 'done' || s.suno_status === 'redo' ? 'text-orange-200/80' : 'text-red-300'}`}>
          {s.suno_error}
        </p>
      )}
      <div className="ml-8 mt-2 rounded-lg border border-purple-400/30 bg-purple-500/10 p-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-64 cursor-pointer items-center gap-2 text-sm font-semibold text-purple-100">
            <input type="checkbox" className="h-4 w-4 accent-purple-500"
              checked={instrumentEnabled} disabled={active || update.isPending}
              onChange={(e) => {
                const enabled = e.target.checked;
                setInstrumentEnabled(enabled);
                setInstrumentMode('');
                setCustomInstrument('');
                update.mutate({
                  id: s.id,
                  featured_instrument: enabled ? '' : NO_CREATIVE_INSTRUMENT,
                }, { onError: onErr });
              }} />
            🎷 Thêm nhạc cụ sáng tạo
            <span className="font-normal text-purple-300">(mặc định bật)</span>
          </label>
          {instrumentEnabled && <select id={`featured-instrument-${s.id}`}
              className="min-w-64 flex-1 rounded-md border border-purple-300/30 bg-surface-2 px-2.5 py-1.5 text-sm text-gray-100 disabled:opacity-40"
              value={instrumentMode} disabled={active || update.isPending}
              title="Để AI tự chọn hoặc chỉ định nhạc cụ nổi bật cho Styles Suno"
              onChange={(e) => {
                const value = e.target.value;
                setInstrumentMode(value);
                if (value === CUSTOM_INSTRUMENT) return;
                setCustomInstrument('');
                update.mutate({ id: s.id, featured_instrument: value }, { onError: onErr });
              }}>
              <option value="">AI tự chọn nhạc cụ phù hợp (mặc định)</option>
              {INSTRUMENT_OPTIONS.map((x) => (
                <option key={x.value} value={x.value}>{x.label}</option>
              ))}
              <option value={CUSTOM_INSTRUMENT}>Khác — tự nhập…</option>
            </select>}
          {instrumentEnabled && instrumentMode === CUSTOM_INSTRUMENT && (
            <span className="flex min-w-64 flex-1 items-center gap-1">
              <input className="min-w-0 flex-1 rounded-md border border-purple-300/30 bg-surface-2 px-2.5 py-1.5 text-sm text-gray-100 placeholder:text-gray-500 disabled:opacity-40"
                value={customInstrument} maxLength={80} disabled={active || update.isPending}
                placeholder="Nhập tên nhạc cụ khác"
                onChange={(e) => setCustomInstrument(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && customInstrument.trim()) {
                    update.mutate({ id: s.id, featured_instrument: customInstrument.trim() }, { onError: onErr });
                  }
                }} />
              <button className={btnSm} disabled={active || update.isPending || !customInstrument.trim()}
                onClick={() => update.mutate(
                  { id: s.id, featured_instrument: customInstrument.trim() }, { onError: onErr })}>
                Lưu
              </button>
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-purple-200/70">
          {instrumentEnabled
            ? 'Đang bật: mặc định AI tự chọn một nhạc cụ phù hợp với thể loại; bạn có thể chỉ định nhạc cụ khác.'
            : 'Đang tắt: AI không thêm nhạc cụ sáng tạo vào Styles Suno của bài này.'}
        </p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 pl-8">
        {s.has_file
          ? <a className={btnSm} href={sourceAudioUrl(s.id)} download={s.file_name}>⬇ File gốc</a>
          : s.status !== 'manual' && <span className="text-xs text-orange-300">chưa tải được file gốc</span>}
        {s.tempo_ready
          ? <a className={btnSm} href={tempoAudioUrl(s.id)} download={s.tempo_file_name}>
              🎚 Bản tempo {s.tempo_factor.toFixed(2)}×
            </a>
          : s.has_file && <span className="text-xs text-orange-300">chưa xuất bản tempo ở tab ①</span>}
        <select className="rounded-md border border-white/10 bg-surface-2 px-1.5 py-1 text-xs text-gray-200 disabled:opacity-40"
          value={s.vocal_gender} disabled={active || update.isPending}
          title="Giọng ca sĩ gốc — chọn «Vocal Gender» trên Suno"
          onChange={(e) => update.mutate(
            { id: s.id, vocal_gender: e.target.value as '' | 'male' | 'female' | 'duet' }, { onError: onErr })}>
          <option value="">Giọng: tool tự phân tích</option>
          <option value="male">Giọng: Nam</option>
          <option value="female">Giọng: Nữ</option>
          <option value="duet">Giọng: Song ca nam–nữ</option>
        </select>
        <label className="flex items-center gap-1 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-xs text-cyan-100"
          title={redoBpm
            ? `Tempo hiện tại đã đọc/lưu từ Suno Editor: ${redoBpm} BPM. Có thể sửa số này để làm lại.`
            : 'Chưa đọc được Tempo từ Suno Editor; số sẽ hiện sau khi Editor xác nhận BPM.'}>
          🎚 Tempo
          <input type="number" min={20} max={300} step={1}
            className="w-16 bg-transparent text-white outline-none"
            value={redoBpm} disabled={active || one.isPending}
            placeholder="Chưa đọc"
            onChange={(e) => setRedoBpm(e.target.value)} />
        </label>
        {s.suno_created && !active && (
          <>
            <button className={btnSm} disabled={!worker || loginOpen || one.isPending}
              title="Suno đã tạo xong 2 bản ở lần trước — chỉ tải về, KHÔNG bấm Create lại (không tốn credit)"
              onClick={() => go({}, null)}>
              ⬇ Tải lại 2 bản từ Suno
            </button>
            <button className={btnSm} disabled={!canSuno || one.isPending}
              onClick={() => go({ fresh: true, editorBpm: redoBpm ? Number(redoBpm) : undefined,
                                  tempoOnly: !!redoBpm },
                `Bỏ 2 bản Suno đã tạo lần trước và tạo bản MỚI cho «${name}»?\nTốn khoảng ${CREDITS_PER_SONG} credit.`)}>
              ↻ Tạo mới trên Suno
            </button>
          </>
        )}
        {!s.suno_created && !active && (
          <>
            <button className={btnSm} disabled={!canSuno || one.isPending}
              title={!ready(s) ? 'Cần file bài gốc + bản tempo đã xuất + lời trước' : ''}
              onClick={() => go({ fresh: true, editorBpm: redoBpm ? Number(redoBpm) : undefined,
                                  tempoOnly: !!redoBpm, authorizedLyrics: rejected || undefined },
                `${rejected ? '⚠ Lần trước Suno báo lyrics bài này có bản quyền. Chỉ làm lại khi lyrics đã thay bằng nội dung bạn tự sáng tác hoặc được phép sử dụng.\n\n' : ''}Làm mới/làm lại cover «${name}»${redoBpm ? ` với Tempo ${redoBpm} BPM` : ' với Tempo mặc định theo thể loại'} trên Suno?\nSuno ra 2 bản, tốn khoảng ${CREDITS_PER_SONG} credit.`)}>
              {done ? '➕ Làm thêm trên Suno' : s.covers.length || s.suno_status ? '↻ Làm lại trên Suno' : '▶ Làm trên Suno'}
            </button>
            <button className={`${btnSm} text-gray-400`} disabled={!canSuno || one.isPending || rejected}
              title="Điền form Suno cho bài này rồi dừng, KHÔNG bấm Create — không tốn credit"
              onClick={() => go({ dryRun: true }, null)}>
              🧪 Chạy thử
            </button>
          </>
        )}
        {!active && (
          <button className={btnSm} disabled={!worker || loginOpen || one.isPending}
            title="Dán link/ID các bài bạn tự làm trên Suno — tool chỉ tải về và nhập làm cover, không bấm Create (không tốn credit)"
            onClick={() => {
              const raw = window.prompt(
                `Dán link hoặc ID bài Suno bạn đã tự làm cho «${name}» (mỗi link 1 dòng hoặc cách nhau bằng dấu phẩy/khoảng trắng):`);
              if (!raw) return;
              const links = raw.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
              if (links.length) go({ songLinks: links }, null);
            }}>
            🔗 Nhận bài làm tay trên Suno
          </button>
        )}
        <CopyBtn text={s.title} label="📋 Chép tên bài" />
        <CopyBtn text={s.lyrics_text} label="📋 Chép lời" />
        {!excluded && <CoverImportButton projectId={projectId} source={s} />}
        {excluded ? (
          <button className={`${btnSm} text-emerald-300`} disabled={exclude.isPending}
            onClick={() => exclude.mutate({ id: s.id, restore: true }, { onError: onErr })}>
            ↩ Khôi phục bài
          </button>
        ) : (
          <button className={`${btnSm} text-red-300 hover:bg-red-600/30`}
            disabled={active || exclude.isPending}
            title="Loại bài và tất cả bản cover đã tải khỏi Mix Set; vẫn có thể khôi phục hoặc tạo bản Suno mới để thay thế"
            onClick={() => {
              if (window.confirm(`Loại «${name}» khỏi Mix Set?\n${s.covers.length} bản cover đã tải sẽ được chuyển vào thùng rác và có thể khôi phục.`))
                exclude.mutate({ id: s.id }, { onError: onErr });
            }}>
            🚫 Loại bài
          </button>
        )}
      </div>
      {err && <p className="mt-1 pl-8 text-xs text-red-400">{err}</p>}
      {s.covers.length > 0 && (
        <div className="mt-2 space-y-1.5 pl-8">
          {s.covers.map((c) => <CoverRow key={c.track_id} projectId={projectId}
            source={s} cover={c} redoBpm={redoBpm} settingsPending={update.isPending} />)}
        </div>
      )}
      {!excluded && <CoverTrash projectId={projectId} sourceId={s.id} />}
    </li>
  );
}

export default function SunoCoverPanel({ projectId, onGoToSource }:
  { projectId: number; onGoToSource: () => void }) {
  const { data } = useSources(projectId);
  const runQc = useRunQc(projectId);
  const items = data?.items ?? [];
  const skippedNoLyrics = items.filter((s) => s.status === 'skipped_no_lyrics');
  const activeItems = items.filter((s) => s.status !== 'skipped_no_lyrics');
  const worker = data?.suno_worker;
  const covers = activeItems.flatMap((s) => s.covers);
  const badCount = covers.filter((c) => c.qc?.status === 'error').length;
  const redoCount = activeItems.filter((s) => s.suno_status === 'redo').length;
  const rejectedCount = activeItems.filter((s) => s.suno_status === 'rejected').length;
  const masteredCount = covers.filter((c) => c.master?.state === 'done').length;
  const checking = !!data?.qc_running || covers.some((c) => !c.qc || c.qc.status === 'pending');
  return (
    <>
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="space-y-4">
        <div className={card}>
          <h2 className={h2}>② Suno Cover — tool tự làm</h2>
          {data && <SunoControls projectId={projectId} items={activeItems} worker={worker} />}
          <table className="mt-4 w-full text-sm">
            <tbody>
              {SUNO_SETTINGS.map(([k, v]) => (
                <tr key={k} className="border-b border-white/5">
                  <td className="py-1 pr-3 text-gray-400">{k}</td>
                  <td className="py-1 text-gray-100">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="mt-3 space-y-1 text-xs text-gray-500">
            <li>• Tool KHÔNG tự giải captcha, KHÔNG tự bấm đồng ý điều khoản, KHÔNG mua thêm credit — những
              việc đó tool kêu «bíp», đưa cửa sổ Suno lên và chờ bạn.</li>
            <li>• Suno báo lyrics có bản quyền (bảng cam) → tool bíp + hiện popup báo bạn; bạn <b>tự sửa lyrics
              và tự bấm Create</b> trong cửa sổ Suno, tool tự nhận diện và tải 2 bản mới. Tool không tự sửa lyrics.</li>
            <li>• Lỗi giữa chừng sau khi đã bấm Create → tool nhớ 2 bản Suno đã tạo, lần sau chỉ tải về (không
              tốn credit lại).</li>
          </ul>
        </div>
        {data && <MasterPanel projectId={projectId} covers={covers} />}
        <SunoWavDownloader projectId={projectId} />
        <OwnCoverPanel projectId={projectId} />
      </div>

      <div className={card}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className={`${h2} mb-0 flex-1`}>Bài gốc → bản cover ({activeItems.length})</h2>
          {skippedNoLyrics.length > 0 && (
            <span className="rounded bg-gray-600/40 px-2 py-0.5 text-xs text-gray-200">
              ⏭ Đã loại {skippedNoLyrics.length} bài không có lyrics
            </span>
          )}
          {badCount > 0 && (
            <span className="rounded bg-red-600 px-2 py-0.5 text-xs font-semibold text-white">🔴 {badCount} bản lỗi</span>
          )}
          {redoCount > 0 && (
            <span className="rounded bg-orange-600/30 px-2 py-0.5 text-xs text-orange-200">⏳ {redoCount} bài chờ tạo lại</span>
          )}
          {masteredCount > 0 && (
            <span className="rounded bg-violet-600/30 px-2 py-0.5 text-xs text-violet-100">
              🎚 {masteredCount}/{covers.length} đã master
            </span>
          )}
          {rejectedCount > 0 && (
            <span className="rounded bg-red-700/40 px-2 py-0.5 text-xs text-red-200">⛔ {rejectedCount} bài Suno từ chối</span>
          )}
          {covers.length > 0 && (
            <button className={btnSm} disabled={runQc.isPending || !!data?.qc_running}
              title="Kiểm tra lại mọi bản cover: tiếng rè/nhiễu, bị cắt ngắn, mất tiếng giữa bài, vỡ tiếng…"
              onClick={() => runQc.mutate()}>
              {checking ? '⏳ Đang kiểm tra…' : '🔍 Kiểm tra lại'}
            </button>
          )}
        </div>
        {covers.length > 0 && (
          <p className="mb-3 text-xs text-gray-500">
            Tool tự nghe từng bản cover và so với bài gốc: chỉ báo <b className="text-red-300">🔴 đỏ</b> khi có lỗi
            rõ ràng (rè/nhiễu, bị cắt ngắn, mất tiếng giữa bài, vỡ tiếng, im lặng). Máy báo nhầm → bấm
            <b> ✓ Nghe ổn</b>. Bạn nghe thấy lỗi mà máy không báo → bấm <b>🚩 Báo lỗi</b>.
          </p>
        )}
        {activeItems.length === 0 && (
          <p className="text-sm text-gray-400">
            Chưa có bài nào. <button className="text-blue-300 hover:underline" onClick={onGoToSource}>
              Sang tab ① Tải nhạc gốc</button> để thêm bài, hoặc nhập bài cover bạn đã làm ở khung bên trái.
          </p>
        )}
        <ol className="space-y-2">
          {activeItems.map((s, i) => <SourceRow key={s.id} projectId={projectId} s={s} index={i} worker={worker} />)}
        </ol>
      </div>
    </div>
    </>
  );
}
