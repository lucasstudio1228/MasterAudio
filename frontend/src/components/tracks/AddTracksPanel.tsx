import { useRef, useState } from 'react';
import {
  useScanFolder,
  useUploadTrack,
  useDeleteAllTracks,
  useRestoreSelectedTracks,
  useTrackRecovery,
  recoveryAudioUrl,
  recoveryOrphanAudioUrl,
} from '../../api/tracks';
import type { ScanResult, Track } from '../../types';

interface Props {
  projectId: number;
  onGoToVideo?: () => void;   // chuyển sang tab Video sau khi upload
}

export default function AddTracksPanel({ projectId, onGoToVideo }: Props) {
  const [folder, setFolder] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [uploaded, setUploaded] = useState<Track | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const scan = useScanFolder(projectId);
  const upload = useUploadTrack(projectId);
  const deleteAll = useDeleteAllTracks(projectId);
  const recovery = useTrackRecovery(projectId);
  const restoreSel = useRestoreSelectedTracks(projectId);
  const [restoreMessage, setRestoreMessage] = useState('');
  const [showRestore, setShowRestore] = useState(false);
  // Khoá chọn: "t:<id>" = bài trong thùng rác, "o:<tên file>" = file cũ trên ổ đĩa.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const restoreKeys = [
    ...(recovery.data?.items ?? []).filter((it) => it.file_exists).map((it) => `t:${it.id}`),
    ...(recovery.data?.orphan_files ?? []).map((n) => `o:${n}`),
  ];
  const pickedCount = restoreKeys.filter((k) => picked.has(k)).length;

  function togglePick(key: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function restorePicked() {
    const keys = restoreKeys.filter((k) => picked.has(k));
    if (!keys.length) return;
    setRestoreMessage('');
    restoreSel.mutate({
      trash_ids: keys.filter((k) => k.startsWith('t:')).map((k) => Number(k.slice(2))),
      orphan_files: keys.filter((k) => k.startsWith('o:')).map((k) => k.slice(2)),
    }, {
      onSuccess: (r) => {
        setPicked(new Set());
        setRestoreMessage(
          `✅ Đã khôi phục ${r.total} bài${r.missing_files ? `; ${r.missing_files} file không còn trên ổ đĩa` : ''}.`);
      },
    });
  }

  function runScan() {
    if (!folder.trim()) return;
    scan.mutate(folder.trim(), { onSuccess: (r) => setResult(r) });
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = ''; // cho phép chọn lại cùng 1 file
    if (!f) return;
    setUploaded(null);
    upload.mutate(f, { onSuccess: (t) => setUploaded(t) });
  }

  const inputCls =
    'flex-1 rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-white outline-none focus:border-accent';

  return (
    <div className="space-y-2">
      {/* Quét cả thư mục (nhiều track để mix) */}
      <p className="text-xs text-gray-400">Quét cả thư mục (nhiều file cover)</p>
      <div className="flex gap-2">
        <input
          className={inputCls}
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runScan()}
          placeholder="D:/Music/Cover"
        />
        <button
          onClick={runScan}
          disabled={scan.isPending || !folder.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          {scan.isPending ? 'Scanning…' : 'Scan'}
        </button>
      </div>

      {scan.isError && (
        <p className="text-xs text-red-400">
          {(scan.error as any)?.response?.data?.detail ?? 'Scan failed'}
        </p>
      )}

      {result && (
        <div className="rounded-lg bg-background/50 p-2 text-xs text-gray-300">
          <span className="text-green-400">{result.added} added</span>
          {result.skipped_duplicates > 0 && (
            <span>, {result.skipped_duplicates} duplicates</span>
          )}
          {result.skipped_errors > 0 && (
            <span className="text-red-400">, {result.skipped_errors} errors</span>
          )}
          {result.warning_lossy && (
            <div className="mt-1 text-yellow-300">
              ⚠️ Contains MP3 (lossy) sources.
            </div>
          )}
        </div>
      )}

      {/* Upload 1 track dài từ máy → dùng trực tiếp làm nhạc nền video */}
      <div className="border-t border-white/5 pt-3">
        <p className="text-xs text-gray-400">
          Upload track dài → dùng trực tiếp làm nhạc nền video (không cần mix)
        </p>
        <div className="flex items-center gap-2 mt-1">
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.wma"
            onChange={onPickFile}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={upload.isPending}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {upload.isPending ? '⏳ Uploading…' : '⬆️ Upload track'}
          </button>
          {upload.isPending && (
            <span className="text-xs text-gray-400">
              File dài có thể mất một lúc…
            </span>
          )}
        </div>

        {upload.isError && (
          <p className="text-xs text-red-400 mt-1">
            {(upload.error as any)?.response?.data?.detail ?? 'Upload failed'}
          </p>
        )}

        {uploaded && (
          <div className="rounded-lg bg-green-500/10 border border-green-500/30 p-2 mt-2 text-xs">
            <div className="text-green-300">
              ✅ Uploaded: {uploaded.filename}
              <span className="text-green-400/70">
                {' '}
                ({Math.round(uploaded.duration_seconds / 60)} min
                {uploaded.is_lossy ? ' · lossy' : ''})
              </span>
            </div>
            <div className="text-gray-400 mt-1">
              Sẽ được dùng làm nhạc nền khi tạo video.
            </div>
            {onGoToVideo && (
              <button
                onClick={onGoToVideo}
                className="mt-2 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
              >
                🎬 Sang phần Video để tạo video →
              </button>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-white/5 pt-3">
        {(recovery.data?.total ?? 0) > 0 && (
          <div className="mb-2 rounded-lg border border-green-500/30 bg-green-500/10 p-2.5">
            <p className="text-xs text-green-200">
              Có {recovery.data?.total} bài có thể khôi phục
              {!!recovery.data?.orphan_count && ` (${recovery.data.orphan_count} file cũ còn trên ổ đĩa)`}.
            </p>
            {!showRestore ? (
              <button className="mt-2 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-500"
                onClick={() => { setShowRestore(true); setRestoreMessage(''); }}>
                ↩ Chọn bài để khôi phục…
              </button>
            ) : (
              <div className="mt-2 space-y-1.5">
                <div className="flex items-center gap-3 text-[11px] text-green-200/80">
                  <span>Tích các bài muốn lấy lại (bấm ▶ để nghe thử):</span>
                  <button className="underline hover:text-white"
                    onClick={() => setPicked(new Set(restoreKeys))}>Chọn tất cả</button>
                  <button className="underline hover:text-white"
                    onClick={() => setPicked(new Set())}>Bỏ chọn</button>
                </div>
                {(recovery.data?.items ?? []).map((it) => (
                  <RestoreRow key={`t:${it.id}`} label={it.filename}
                    note={it.file_exists ? `xoá lúc ${new Date(it.deleted_at).toLocaleString('vi-VN')}` : 'file không còn trên ổ đĩa'}
                    disabled={!it.file_exists}
                    checked={picked.has(`t:${it.id}`)}
                    onToggle={() => togglePick(`t:${it.id}`)}
                    audioUrl={recoveryAudioUrl(projectId, it.id)} />
                ))}
                {(recovery.data?.orphan_files ?? []).map((name) => (
                  <RestoreRow key={`o:${name}`} label={name} note="file cũ còn trên ổ đĩa"
                    checked={picked.has(`o:${name}`)}
                    onToggle={() => togglePick(`o:${name}`)}
                    audioUrl={recoveryOrphanAudioUrl(projectId, name)} />
                ))}
                <div className="flex items-center gap-2 pt-1">
                  <button className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-500 disabled:opacity-40"
                    disabled={!pickedCount || restoreSel.isPending}
                    onClick={restorePicked}>
                    {restoreSel.isPending ? '⏳ Đang khôi phục…' : `↩ Khôi phục ${pickedCount} bài đã chọn`}
                  </button>
                  <button className="text-xs text-gray-400 hover:text-white"
                    onClick={() => { setShowRestore(false); setPicked(new Set()); }}>Đóng</button>
                </div>
              </div>
            )}
          </div>
        )}
        {restoreMessage && <p className="mb-2 text-xs text-green-300">{restoreMessage}</p>}
        {restoreSel.isError && (
          <p className="mb-2 text-xs text-red-400">
            {(restoreSel.error as any)?.response?.data?.detail ?? 'Không khôi phục được.'}
          </p>
        )}
        <button
          onClick={() => {
            if (window.confirm('Đưa toàn bộ bài vào thùng rác? Bạn có thể khôi phục lại sau.')) {
              deleteAll.mutate();
            }
          }}
          disabled={deleteAll.isPending}
          className="text-xs text-gray-400 hover:text-red-400"
        >
          🗑 Xoá tất cả bài (có thể khôi phục)
        </button>
      </div>
    </div>
  );
}

function RestoreRow({ label, note, checked, disabled, onToggle, audioUrl }: {
  label: string; note: string; checked: boolean; disabled?: boolean;
  onToggle: () => void; audioUrl: string;
}) {
  const [listen, setListen] = useState(false);
  return (
    <div className="rounded-md bg-black/20 px-2 py-1.5">
      <div className="flex items-center gap-2">
        <input type="checkbox" className="accent-green-500" checked={checked}
          disabled={disabled} onChange={onToggle} />
        <span className={`flex-1 truncate text-xs ${disabled ? 'text-gray-500 line-through' : 'text-gray-100'}`}
          title={label}>{label}</span>
        <span className="text-[10px] text-gray-400">{note}</span>
        {!disabled && (
          <button className="rounded px-1.5 text-xs text-green-300 hover:bg-white/10"
            title="Nghe thử" onClick={() => setListen((v) => !v)}>{listen ? '■' : '▶'}</button>
        )}
      </div>
      {listen && <audio controls autoPlay preload="none" src={audioUrl} className="mt-1 h-8 w-full" />}
    </div>
  );
}
