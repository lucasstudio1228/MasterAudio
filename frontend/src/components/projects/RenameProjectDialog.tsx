import { useEffect, useState } from 'react';
import { useRenameProject } from '../../api/projects';

interface Props {
  open: boolean;
  projectId: number;
  currentName: string;
  onClose: () => void;
}

export default function RenameProjectDialog({ open, projectId, currentName, onClose }: Props) {
  const rename = useRenameProject(projectId);
  const [name, setName] = useState(currentName);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(currentName);
      setError(null);
    }
  }, [open, currentName]);

  if (!open) return null;
  const trimmed = name.trim().replace(/\s+/g, ' ');
  const canSubmit = Boolean(trimmed) && trimmed !== currentName && !rename.isPending;

  function submit() {
    setError(null);
    rename.mutate(
      { name: trimmed, refresh: 'none' },
      {
        onSuccess: () => onClose(),
        onError: (err: any) =>
          setError(err?.response?.data?.detail ?? 'Không đổi được tên project.'),
      },
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-lg rounded-xl bg-surface p-6 shadow-xl">
        <h3 className="mb-4 text-lg font-semibold text-white">✏️ Đổi tên project</h3>
        <label className="mb-1 block text-sm text-gray-300">Tên mới</label>
        <input
          autoFocus
          value={name}
          maxLength={150}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && canSubmit) submit();
          }}
          className="mb-4 w-full rounded-lg bg-surface-2 px-3 py-2 text-white outline-none focus:ring-2 focus:ring-accent"
        />
        <p className="mb-4 text-xs text-gray-400">
          Thư mục chứa nhạc của project được đổi tên theo.
        </p>
        {error && (
          <p className="mb-4 whitespace-pre-wrap rounded-lg bg-red-900/40 p-3 text-sm text-red-200">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2"
          >
            Huỷ
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {rename.isPending ? 'Đang đổi tên…' : 'Đổi tên'}
          </button>
        </div>
      </div>
    </div>
  );
}
