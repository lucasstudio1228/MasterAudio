import { useEffect, useRef, useState } from 'react';
import {
  errText,
  sourceAudioUrl,
  tempoAudioUrl,
  useAddSources,
  useDeleteSource,
  useExcludeSource,
  useExportTempo,
  useImportCovers,
  useMakeLyrics,
  useRetrySource,
  useSources,
  useStartAllSources,
  useStopSources,
  useUpdateSource,
  useVerifyLyrics,
  type SourceSong,
  type SourceStatus,
} from '../../api/sources';
import NctLoginPanel from './NctLoginPanel';

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnSm = 'rounded-md bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';
const input = 'w-full rounded-lg border border-white/10 bg-surface-2 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-accent';

function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

const STATUS: Record<SourceStatus, { label: string; cls: string }> = {
  queued: { label: '⏳ Đang chờ', cls: 'bg-yellow-600/30 text-yellow-300' },
  running: { label: '⬇️ Đang tải…', cls: 'bg-blue-600/30 text-blue-300 animate-pulse' },
  done: { label: '✅ Đã tải', cls: 'bg-green-600/30 text-green-300' },
  lyrics_only: { label: '📝 Có lời, chưa có file', cls: 'bg-orange-600/30 text-orange-300' },
  login: { label: '🔑 Cần đăng nhập', cls: 'bg-orange-600/30 text-orange-300' },
  not_found: { label: '❓ Không tìm thấy', cls: 'bg-red-600/30 text-red-300' },
  failed: { label: '❌ Lỗi', cls: 'bg-red-600/30 text-red-300' },
  stopped: { label: '⏸ Đã dừng', cls: 'bg-gray-600/40 text-gray-300' },
  manual: { label: '✍️ Tự nhập', cls: 'bg-purple-600/30 text-purple-200' },
  skipped_no_lyrics: { label: '⏭ Bỏ qua · thiếu lời', cls: 'bg-gray-600/40 text-gray-300' },
  skipped_no_official: { label: '⏭ Bỏ qua · không có official', cls: 'bg-gray-600/40 text-gray-300' },
  excluded: { label: '🚫 Đã loại khỏi Mix Set', cls: 'bg-red-900/50 text-red-200' },
};

const QUALITY: Record<string, string> = {
  high: 'High quality', normal: 'Chất lượng thường', lossless: 'Lossless',
  zing_mp3: 'Zing MP3', ytmusic_mp3: 'YouTube Music', youtube_mp3: 'YouTube MP3',
};

const SOURCE_LABEL: Record<string, string> = {
  nhaccuatui: 'NhacCuaTui', zingmp3: 'Zing MP3', ytmusic: 'YouTube Music', youtube: 'YouTube',
};

const clampTempo = (factor: number) => Math.min(5, Math.max(0.2, factor || 1));
const sliderToTempo = (value: number) => Math.pow(5, value / 100);
const tempoToSlider = (factor: number) => Math.log(clampTempo(factor)) / Math.log(5) * 100;
const DEFAULT_COVER_TEMPO = 4.5;

function TempoControls({ projectId, source }: { projectId: number; source: SourceSong }) {
  // Bài vừa tải chưa có bản tempo: đặt sẵn 4.5× nhưng vẫn chờ người dùng bấm
  // Xuất. Dải thanh trượt tiếp tục giữ đúng 0.2×–5× như thiết kế ban đầu.
  const initial = clampTempo(source.tempo_ready ? source.tempo_factor : DEFAULT_COVER_TEMPO);
  const [tempo, setTempo] = useState(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const exp = useExportTempo(projectId);

  useEffect(
    () => setTempo(clampTempo(source.tempo_ready ? source.tempo_factor : DEFAULT_COVER_TEMPO)),
    [source.id, source.tempo_factor, source.tempo_ready],
  );

  const saved = source.tempo_ready && Math.abs(tempo - initial) < 0.005;
  const slower = tempo < 0.995;
  const faster = tempo > 1.005;
  const description = slower
    ? `chậm hơn ${(1 / tempo).toFixed(2)} lần`
    : faster ? `nhanh hơn ${tempo.toFixed(2)} lần` : 'tốc độ gốc';

  return (
    <div className="mt-2 ml-8 rounded-lg border border-cyan-500/20 bg-cyan-950/10 p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-xs font-medium text-cyan-100">🎚 Tempo</span>
        <span className="rounded bg-cyan-600/20 px-2 py-0.5 text-xs font-semibold tabular-nums text-cyan-200">
          {tempo.toFixed(2)}×
        </span>
        <span className="text-xs text-gray-400">{description} · không đổi cao độ</span>
        {source.tempo_ready && (
          <span className="text-xs text-green-300">✓ Đã xuất {source.tempo_file_name}</span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-[auto_minmax(10rem,1fr)_auto] items-center gap-2">
        <span className="text-[11px] text-gray-500">Chậm 5×</span>
        <input
          type="range"
          min={-100}
          max={100}
          step={1}
          value={tempoToSlider(tempo)}
          disabled={exp.isPending}
          aria-label="Chỉnh tempo từ chậm 5 lần đến nhanh 5 lần"
          className="h-1.5 w-full cursor-pointer accent-cyan-500 disabled:opacity-40"
          onChange={(e) => {
            setTempo(sliderToTempo(Number(e.target.value)));
            setMsg(null);
          }}
        />
        <span className="text-[11px] text-gray-500">Nhanh 5×</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          className={btnPrimary}
          disabled={exp.isPending || saved}
          title={saved ? 'Đúng mức tempo này đã được xuất' : 'Xuất WAV giữ nguyên cao độ để đưa lên Suno'}
          onClick={() => {
            setMsg(null);
            exp.mutate({ id: source.id, tempoFactor: tempo }, {
              onSuccess: () => setMsg('Đã xuất file. Suno sẽ dùng đúng bản đã chỉnh tempo này.'),
              onError: (e) => setMsg(errText(e, 'Không xuất được file tempo')),
            });
          }}
        >
          {exp.isPending ? '⏳ Đang xuất…' : saved ? '✓ Đã xuất file tempo' : '⬇ Xuất file đã chỉnh tempo'}
        </button>
        {Math.abs(tempo - 1) > 0.005 && (
          <button className={btnSm} disabled={exp.isPending} onClick={() => setTempo(1)}>
            Về 1.00×
          </button>
        )}
        {source.tempo_ready && (
          <a className={btnSm} href={tempoAudioUrl(source.id)} download={source.tempo_file_name}>
            ⬇ Tải bản tempo
          </a>
        )}
        {msg && <span className={`text-xs ${msg.startsWith('Đã') ? 'text-green-300' : 'text-red-300'}`}>{msg}</span>}
      </div>
      {source.tempo_ready && (
        <audio controls preload="none" src={tempoAudioUrl(source.id)} className="mt-2 h-8 w-full" />
      )}
      {!source.tempo_ready && (
        <p className="mt-2 text-xs text-orange-300">
          Cần xuất bản tempo trước; Suno sẽ không nhận trực tiếp file gốc.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Nhập file cover
export function CoverImportButton({ projectId, source, label = '⬆ Nhập file cover' }:
  { projectId: number; source: SourceSong; label?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const imp = useImportCovers(projectId);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <>
      <input ref={ref} type="file" multiple accept="audio/*,.wav,.mp3,.flac,.m4a" className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (!files.length) return;
          setMsg(null);
          imp.mutate({ id: source.id, files }, {
            onSuccess: (r) => setMsg(`Đã thêm ${r.added.length} bản cover`),
            onError: (er) => setMsg(errText(er)),
          });
        }} />
      <button className={btnSm} disabled={imp.isPending} onClick={() => ref.current?.click()}
        title="Chọn 1 hoặc nhiều file bản cover (vd 2 bản Suno) của bài này — tool tự gắn tên bài, ca sĩ, lời">
        {imp.isPending ? 'Đang nhập…' : label}
      </button>
      {msg && <span className="text-xs text-gray-400">{msg}</span>}
    </>
  );
}

// ---------------------------------------------------------------- 1 bài
function SourceRow({ projectId, s, index, current, requireLyrics = false }:
  { projectId: number; s: SourceSong; index: number; current: boolean; requireLyrics?: boolean }) {
  const update = useUpdateSource(projectId);
  const retry = useRetrySource(projectId);
  const verifyLyrics = useVerifyLyrics(projectId);
  const del = useDeleteSource(projectId);
  const exclude = useExcludeSource(projectId);
  const [editQuery, setEditQuery] = useState<string | null>(null);
  const needsLyrics = s.lyric_line_count === 0;
  const [lyrics, setLyrics] = useState<string | null>(requireLyrics && needsLyrics ? '' : null);
  const [err, setErr] = useState<string | null>(null);
  const st = STATUS[s.status] ?? STATUS.failed;
  const busy = s.status === 'queued' || s.status === 'running';
  const excluded = s.status === 'excluded';
  const manual = s.status === 'manual';
  const badCovers = s.covers.filter((c) => c.qc?.status === 'error').length;
  const onErr = (e: unknown) => setErr(errText(e));

  return (
    <li className={`rounded-lg border px-3 py-2 ${
      requireLyrics && needsLyrics
        ? 'border-amber-400/60 bg-amber-500/5'
        : current ? 'border-accent/60 bg-accent/5' : 'border-white/5'
    }`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-6 text-right text-sm text-gray-500">{index + 1}.</span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm text-gray-100">
            {s.title ? <>{s.title}{s.artist && <span className="text-gray-400"> – {s.artist}</span>}</> : s.query}
            {s.has_file && s.official_source === false && (
              <span className="ml-2 text-xs text-amber-300">Remix fallback</span>
            )}
          </div>
          <div className="truncate text-xs text-gray-500">
            {manual ? <>Bài cover bạn tự nhập{s.has_file ? ' · có file bài gốc' : ''}</> : <>
            {s.title && s.query !== s.nct_url && <>Tìm: «{s.query}» · </>}
            {s.nct_url
              ? <a href={s.nct_url} target="_blank" rel="noreferrer" className="text-blue-300 hover:underline">
                  mở trên {SOURCE_LABEL[s.source_provider ?? ''] ?? hostOf(s.nct_url)} ↗</a>
              : 'chưa có link'}
            {s.quality && <> · {QUALITY[s.quality] ?? s.quality}</>}
            </>}
            {s.lyric_line_count > 0 ? <> · {s.lyric_line_count} câu lời</> : (s.nct_url || manual) ? <> · <span className="text-yellow-400">chưa có lời</span></> : null}
            {s.lyric_line_count > 0 && s.lyrics_source && (
              <> · <span className="text-sky-300"
                title={`Nguồn/đối chiếu lyrics:\n${s.lyrics_source}`}>
                {s.lyrics_source.startsWith('Đã đối chiếu:') ? '✅ lyrics đã đối chiếu' : '🌐 lyrics từ nhà cung cấp'}
                {s.lyrics_source.startsWith('http') && <> (<a href={s.lyrics_source} target="_blank" rel="noreferrer"
                  className="hover:underline">{hostOf(s.lyrics_source)} ↗</a>)</>}
              </span></>
            )}
          </div>
        </div>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${st.cls}`}>{st.label}</span>
      </div>

      {s.error && !busy && (
        <p className={`mt-1 pl-8 text-xs ${s.error.startsWith('⚠') ? 'text-amber-300' : 'text-red-300'}`}>
          {s.error}</p>)}

      {s.has_file && (
        <audio controls preload="none" src={sourceAudioUrl(s.id)} className="mt-2 ml-8 h-8 w-[calc(100%-2rem)]" />
      )}

      {s.has_file && <TempoControls projectId={projectId} source={s} />}

      {editQuery !== null && (
        <div className="mt-2 flex gap-2 pl-8">
          <input className={input} value={editQuery} onChange={(e) => setEditQuery(e.target.value)}
            placeholder="Tên bài – Ca sĩ, hoặc dán link bài nhạc" />
          <button className={btnPrimary} disabled={update.isPending || !editQuery.trim()}
            onClick={() => update.mutate({ id: s.id, query: editQuery }, {
              onSuccess: () => setEditQuery(null), onError: onErr })}>Tìm lại</button>
          <button className={btn} onClick={() => setEditQuery(null)}>Huỷ</button>
        </div>
      )}

      {lyrics !== null && (
        <div className="mt-2 space-y-2 pl-8">
          <textarea className={`${input} h-56 font-mono text-xs`} value={lyrics}
            onChange={(e) => setLyrics(e.target.value)} placeholder="Mỗi câu 1 dòng" />
          <div className="flex gap-2">
            <button className={btnPrimary} disabled={update.isPending}
              onClick={() => {
                // Bài Suno đã từ chối lyrics: lời thay thế phải là nội dung được phép.
                const rejected = s.suno_status === 'rejected';
                if (rejected && !window.confirm('Suno đã từ chối lyrics cũ của bài này.\nXác nhận lyrics mới là nội dung bạn tự sáng tác hoặc có quyền sử dụng?')) return;
                update.mutate({ id: s.id, lyrics_text: lyrics.normalize('NFC'),
                                authorized_lyrics: rejected || undefined }, {
                  onSuccess: () => setLyrics(null), onError: onErr });
              }}>Lưu lời</button>
            <button className={btn} onClick={() => setLyrics(null)}>Đóng</button>
          </div>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 pl-8">
        {!busy && !manual && s.status !== 'done' && (
          <button className={btnSm} disabled={retry.isPending}
            onClick={() => retry.mutate(s.id, { onError: onErr })}>↻ Thử lại</button>
        )}
        {!busy && !manual && s.status === 'done' && s.lyric_line_count === 0 && (
          <button className={btnSm} disabled={retry.isPending}
            title="Tool ưu tiên Zing MP3 (dấu ba chấm → Lời bài hát), rồi đối chiếu LRCLIB, NhacCuaTui và Google"
            onClick={() => retry.mutate(s.id, { onError: onErr })}>🔎 Tìm lời Zing MP3</button>
        )}
        {!busy && !manual && editQuery === null && (
          <button className={btnSm} onClick={() => setEditQuery(s.query)}
            title="Tool chỉ nhận bản official gốc. Gõ lại rõ tên + ca sĩ hoặc dán link bản official NhacCuaTui">✏️ Tìm bài khác</button>
        )}
        {lyrics === null && (
          <button className={btnSm} onClick={() => setLyrics(s.lyrics_text)}>
            📝 {s.lyric_line_count > 0 ? 'Xem/sửa lời' : 'Dán lời'}
          </button>
        )}
        {!busy && !manual && s.lyric_line_count > 0 && (
          <button className={btnSm} disabled={verifyLyrics.isPending}
            title="Ưu tiên Zing MP3, rồi đối chiếu NhacCuaTui + LRCLIB + Google/website lyrics. Nếu các nguồn không khớp, tool dừng để bạn kiểm tra thủ công."
            onClick={() => {
              if (window.confirm('Tìm lại và đối chiếu lyrics? Nếu các nguồn không khớp, lyrics hiện tại sẽ được xoá để tránh đưa lời sai vào Suno.')) {
                verifyLyrics.mutate(s.id, { onError: onErr });
              }
            }}>
            {verifyLyrics.isPending ? '⏳ Đang đối chiếu…' : '✅ Đối chiếu lại lyrics'}
          </button>
        )}
        <CoverImportButton projectId={projectId} source={s} />
        {s.covers.length > 0 && (
          <span className="text-xs text-green-300" title={s.covers.map((c) => c.filename).join('\n')}>
            🎤 {s.covers.length} bản cover
          </span>
        )}
        {badCovers > 0 && (
          <span className="rounded bg-red-600 px-1.5 py-0.5 text-xs font-semibold text-white"
            title="Xem chi tiết và tạo lại ở tab ② Suno Cover">🔴 {badCovers} bản lỗi</span>
        )}
        {s.suno_status === 'redo' && (
          <span className="text-xs text-orange-300">⏳ chờ tạo lại trên Suno</span>
        )}
        {(s.suno_status === 'queued' || s.suno_status === 'running') && (
          <span className="text-xs text-blue-300">🎛 đang làm trên Suno</span>
        )}
        {s.suno_status === 'rejected' && (
          <span className="text-xs text-red-300" title="Xem ở tab ② Suno Cover">⛔ Suno từ chối</span>
        )}
        <span className="flex-1" />
        {excluded ? (
          <button className={`${btnSm} text-emerald-300`} disabled={exclude.isPending}
            onClick={() => exclude.mutate({ id: s.id, restore: true }, { onError: onErr })}>
            ↩ Khôi phục bài
          </button>
        ) : s.covers.length > 0 && !busy ? (
          <button className={`${btnSm} hover:bg-red-600/40`} disabled={exclude.isPending}
            title="Loại bài và mọi bản cover khỏi Mix; file được giữ trong thùng rác để khôi phục"
            onClick={() => {
              if (window.confirm(`Loại «${s.title || s.query}» và ${s.covers.length} bản cover khỏi Mix Set?\nCó thể khôi phục lại sau.`))
                exclude.mutate({ id: s.id }, { onError: onErr });
            }}>🚫 Loại khỏi Mix Set</button>
        ) : null}
        {!busy && (
          <button className={`${btnSm} hover:bg-red-600/40`} disabled={del.isPending}
            onClick={() => {
              if (window.confirm(`Xoá «${s.title || s.query}» khỏi danh sách (xoá cả file nhạc gốc đã tải)?`
                + (s.covers.length ? '\nCác bản cover đã nhập vẫn được giữ.' : '')))
                del.mutate(s.id, { onError: onErr });
            }}>🗑 Xoá</button>
        )}
      </div>
      {err && <p className="mt-1 pl-8 text-xs text-red-400">{err}</p>}
    </li>
  );
}

// ---------------------------------------------------------------- Panel
export default function SourcePanel({ projectId, onGoToLyrics, requireLyrics = false }:
  { projectId: number; onGoToLyrics: () => void; requireLyrics?: boolean }) {
  const { data, isLoading, isError, error } = useSources(projectId);
  const add = useAddSources(projectId);
  const startAll = useStartAllSources(projectId);
  const stop = useStopSources(projectId);
  const makeLyrics = useMakeLyrics(projectId);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [lyricsMsg, setLyricsMsg] = useState<string | null>(null);

  const items = data?.items ?? [];
  const worker = data?.worker;
  const downloading = worker?.mode === 'download' || (worker?.busy && worker?.mode !== 'login');
  const coverCount = items.reduce((n, s) => n + s.covers.length, 0);
  const notDone = items.filter((s) => ![
    'done', 'queued', 'running', 'manual', 'skipped_no_lyrics', 'excluded',
  ].includes(s.status)).length;
  const needLogin = items.some((s) => s.status === 'login');
  const missingLyricsCount = items.filter((s) => s.lyric_line_count === 0).length;

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="space-y-4">
        <div className={card}>
          <h2 className={h2}>① Tìm &amp; tải nhạc gốc đa nguồn</h2>
          {isError && (
            <p className="mb-3 rounded-lg bg-red-600/20 p-2 text-sm text-red-200">{errText(error)}</p>
          )}
          <NctLoginPanel worker={worker} />
          <label className="mt-4 block text-sm text-gray-300">
            Mỗi dòng 1 bài: <b>Tên bài – Ca sĩ</b> (hoặc dán link NhacCuaTui)
          </label>
          <textarea className={`${input} mt-1 h-36`} value={text} onChange={(e) => setText(e.target.value)}
            placeholder={'Nơi này có anh – Sơn Tùng M-TP\nHẹn yêu – Minh Vương M4U\nhttps://www.nhaccuatui.com/song/...'} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button className={btnPrimary} disabled={add.isPending || !text.trim()}
              onClick={() => {
                setErr(null);
                add.mutate(text, { onSuccess: () => setText(''), onError: (e) => setErr(errText(e)) });
              }}>
              🔎 Tìm &amp; tải
            </button>
            {downloading && (
              <button className={btn} disabled={stop.isPending} onClick={() => stop.mutate()}>⏹ Dừng</button>
            )}
          </div>
          {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
          {worker?.message && worker.mode !== 'login' && (
            <p className={`mt-2 text-xs ${worker.busy ? 'text-blue-300' : 'text-gray-400'}`}>
              {worker.busy && '⏳ '}{worker.message}
            </p>
          )}
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-gray-500">
            <li>Nguồn tải theo thứ tự <b>NhacCuaTui → Zing MP3 → YouTube</b>; file nguồn
              dự phòng được chuẩn hoá thành MP3 bằng FFmpeg.</li>
            <li>Tool ưu tiên <b>bản official/original</b>. Chỉ khi không tìm thấy official
              mới dùng bản remix phù hợp; karaoke/instrumental/live sai bài vẫn bị loại.</li>
            <li>Lyrics ưu tiên lấy trên <b>Zing MP3</b> bằng menu <b>Khác → Lời bài hát</b>,
              rồi đối chiếu với NhacCuaTui, LRCLIB và Google/website lyrics.</li>
            <li>Tool chọn nhầm bài → bấm <b>✏️ Tìm bài khác</b> và dán thẳng link bài đúng.</li>
            {data?.folder && <li>File lưu ở: <code className="break-all text-gray-400">{data.folder}</code></li>}
          </ul>
        </div>

        <div className={card}>
          <h2 className={h2}>Bước tiếp theo</h2>
          <p className="text-sm text-gray-300">
            Có bản cover rồi (làm trên Suno, bấm <b>⬆ Nhập file cover</b> ở từng bài) → bấm nút dưới đây.
            Tool điền sẵn tên bài, ca sĩ, lời cho từng bản cover và cho AI căn lời theo nhạc.
          </p>
          <button className={`${btnPrimary} mt-3`} disabled={makeLyrics.isPending || coverCount === 0}
            title={coverCount === 0 ? 'Chưa có bản cover nào — nhập file cover trước' : ''}
            onClick={() => {
              setLyricsMsg(null);
              makeLyrics.mutate(undefined, {
                onSuccess: () => onGoToLyrics(),
                onError: (e) => setLyricsMsg(errText(e)),
              });
            }}>
            🎤 Tạo lời bài hát →
          </button>
          <span className="ml-2 text-xs text-gray-500">{coverCount} bản cover</span>
          {lyricsMsg && <p className="mt-2 text-xs text-red-400">{lyricsMsg}</p>}
        </div>
      </div>

      <div className={card}>
        <div className="mb-3 flex items-center gap-2">
          <h2 className={`${h2} mb-0 flex-1`}>Bài gốc của project ({items.length})</h2>
          {notDone > 0 && !downloading && (
            <button className={btnSm} disabled={startAll.isPending} onClick={() => startAll.mutate()}>
              ↻ Tải lại {notDone} bài chưa xong
            </button>
          )}
        </div>
        {needLogin && (
          <p className="mb-3 rounded-lg bg-orange-600/15 p-2 text-xs text-orange-200">
            🔑 Có bài cần đăng nhập nhaccuatui mới tải được file (lời và link đã lấy xong). Bấm
            <b> Mở nhaccuatui để đăng nhập</b> ở bên trái, đăng nhập, đóng cửa sổ rồi bấm <b>↻ Thử lại</b>.
          </p>
        )}
        {requireLyrics && missingLyricsCount > 0 && (
          <p className="mb-3 rounded-lg border border-amber-400/30 bg-amber-500/10 p-2 text-xs text-amber-100">
            ⛔ Mix Set đang dừng vì còn <b>{missingLyricsCount} bài chưa có lyrics</b>.
            Các ô nhập của bài thiếu lời đã được mở bên dưới. Dán lời, bấm <b>Lưu lời</b>
            cho từng bài, sau đó quay lại Projects để bấm <b>▶ Tiếp tục Mix Set</b>.
          </p>
        )}
        {requireLyrics && items.length > 0 && missingLyricsCount === 0 && (
          <p className="mb-3 rounded-lg border border-green-400/30 bg-green-500/10 p-2 text-xs text-green-200">
            ✅ Tất cả bài đã có lyrics. Quay lại Projects và bấm <b>▶ Tiếp tục Mix Set</b>.
          </p>
        )}
        {isLoading && <p className="text-sm text-gray-400">Đang tải…</p>}
        {!isLoading && items.length === 0 && (
          <p className="text-sm text-gray-400">Chưa có bài nào. Gõ tên bài – ca sĩ ở bên trái rồi bấm <b>Tìm &amp; tải</b>.</p>
        )}
        <ol className="space-y-2">
          {items.map((s, i) => (
            <SourceRow key={s.id} projectId={projectId} s={s} index={i}
              current={worker?.current === s.id} requireLyrics={requireLyrics} />
          ))}
        </ol>
      </div>
    </div>
  );
}
