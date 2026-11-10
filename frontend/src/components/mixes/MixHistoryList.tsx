import { downloadUrl } from '../../api/client';
import type { Mix } from '../../types';
import StatusBadge from '../shared/StatusBadge';

interface Props {
  mixes: Mix[];
  onDelete: (mix: Mix) => void;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function fmtDuration(sec?: number): string {
  if (!sec) return '';
  const total = Math.round(sec);   // làm tròn trước — tránh "58m 60s"
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

const DOWNLOADS: { type: string; label: string }[] = [
  { type: 'wav', label: '⬇ WAV' },
  { type: 'cue', label: '⬇ CUE' },
  { type: 'tracklist', label: '⬇ Tracklist' },
  { type: 'json', label: '⬇ JSON' },
];

export default function MixHistoryList({ mixes, onDelete }: Props) {
  if (mixes.length === 0) {
    return <p className="text-sm text-gray-500">No mixes yet.</p>;
  }
  return (
    <ul className="space-y-3">
      {mixes.map((mix) => (
        <li key={mix.id} className="rounded-lg bg-background/50 p-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium text-white">{mix.title}</span>
            <StatusBadge status={mix.status} />
          </div>
          <div className="mb-2 text-xs text-gray-400">
            {mix.duration_minutes} min · {mix.track_count ?? '—'} tracks ·{' '}
            {fmtDate(mix.created_at)}
            {mix.total_duration_seconds
              ? ` · ${fmtDuration(mix.total_duration_seconds)}`
              : ''}
          </div>

          {mix.status === 'running' && (
            <div className="mb-2">
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full bg-blue-400 transition-all"
                  style={{ width: `${mix.progress_percent ?? 0}%` }}
                />
              </div>
              <span className="text-xs text-gray-500">
                {mix.progress_step_name} ({(mix.progress_percent ?? 0).toFixed(0)}
                %)
              </span>
            </div>
          )}

          {mix.status === 'failed' && (
            <p className="mb-2 text-xs text-red-400">{mix.error_message}</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {mix.status === 'completed' &&
              DOWNLOADS.map((d) => (
                <a
                  key={d.type}
                  href={downloadUrl(mix.id, d.type)}
                  className="rounded bg-surface-2 px-2 py-1 text-xs text-gray-200 hover:bg-white/10"
                >
                  {d.label}
                </a>
              ))}
            {mix.status !== 'running' && mix.status !== 'pending' && (
              <button
                onClick={() => onDelete(mix)}
                className="rounded bg-surface-2 px-2 py-1 text-xs text-gray-400 hover:bg-red-600 hover:text-white"
              >
                🗑
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
