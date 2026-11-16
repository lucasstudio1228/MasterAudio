import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { errText, useOwnCover } from '../../api/sources';

// «📂 Kéo thả bài cover có sẵn»: thả nhiều file nhạc cover (+ file lời .txt/.lrc) cùng lúc.
// Mỗi bài → tool tự master (đồng bộ âm lượng về cùng mức LUFS) và AI căn lời để xuất .srt.

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btnSm = 'rounded-md bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';
const input = 'w-full rounded-lg border border-white/10 bg-surface-2 px-2 py-1 text-sm text-gray-100 outline-none focus:border-accent';

const AUDIO_EXT = ['.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.aiff', '.aif'];
const LYRIC_EXT = ['.txt', '.lrc'];
const ext = (name: string) => {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
};
const base = (name: string) => {
  const i = name.lastIndexOf('.');
  return i < 0 ? name : name.slice(0, i);
};
/** Tên file → khoá ghép bài: bỏ đuôi phiên bản « (1)», « v2», «_2», «-master»… */
const keyOf = (name: string) =>
  base(name)
    .replace(/[_]+/g, ' ')
    .replace(/\s*[([]\s*\d+\s*[)\]]\s*$/, '')
    .replace(/\s*(?:[-_ ]\s*)?(?:v|ver|version|bản)\s*\d+\s*$/i, '')
    .replace(/\s*[-_ ]\s*(?:master(?:ed)?|final|cover)\s*$/i, '')
    .replace(/\s+\d$/, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .normalize('NFC');
/** «Tên bài - Ca sĩ» trong tên file → tên bài + ca sĩ. */
function splitName(name: string): { title: string; artist: string } {
  const clean = base(name)
    .replace(/[_]+/g, ' ')
    .replace(/\s*[([]\s*\d+\s*[)\]]\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim()
    .normalize('NFC');
  const m = clean.match(/^(.+?)\s[-–—]\s(.+)$/);
  return m ? { title: m[1].trim(), artist: m[2].trim() } : { title: clean, artist: '' };
}

type Row = {
  key: string;
  title: string;
  artist: string;
  lyrics: string;
  lyricFile: string;
  files: File[];
  state: 'ready' | 'uploading' | 'done' | 'error';
  note: string;
};

export default function CoverDropPanel({ projectId }: { projectId: number }) {
  const own = useOwnCover(projectId);
  const [params] = useSearchParams();
  const focus = params.get('drop') === '1';
  const boxRef = useRef<HTMLDivElement>(null);
  const pickRef = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [over, setOver] = useState(false);
  const [autoMaster, setAutoMaster] = useState(true);
  const [autoAlign, setAutoAlign] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    if (focus) boxRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focus]);

  async function addFiles(list: FileList | File[] | null) {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    const audios = files.filter((f) => AUDIO_EXT.includes(ext(f.name)));
    const lyricFiles = files.filter((f) => LYRIC_EXT.includes(ext(f.name)));
    const skipped = files.length - audios.length - lyricFiles.length;
    const texts = await Promise.all(lyricFiles.map(async (f) => ({ f, text: await f.text() })));
    setRows((prev) => {
      const next = prev.map((r) => ({ ...r, files: [...r.files] }));
      for (const f of audios) {
        const k = keyOf(f.name);
        const hit = next.find((r) => r.key === k && r.state !== 'done');
        if (hit) {
          if (!hit.files.some((x) => x.name === f.name && x.size === f.size)) hit.files.push(f);
        } else {
          const { title, artist } = splitName(f.name);
          next.push({ key: k, title, artist, lyrics: '', lyricFile: '', files: [f], state: 'ready', note: '' });
        }
      }
      const open = next.filter((r) => r.state !== 'done');
      for (const { f, text } of texts) {
        const k = keyOf(f.name);
        const hit = open.find((r) => r.key === k)
          ?? (open.length === 1 && texts.length === 1 ? open[0] : undefined);
        if (hit) { hit.lyrics = text; hit.lyricFile = f.name; }
      }
      return next;
    });
    const unmatched = texts.filter(({ f }) => {
      const k = keyOf(f.name);
      return !rows.some((r) => r.key === k) && !audios.some((a) => keyOf(a.name) === k);
    });
    const notes: string[] = [];
    if (skipped) notes.push(`Bỏ qua ${skipped} file không phải nhạc / lời.`);
    if (unmatched.length && !(audios.length + rows.length === 1 && texts.length === 1))
      notes.push(`${unmatched.length} file lời chưa khớp tên bài nào — kéo file lời thả thẳng vào đúng bài bên dưới.`);
    setMsg(notes.join(' '));
  }

  const patch = (key: string, p: Partial<Row>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...p } : r)));

  async function uploadAll() {
    setBusy(true);
    setMsg('');
    let ok = 0;
    for (const r of rows) {
      if (r.state === 'done' || !r.title.trim() || !r.files.length) continue;
      patch(r.key, { state: 'uploading', note: '' });
      try {
        const res = await own.mutateAsync({
          title: r.title.trim(), artist: r.artist.trim(), lyrics: r.lyrics, files: r.files,
          original: null, autoMaster, autoAlign,
        });
        const parts = [`✅ ${res.added.length} bản`];
        if (res.master_queued) parts.push('đang master');
        if (res.master_error) parts.push(`master lỗi: ${res.master_error}`);
        if (res.align_queued) parts.push('đang AI căn lời');
        patch(r.key, { state: 'done', note: parts.join(' · ') });
        ok += 1;
      } catch (e) {
        patch(r.key, { state: 'error', note: errText(e) });
      }
    }
    setBusy(false);
    if (ok) setMsg(`Đã nhập ${ok} bài. Theo dõi master ở danh sách bên phải; lời đã căn xem ở tab ③ Lời bài hát → nút ⬇ .srt từng bài.`);
  }

  const pending = rows.filter((r) => r.state !== 'done');
  const ready = pending.filter((r) => r.title.trim() && r.files.length);

  return (
    <div ref={boxRef} className={`${card} ${focus ? 'ring-2 ring-accent/60' : ''}`}>
      <h2 className={h2}>📂 Kéo thả bài cover có sẵn</h2>
      <p className="text-xs text-gray-400">
        Đã có bản cover (làm trên Suno hoặc nơi khác)? Kéo các file nhạc vào đây — có thể kéo
        nhiều bài cùng lúc, kèm file lời <b>.txt / .lrc</b> cùng tên. Tên file nên đặt
        «<b>Tên bài - Ca sĩ</b>»; các bản «(1)», «(2)» của cùng 1 bài được gom chung.
      </p>

      <div
        role="button"
        tabIndex={0}
        onClick={() => pickRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter') pickRef.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); void addFiles(e.dataTransfer.files); }}
        className={`mt-3 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-3 py-6 text-center text-sm ${
          over ? 'border-accent bg-accent/10 text-white' : 'border-white/15 bg-background text-gray-400'
        }`}
      >
        <span className="text-3xl">🎵</span>
        <span className="mt-1">Kéo thả file nhạc cover (+ file lời) vào đây</span>
        <span className="text-xs text-gray-500">hoặc bấm để chọn · .wav .mp3 .flac .m4a · lời .txt .lrc</span>
      </div>
      <input ref={pickRef} type="file" multiple className="hidden"
        accept={[...AUDIO_EXT, ...LYRIC_EXT, 'audio/*'].join(',')}
        onChange={(e) => { const f = e.target.files; void addFiles(f ? Array.from(f) : null); e.target.value = ''; }} />

      {rows.length > 0 && (
        <div className="mt-3 space-y-2">
          {rows.map((r) => (
            <div key={r.key}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const f = Array.from(e.dataTransfer.files).find((x) => LYRIC_EXT.includes(ext(x.name)));
                if (f && r.state !== 'done') f.text().then((t) => patch(r.key, { lyrics: t, lyricFile: f.name }));
              }}
              className={`rounded-lg border px-3 py-2 ${
                r.state === 'done' ? 'border-emerald-500/30 bg-emerald-500/5'
                  : r.state === 'error' ? 'border-red-500/30 bg-red-500/5' : 'border-white/10 bg-background'
              }`}>
              <div className="grid grid-cols-2 gap-2">
                <input className={input} value={r.title} disabled={r.state !== 'ready' && r.state !== 'error'}
                  onChange={(e) => patch(r.key, { title: e.target.value })} placeholder="Tên bài (bắt buộc)" />
                <input className={input} value={r.artist} disabled={r.state !== 'ready' && r.state !== 'error'}
                  onChange={(e) => patch(r.key, { artist: e.target.value })} placeholder="Ca sĩ gốc" />
              </div>
              <div className="mt-1 text-xs text-gray-500">
                🎧 {r.files.map((f) => f.name).join(' · ')}
              </div>
              {r.state !== 'done' && (
                <details className="mt-1" open={!r.lyrics}>
                  <summary className="cursor-pointer text-xs text-gray-400">
                    📝 Lời bài hát {r.lyrics
                      ? `— ${r.lyricFile ? `từ ${r.lyricFile}` : 'đã dán'} (${r.lyrics.split(/\r?\n/).filter((x) => x.trim()).length} dòng)`
                      : '— chưa có (dán vào, hoặc kéo file .txt/.lrc thả vào bài này)'}
                  </summary>
                  <textarea className={`${input} mt-1 h-28 font-mono text-xs`} value={r.lyrics}
                    onChange={(e) => patch(r.key, { lyrics: e.target.value })}
                    placeholder="Dán lời vào đây, mỗi câu 1 dòng (không cần ghi tên bài / ca sĩ)" />
                </details>
              )}
              <div className="mt-1 flex items-center gap-2 text-xs">
                {r.state === 'uploading' && <span className="text-amber-300">⏳ Đang nhập…</span>}
                {r.note && <span className={r.state === 'error' ? 'text-red-300' : 'text-emerald-300'}>{r.note}</span>}
                <span className="flex-1" />
                {r.state !== 'done' && r.state !== 'uploading' && (
                  <button className={btnSm} disabled={busy}
                    onClick={() => setRows((prev) => prev.filter((x) => x.key !== r.key))}>✕ Bỏ</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {pending.length > 0 && (
        <div className="mt-3 space-y-1 text-xs text-gray-300">
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-0.5" checked={autoMaster}
              onChange={(e) => setAutoMaster(e.target.checked)} />
            <span><b>Master + đồng bộ âm lượng</b> — mọi bài được đưa về cùng mức âm lượng chuẩn
              (preset master đang chọn ở ô Master bên dưới).</span>
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-0.5" checked={autoAlign}
              onChange={(e) => setAutoAlign(e.target.checked)} />
            <span><b>AI căn lời ngay</b> (bài có lời) — để xuất file lời <b>.srt</b> cho CapCut ở tab ③.</span>
          </label>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {pending.length > 0 && (
          <button className={btnPrimary} disabled={busy || ready.length === 0} onClick={() => void uploadAll()}>
            {busy ? '⏳ Đang nhập…' : `⬆ Nhập ${ready.length} bài`}
          </button>
        )}
        {rows.some((r) => r.state === 'done') && !busy && (
          <button className={btnSm} onClick={() => setRows((prev) => prev.filter((r) => r.state !== 'done'))}>
            Dọn danh sách đã nhập
          </button>
        )}
      </div>
      {msg && <p className="mt-2 text-xs text-cyan-200">{msg}</p>}
    </div>
  );
}
