import { useAnalyzeAllStems, useProjectStemStatus } from "../../api/stems";

interface Props { projectId: number; }

export function StemSectionHeader({ projectId }: Props) {
  const { data: status } = useProjectStemStatus(projectId);
  const analyzeAll       = useAnalyzeAllStems(projectId);

  const completed = status?.counts.completed ?? 0;
  const total     = status?.total ?? 0;
  const running   = status?.is_processing ?? false;

  return (
    <div className="flex items-center justify-between">
      <div>
        <h3 className="text-xs font-semibold text-gray-400
                       uppercase tracking-wider">
          Stem Separation
        </h3>
        {total > 0 && (
          <p className="text-xs text-gray-600 mt-0.5">
            {completed}/{total} tracks sẵn sàng
            {running && (
              <span className="text-blue-400 ml-1">
                · đang xử lý...
              </span>
            )}
          </p>
        )}
      </div>
      <button
        onClick={() => analyzeAll.mutate()}
        disabled={analyzeAll.isPending || running}
        className="text-xs px-3 py-1.5 rounded-lg font-medium
                   bg-violet-800/60 hover:bg-violet-700/60
                   disabled:opacity-40 text-violet-200
                   border border-violet-600/30"
      >
        {running ? "⏳ Đang chạy..." : "Analyze All"}
      </button>
    </div>
  );
}
