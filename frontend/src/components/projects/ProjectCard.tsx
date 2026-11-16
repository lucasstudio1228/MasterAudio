import { useNavigate } from 'react-router-dom';
import { formatBytes } from '../../api/storage';
import type { Project, ProjectProgress } from '../../types';
import ProjectProgressView from './ProjectProgressView';

interface Props {
  project: Project;
  onDelete: (project: Project) => void;
  /** Dung lượng thật trên đĩa (undefined = đang tính, null = không đo được). */
  sizeBytes?: number | null;
  /** Chế độ «Chọn nhiều»: bấm thẻ = chọn/bỏ chọn thay vì mở project. */
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (project: Project) => void;
  /** Tình trạng các bước (undefined = đang tải). */
  progress?: ProjectProgress;
  progressUnavailable?: boolean;
}

export default function ProjectCard({
  project,
  onDelete,
  sizeBytes,
  selectable = false,
  selected = false,
  onToggle,
  progress,
  progressUnavailable = false,
}: Props) {
  const navigate = useNavigate();
  return (
    <div
      onClick={selectable ? () => onToggle?.(project) : undefined}
      className={`flex flex-col rounded-xl border bg-surface p-5 transition ${
        selected
          ? 'border-red-500/70 ring-1 ring-red-500/50'
          : 'border-white/5 hover:border-accent/40'
      } ${selectable ? 'cursor-pointer select-none' : ''}`}
    >
      <div className="mb-1 flex items-start gap-3">
        {selectable && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggle?.(project)}
            onClick={(e) => e.stopPropagation()}
            className="mt-1.5 h-4 w-4 shrink-0 accent-red-500"
            aria-label={`Chọn ${project.name}`}
          />
        )}
        <h3 className="text-lg font-semibold text-white">{project.name}</h3>
        <span
          className="ml-auto mt-1 shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-gray-400"
          title="Nhóm thư mục lưu project"
        >
          {project.kind === 'single' ? '🎵 Track lẻ' : '🎼 Mix Set'}
        </span>
      </div>
      <p className="mb-4 line-clamp-2 min-h-[2.5rem] text-sm text-gray-400">
        {project.description || 'No description'}
      </p>
      <ProjectProgressView progress={progress} unavailable={progressUnavailable} />
      <div className="mb-4 flex flex-wrap gap-4 text-sm text-gray-300">
        <span>{project.track_count ?? 0} tracks</span>
        <span>{project.mix_count ?? 0} mixes</span>
        {sizeBytes !== null && (
          <span className="text-gray-400" title="Dung lượng dữ liệu của project trên ổ đĩa">
            💾 {sizeBytes === undefined ? '…' : formatBytes(sizeBytes)}
          </span>
        )}
      </div>
      {!selectable && (
        <div className="mt-auto flex gap-2">
          <button
            onClick={() => navigate(`/projects/${project.id}`)}
            className="flex-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-hover"
          >
            Open
          </button>
          <button
            onClick={() => onDelete(project)}
            className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-gray-300 hover:bg-red-600 hover:text-white"
            title="Chuyển vào Thùng rác (giữ 7 ngày, khôi phục được)"
          >
            🗑
          </button>
        </div>
      )}
    </div>
  );
}
