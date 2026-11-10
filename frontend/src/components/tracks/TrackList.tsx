import type { Track } from '../../types';

interface Props {
  tracks: Track[];
  onDelete: (trackId: number) => void;
}

function fmtDuration(sec: number): string {
  const total = Math.round(sec);   // làm tròn TRƯỚC để 179.6s → 3:00, không ra 2:60
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function TrackList({ tracks, onDelete }: Props) {
  return (
    <div>
      {tracks.length === 0 ? (
        <p className="text-sm text-gray-500">
          Chưa có bài nào — nhập file cover ở tab ① hoặc ② trước.
        </p>
      ) : (
        <ul className="space-y-1">
          {tracks.map((t) => (
            <li
              key={t.id}
              className="group flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-2"
            >
              <span title={t.is_lossy ? 'Lossy (MP3)' : t.format}>
                {t.is_lossy ? '⚠️' : '✅'}
              </span>
              <span className="flex-1 truncate text-gray-200" title={t.filename}>
                {t.filename}
              </span>
              <span className="text-xs text-gray-500">
                {(t.sample_rate / 1000).toFixed(1)}k
              </span>
              <span className="w-12 text-right text-gray-400">
                {fmtDuration(t.duration_seconds)}
              </span>
              <button
                onClick={() => onDelete(t.id)}
                className="opacity-0 transition group-hover:opacity-100"
                title="Remove track"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
