import { useRef, useState } from 'react';
import { errText, useOwnCover } from '../../api/sources';

const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnSm = 'rounded-md bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40';
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40';
const input = 'w-full rounded-lg border border-white/10 bg-surface-2 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-accent';
const AUDIO = 'audio/*,.wav,.mp3,.flac,.m4a';

// «⬆ Nhập bài cover tôi đã làm sẵn»: file bản cover + tên bài, ca sĩ, lời (+ bài gốc nếu có).
export default function OwnCoverPanel({ projectId }: { projectId: number }) {
  const own = useOwnCover(projectId);
  const coverRef = useRef<HTMLInputElement>(null);
  const origRef = useRef<HTMLInputElement>(null);
  const lyricsRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState('');
  const [artist, setArtist] = useState('');
  const [lyrics, setLyrics] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [original, setOriginal] = useState<File | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const reset = () => {
    setTitle(''); setArtist(''); setLyrics(''); setFiles([]); setOriginal(null);
  };

  return (
    <div className={card}>
      <h2 className={h2}>⬆ Nhập bài cover tôi đã làm sẵn</h2>
      <p className="text-xs text-gray-400">
        Đã có bản cover (làm trên Suno hoặc nơi khác)? Đưa thẳng vào đây kèm lời — không cần tải bài gốc từ nhaccuatui.
      </p>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <input className={input} value={title} onChange={(e) => setTitle(e.target.value)}
          placeholder="Tên bài (bắt buộc)" />
        <input className={input} value={artist} onChange={(e) => setArtist(e.target.value)}
          placeholder="Ca sĩ gốc" />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input ref={coverRef} type="file" multiple accept={AUDIO} className="hidden"
          onChange={(e) => {
            const picked = Array.from(e.target.files ?? []);
            e.target.value = '';
            if (picked.length) setFiles((old) => [...old, ...picked]);
          }} />
        <button className={btnSm} onClick={() => coverRef.current?.click()}>🎤 Chọn file bản cover…</button>
        {files.length > 0
          ? <span className="text-xs text-green-300" title={files.map((f) => f.name).join('\n')}>
              {files.length} file <button className="ml-1 text-gray-400 hover:text-red-300" onClick={() => setFiles([])}>✕</button>
            </span>
          : <span className="text-xs text-gray-500">chọn được nhiều file (vd 2 bản Suno)</span>}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input ref={origRef} type="file" accept={AUDIO} className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            e.target.value = '';
            if (f) setOriginal(f);
          }} />
        <button className={btnSm} onClick={() => origRef.current?.click()}>🎵 File bài gốc (không bắt buộc)…</button>
        {original
          ? <span className="truncate text-xs text-green-300">{original.name}
              <button className="ml-1 text-gray-400 hover:text-red-300" onClick={() => setOriginal(null)}>✕</button></span>
          : <span className="text-xs text-gray-500">có bài gốc thì kiểm tra lỗi chính xác hơn</span>}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <span className="flex-1 text-sm text-gray-300">Lời bài hát</span>
        <input ref={lyricsRef} type="file" accept=".txt,.lrc,text/plain" className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = '';
            f?.text().then(setLyrics);
          }} />
        <button className={btnSm} onClick={() => lyricsRef.current?.click()}
          title="Mở file lời .txt hoặc .lrc — tool tự bỏ các mốc thời gian [00:12.30]">📄 Mở file lời…</button>
      </div>
      <textarea className={`${input} mt-1 h-40 font-mono text-xs`} value={lyrics}
        onChange={(e) => setLyrics(e.target.value)} placeholder="Dán lời vào đây, mỗi câu 1 dòng" />

      <div className="mt-2 flex items-center gap-2">
        <button className={btnPrimary} disabled={own.isPending || !title.trim() || files.length === 0}
          title={!title.trim() ? 'Nhập tên bài' : files.length === 0 ? 'Chọn file bản cover' : ''}
          onClick={() => {
            setMsg(null);
            own.mutate({ title, artist, lyrics, files, original }, {
              onSuccess: (r) => {
                setMsg({ ok: true, text: `✅ Đã thêm «${title.trim()}» với ${r.added.length} bản cover — tool đang kiểm tra lỗi.` });
                reset();
              },
              onError: (er) => setMsg({ ok: false, text: errText(er) }),
            });
          }}>
          {own.isPending ? 'Đang nhập…' : '⬆ Nhập bài này'}
        </button>
        {(title || artist || lyrics || files.length > 0 || original) && !own.isPending && (
          <button className={btn} onClick={() => { reset(); setMsg(null); }}>Xoá ô</button>
        )}
      </div>
      {msg && <p className={`mt-2 text-xs ${msg.ok ? 'text-green-300' : 'text-red-400'}`}>{msg.text}</p>}
    </div>
  );
}
