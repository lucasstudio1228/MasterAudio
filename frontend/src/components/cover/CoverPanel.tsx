import { useEffect, useMemo, useRef, useState } from 'react';
import { useOpenProjectFolder, useProjectFolders } from '../../api/projects';
import {
  songAudioUrl,
  songLrcUrl,
  lyricsSrtUrl,
  useAlignAll,
  useAlignSong,
  useCoverSettings,
  useCoverSongs,
  useReorderSongs,
  useSaveLines,
  useShiftLines,
  useUpdateCoverSettings,
  useUpdateSong,
  type AlignStatus,
  type CoverSettings,
  type CoverSong,
  type CoverStyle,
  type LyricLine,
} from '../../api/cover';

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';
const input = 'w-full rounded-lg border border-white/10 bg-surface-2 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-accent';

const STATUS: Record<AlignStatus, { label: string; cls: string }> = {
  none: { label: 'Chưa căn lời', cls: 'bg-gray-600/40 text-gray-300' },
  queued: { label: 'Đang chờ', cls: 'bg-yellow-600/30 text-yellow-300' },
  running: { label: 'AI đang căn…', cls: 'bg-blue-600/30 text-blue-300 animate-pulse' },
  done: { label: 'Đã căn', cls: 'bg-green-600/30 text-green-300' },
  edited: { label: 'Đã sửa tay', cls: 'bg-emerald-600/30 text-emerald-300' },
  failed: { label: 'Lỗi', cls: 'bg-red-600/30 text-red-300' },
};

function fmt(t: number) {
  if (!Number.isFinite(t)) return '0:00.0';
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

function parseTime(v: string): number | null {
  const s = v.trim();
  const m = s.match(/^(\d+):(\d+(?:\.\d+)?)$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

const LANG_LABEL: Record<string, string> = {
  vi: '🇻🇳 Tiếng Việt (tiêu đề, mô tả, lời đều tiếng Việt)',
  en: '🇺🇸 English (everything in English)',
};
const STYLE_LABEL: Record<CoverStyle, string> = {
  ballad: 'Ballad (nhẹ nhàng, chậm)',
  remix: 'Remix (sôi động)',
  acoustic: 'Acoustic (mộc)',
  original: 'Giữ nguyên phong cách',
};
const short = (s: string) => s.split(' (')[0];

// ---------------------------------------------------------------- Cài đặt
function SettingsCard({ projectId }: { projectId: number }) {
  const { data } = useCoverSettings(projectId);
  const update = useUpdateCoverSettings(projectId);
  const [subtitle, setSubtitle] = useState('');
  useEffect(() => setSubtitle(data?.thumb_subtitle ?? ''), [data?.thumb_subtitle]);
  if (!data) return <div className={card}>Đang tải…</div>;
  const set = (patch: Partial<CoverSettings>) => update.mutate(patch);
  const eff = data.effective;
  const manual = [data.language, data.cover_style, data.thumb_subtitle.trim()].filter(Boolean).length;

  return (
    <div className={card}>
      <h2 className={h2}>Cài đặt video cover</h2>
      {eff && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-accent/10 px-3 py-2 text-xs text-gray-300">
          <span>
            🤖 Tự theo thể loại project: <b className="text-gray-100">{eff.reason}</b>
            {' → '}{short(LANG_LABEL[eff.language])} · {short(STYLE_LABEL[eff.cover_style])}
          </span>
          <span className="flex-1" />
          {manual > 0 && (
            <button className={btn} disabled={update.isPending}
              title="Bỏ các lựa chọn tay — để tool tự đặt theo thể loại (tiện chạy tự động)"
              onClick={() => set({ language: '', cover_style: '', thumb_subtitle: '' })}>
              ↺ Đặt lại theo thể loại
            </button>
          )}
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm text-gray-300">
          <span>Ngôn ngữ của video</span>
          <select className={input} value={data.language}
            onChange={(e) => set({ language: e.target.value as CoverSettings['language'] })}>
            <option value="">
              🤖 Theo dòng nhạc{eff ? ` — ${short(LANG_LABEL[eff.suggested.language])}` : ''}
            </option>
            <option value="vi">{LANG_LABEL.vi}</option>
            <option value="en">{LANG_LABEL.en}</option>
          </select>
        </label>
        <label className="space-y-1 text-sm text-gray-300">
          <span>Phong cách cover</span>
          <select className={input} value={data.cover_style}
            onChange={(e) => set({ cover_style: e.target.value as CoverSettings['cover_style'] })}>
            <option value="">
              🤖 Theo thể loại{eff ? ` — ${short(STYLE_LABEL[eff.suggested.cover_style])}` : ''}
            </option>
            {(Object.keys(STYLE_LABEL) as CoverStyle[]).map((k) => (
              <option key={k} value={k}>{STYLE_LABEL[k]}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm text-gray-300 md:col-span-2">
          <span>Dòng chữ phụ trên thumbnail (để trống = tool tự đặt theo thể loại)</span>
          <input className={input} value={subtitle}
            placeholder={eff ? `🤖 Tự đặt: ${eff.thumb_subtitle}` : 'VD: Tuyển Tập Ballad Cover Nhẹ Nhàng'}
            onChange={(e) => setSubtitle(e.target.value)}
            onBlur={() => subtitle !== data.thumb_subtitle && set({ thumb_subtitle: subtitle })} />
        </label>
        <label className="space-y-1 text-sm text-gray-300">
          <span>Độ chính xác AI căn lời</span>
          <select className={input} value={data.whisper_model}
            onChange={(e) => set({ whisper_model: e.target.value })}>
            <option value="large-v3">Cao nhất (chậm hơn) — khuyên dùng</option>
            <option value="large-v3-turbo">Nhanh, gần bằng cao nhất</option>
            <option value="medium">Trung bình</option>
            <option value="small">Nhanh nhất (kém chính xác)</option>
          </select>
        </label>
        <div className="space-y-1.5 text-sm text-gray-300">
          {([
            ['show_tracklist', 'Hiện danh sách bài (góc phải)'],
            ['show_lyrics', 'Hiện lời bài hát (3 dòng dưới)'],
            ['show_waveform', 'Hiện sóng âm nhảy theo nhạc (dưới lời)'],
          ] as const).map(([k, label]) => (
            <label key={k} className="flex items-center gap-2">
              <input type="checkbox" checked={data[k]} onChange={(e) => set({ [k]: e.target.checked })} />
              {label}
            </label>
          ))}
        </div>
      </div>
      {update.isError && <p className="mt-2 text-xs text-red-400">Không lưu được cài đặt.</p>}
    </div>
  );
}

// ---------------------------------------------------------------- Danh sách bài
/** Nút mở thư mục audio đã xong trên máy (File Explorer) để nghe. */
function OpenAudioFolderButton({ projectId }: { projectId: number }) {
  const { data } = useProjectFolders(projectId);
  const open = useOpenProjectFolder(projectId);
  if (!data?.audio) return null;
  return (
    <button className={btnPrimary} disabled={open.isPending}
      onClick={() => open.mutate('audio')}
      title={`Mở thư mục trên máy tính để nghe: ${data.audio}`}>
      📂 Mở thư mục nghe audio
    </button>
  );
}

function SongList({ projectId, songs, selected, onSelect, alignMsg, busy }: {
  projectId: number; songs: CoverSong[]; selected: number | null;
  onSelect: (id: number) => void; alignMsg: string; busy: boolean;
}) {
  const reorder = useReorderSongs(projectId);
  const alignAll = useAlignAll(projectId);
  const move = (i: number, d: number) => {
    const ids = songs.map((s) => s.id);
    const j = i + d;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids);
  };
  const missing = songs.filter((s) => s.align_status === 'none' || s.align_status === 'failed').length;

  return (
    <div className={card}>
      <h2 className={h2}>Danh sách bài · thứ tự phát ({songs.length})</h2>
      {songs.length === 0 && (
        <p className="text-sm text-gray-400">
          Chưa có bài nào. Sang tab <b>① Tải nhạc gốc</b> hoặc <b>② Suno Cover</b>, bấm
          <b> ⬆ Nhập file cover</b> ở từng bài trước.
        </p>
      )}
      <ol className="space-y-1">
        {songs.map((s, i) => {
          const st = STATUS[s.align_status] ?? STATUS.none;
          return (
            <li key={s.id}
              className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm ${
                selected === s.id ? 'bg-accent/20 ring-1 ring-accent' : 'hover:bg-white/5'}`}>
              <div className="flex flex-col">
                <button className="text-xs text-gray-400 hover:text-white disabled:opacity-20"
                  disabled={i === 0 || reorder.isPending} onClick={() => move(i, -1)} title="Lên">▲</button>
                <button className="text-xs text-gray-400 hover:text-white disabled:opacity-20"
                  disabled={i === songs.length - 1 || reorder.isPending} onClick={() => move(i, 1)} title="Xuống">▼</button>
              </div>
              <span className="w-6 text-right text-gray-500">{i + 1}.</span>
              <button className="min-w-0 flex-1 text-left" onClick={() => onSelect(s.id)}>
                <div className="truncate text-gray-100">{s.title}</div>
                <div className="truncate text-xs text-gray-500">
                  {s.artist || 'chưa ghi ca sĩ gốc'} · {fmt(s.duration_seconds).split('.')[0]}
                  {s.lines.length > 0 && ` · ${s.lines.length} câu`}
                  {s.quality && !s.quality.ok && s.lines.length > 0 &&
                    <span className="text-yellow-400"> · {s.quality.suspicious} câu nghi lệch</span>}
                </div>
              </button>
              <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${st.cls}`}
                title={s.align_error ?? ''}>{st.label}</span>
            </li>
          );
        })}
      </ol>
      {songs.length > 0 && (
        <div className="mt-3 space-y-2 border-t border-white/5 pt-3">
          <div className="flex flex-wrap gap-2">
            <button className={btnPrimary} disabled={alignAll.isPending || missing === 0}
              onClick={() => alignAll.mutate(true)}>
              🤖 AI căn lời {missing > 0 ? `${missing} bài chưa căn` : '(đã căn hết)'}
            </button>
            <button className={btn} disabled={alignAll.isPending}
              onClick={() => {
                if (window.confirm('Căn lại TẤT CẢ bài? Những chỗ bạn đã sửa tay sẽ bị ghi đè.'))
                  alignAll.mutate(false);
              }}>Căn lại tất cả</button>
            <a className={btn} href={lyricsSrtUrl(projectId)}
              title="File phụ đề chỉ có lời bài hát, chạy theo từng câu nhạc của bản Mix mới nhất — kéo thả vào CapCut">
              ⬇ Lời cho CapCut (.srt)
            </a>
            <OpenAudioFolderButton projectId={projectId} />
          </div>
          {(busy || alignMsg) && (
            <p className={`text-xs ${busy ? 'text-blue-300' : 'text-gray-400'}`}>
              {busy && '⏳ '}{alignMsg}
            </p>
          )}
          <p className="text-xs text-gray-500">
            Mẹo: dán lời đúng của bài trước khi căn → AI chỉ cần dò thời gian, chính xác hơn nhiều.
            Mỗi bài mất khoảng 1–3 phút.
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Sửa 1 bài
function SongEditor({ projectId, song, busy }: { projectId: number; song: CoverSong; busy: boolean }) {
  const updateSong = useUpdateSong(projectId);
  const saveLines = useSaveLines(projectId);
  const shift = useShiftLines(projectId);
  const align = useAlignSong(projectId);

  const [title, setTitle] = useState(song.title);
  const [artist, setArtist] = useState(song.artist);
  const [lyrics, setLyrics] = useState(song.lyrics_text);
  const [lines, setLines] = useState<LyricLine[]>(song.lines);
  const [dirty, setDirty] = useState(false);
  const [now, setNow] = useState(0);
  const audio = useRef<HTMLAudioElement>(null);
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);

  // Đổi bài, hoặc server trả kết quả mới (AI căn xong) → nạp lại.
  useEffect(() => {
    setTitle(song.title);
    setArtist(song.artist);
    setLyrics(song.lyrics_text);
  }, [song.id, song.title, song.artist, song.lyrics_text]);
  const linesKey = JSON.stringify(song.lines);
  useEffect(() => {
    setLines(song.lines);
    setDirty(false);
  }, [song.id, linesKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = useMemo(
    () => lines.findIndex((l) => now >= l.start && now < l.end), [lines, now]);
  useEffect(() => {
    if (current >= 0 && !audio.current?.paused)
      rowRefs.current[current]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [current]);

  const seek = (t: number, play = true) => {
    const a = audio.current;
    if (!a) return;
    a.currentTime = Math.max(t - 0.5, 0);
    if (play) void a.play();
  };
  const edit = (i: number, patch: Partial<LyricLine>) => {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
    setDirty(true);
  };
  const stampStart = (i: number) => {
    const t = Math.round((audio.current?.currentTime ?? 0) * 100) / 100;
    edit(i, { start: t, end: Math.max(lines[i].end, t + 0.5) });
  };

  const infoDirty = title !== song.title || artist !== song.artist;
  const lyricsDirty = lyrics !== song.lyrics_text;
  const isWorking = song.align_status === 'queued' || song.align_status === 'running';

  return (
    <div className={`${card} space-y-4`}>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm text-gray-300">
          <span>Tên bài (hiện trên video &amp; mô tả YouTube)</span>
          <input className={input} value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="space-y-1 text-sm text-gray-300">
          <span>Ca sĩ / tác giả gốc (không bắt buộc)</span>
          <input className={input} value={artist} onChange={(e) => setArtist(e.target.value)} />
        </label>
      </div>
      {infoDirty && (
        <button className={btnPrimary} disabled={updateSong.isPending}
          onClick={() => updateSong.mutate({ id: song.id, title, artist })}>Lưu tên bài</button>
      )}

      <audio ref={audio} src={songAudioUrl(song.id, String(song.tempo_factor ?? ''))} controls preload="metadata" className="w-full"
        onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        {/* Lời gốc */}
        <div className="space-y-2 xl:col-span-2">
          <div className="text-sm text-gray-300">
            Lời bài hát — mỗi câu 1 dòng
            <span className="text-xs text-gray-500"> ({song.lyric_line_count} dòng · có thể dán cả file .lrc)</span>
          </div>
          <textarea className={`${input} h-80 font-mono text-xs leading-5`} value={lyrics}
            placeholder={'Dán lời bài hát vào đây…\nMỗi câu hát 1 dòng.\nĐể trống thì AI tự nghe và chép lời (kém chính xác hơn).'}
            onChange={(e) => setLyrics(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button className={btn} disabled={!lyricsDirty || updateSong.isPending}
              onClick={() => updateSong.mutate({ id: song.id, lyrics_text: lyrics })}>
              Lưu lời
            </button>
            <button className={btnPrimary} disabled={isWorking || align.isPending || updateSong.isPending}
              onClick={async () => {
                if (lines.length && !window.confirm('Căn lại bài này? Mốc thời gian hiện tại sẽ bị thay.')) return;
                if (lyricsDirty) await updateSong.mutateAsync({ id: song.id, lyrics_text: lyrics });
                align.mutate(song.id);
              }}>
              {isWorking ? '⏳ AI đang căn…' : '🤖 AI căn lời bài này'}
            </button>
          </div>
          {song.align_status === 'failed' && song.align_error && (
            <p className="text-xs text-red-400">Lỗi: {song.align_error}</p>
          )}
          {busy && !isWorking && <p className="text-xs text-gray-500">AI đang bận bài khác, bài này sẽ xếp hàng.</p>}
        </div>

        {/* Bảng mốc thời gian */}
        <div className="space-y-2 xl:col-span-3">
          <div className="flex flex-wrap items-center gap-2 text-sm text-gray-300">
            <span>Rà lời theo nhạc ({lines.length} câu)</span>
            <span className="flex-1" />
            <span className="text-xs text-gray-500">Cả bài lệch đều:</span>
            {[-0.5, -0.2, 0.2, 0.5].map((d) => (
              <button key={d} className={btn} disabled={!lines.length || dirty || shift.isPending}
                title={d < 0 ? 'Lời hiện SỚM hơn' : 'Lời hiện MUỘN hơn'}
                onClick={() => shift.mutate({ id: song.id, seconds: d })}>
                {d > 0 ? `+${d}` : d}s
              </button>
            ))}
            {lines.length > 0 && <a className={btn} href={songLrcUrl(song.id)} target="_blank" rel="noreferrer">⬇ .lrc</a>}
          </div>
          {lines.length === 0 ? (
            <p className="rounded-lg bg-surface-2 p-4 text-sm text-gray-400">
              Chưa có mốc thời gian. Dán lời rồi bấm <b>🤖 AI căn lời bài này</b>.
            </p>
          ) : (
            <div className="max-h-96 overflow-y-auto rounded-lg border border-white/5">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-surface-2 text-xs text-gray-400">
                  <tr><th className="w-8 p-1" /><th className="w-20 p-1">Bắt đầu</th><th className="w-20 p-1">Kết thúc</th><th className="p-1 text-left">Câu hát</th><th className="w-16 p-1" /></tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => {
                    const bad = l.end <= l.start || (i > 0 && l.start < lines[i - 1].start);
                    return (
                      <tr key={i} ref={(el) => { rowRefs.current[i] = el; }}
                        className={i === current ? 'bg-accent/25' : bad ? 'bg-red-900/30' : 'odd:bg-white/[0.02]'}>
                        <td className="p-1 text-center">
                          <button title="Nghe từ câu này" className="text-gray-400 hover:text-white"
                            onClick={() => seek(l.start)}>▶</button>
                        </td>
                        <td className="p-1">
                          <TimeInput value={l.start} onChange={(v) => edit(i, { start: v })} />
                        </td>
                        <td className="p-1">
                          <TimeInput value={l.end} onChange={(v) => edit(i, { end: v })} />
                        </td>
                        <td className="p-1">
                          <input className="w-full bg-transparent px-1 text-gray-100 outline-none focus:bg-surface-2"
                            value={l.text} onChange={(e) => edit(i, { text: e.target.value })} />
                        </td>
                        <td className="p-1 text-center">
                          <button title="Đặt giờ bắt đầu = vị trí đang nghe" className="text-xs text-gray-400 hover:text-white"
                            onClick={() => stampStart(i)}>⏱ đây</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {lines.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <button className={btnPrimary} disabled={!dirty || saveLines.isPending}
                onClick={() => saveLines.mutate({ id: song.id, lines })}>
                {saveLines.isPending ? 'Đang lưu…' : 'Lưu mốc thời gian'}
              </button>
              {dirty && <button className={btn} onClick={() => { setLines(song.lines); setDirty(false); }}>Huỷ sửa</button>}
              <span className="text-xs text-gray-500">
                Bấm ▶ để nghe từ câu đó · đang phát câu nào sẽ tô sáng · "⏱ đây" = lấy giờ đang nghe.
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TimeInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(fmt(value));
  useEffect(() => setText(fmt(value)), [value]);
  return (
    <input className="w-20 bg-transparent px-1 text-center font-mono text-xs text-gray-200 outline-none focus:bg-surface-2"
      value={text} onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const v = parseTime(text);
        if (v === null || v < 0) setText(fmt(value));
        else if (Math.abs(v - value) > 0.001) onChange(Math.round(v * 100) / 100);
      }} />
  );
}

// ---------------------------------------------------------------- Panel
export default function CoverPanel({ projectId }: { projectId: number }) {
  const { data, isLoading } = useCoverSongs(projectId);
  const songs = data?.songs ?? [];
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    if (songs.length && !songs.some((s) => s.id === selected)) setSelected(songs[0].id);
  }, [songs, selected]);
  const song = songs.find((s) => s.id === selected) ?? null;

  if (isLoading) return <p className="text-gray-400">Đang tải…</p>;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SongList projectId={projectId} songs={songs} selected={selected} onSelect={setSelected}
          alignMsg={data?.align.message ?? ''} busy={!!data?.align.busy} />
        <SettingsCard projectId={projectId} />
      </div>
      {song && <SongEditor key={song.id} projectId={projectId} song={song} busy={!!data?.align.busy} />}
    </div>
  );
}
