import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '../components/layout/AppShell';
import BatchProgressBar from '../components/projects/BatchProgressBar';
import ProjectCard from '../components/projects/ProjectCard';
import ProjectFormModal from '../components/projects/ProjectFormModal';
import StorageCleanupModal from '../components/projects/StorageCleanupModal';
import TrashModal from '../components/projects/TrashModal';
import ConfirmDialog from '../components/shared/ConfirmDialog';
import {
  useActiveBatch,
  useBulkDeleteProjects,
  useCreateMixSet,
  useCreateProject,
  useDeleteProject,
  useProjects,
  useProjectsProgress,
  useTrash,
} from '../api/projects';
import { formatBytes, useProjectDiskUsage } from '../api/storage';
import type { Project } from '../types';

// Lô chạy rất lâu (hàng giờ) → nhớ key qua reload để vẫn theo dõi được tiến độ.
const BATCH_KEY_STORAGE = 'activeBatchKey';

type Group = 'mixset' | 'single' | 'all';
const kindOf = (p: Project) => (p.kind === 'single' ? 'single' : 'mixset');

export default function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();
  const createProject = useCreateProject();
  const navigate = useNavigate();
  const createMixSet = useCreateMixSet();
  const deleteProject = useDeleteProject();
  const bulkDelete = useBulkDeleteProjects();
  const { data: usage, error: usageError } = useProjectDiskUsage();
  const [showForm, setShowForm] = useState(false);
  const [showCleanup, setShowCleanup] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const { data: progressData, error: progressError } = useProjectsProgress();
  const { data: trashData } = useTrash();
  const progressOf = (id: number) => progressData?.projects.find((x) => x.project_id === id);
  const trashCount = trashData?.items.length ?? 0;
  const retentionDays = trashData?.retention_days ?? 7;
  const [toDelete, setToDelete] = useState<Project | null>(null);
  // Chế độ «Chọn nhiều» để xoá hàng loạt.
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [batchKey, setBatchKey] = useState<string | null>(() =>
    localStorage.getItem(BATCH_KEY_STORAGE)
  );
  // Tab nhóm: Tất cả | Mix Set | Track lẻ — mở trang luôn ở «Tất cả».
  const [group, setGroup] = useState<Group>('all');
  const countOf = (g: Group) =>
    (projects ?? []).filter((p) => g === 'all' || kindOf(p) === g).length;
  const shown = (projects ?? []).filter((p) => group === 'all' || kindOf(p) === group);

  // Server đang chạy lô khác lô đang hiện (vd request tạo lô timeout nên chưa lưu
  // key) → tự chuyển thanh tiến độ sang lô đang chạy thật.
  const { data: active } = useActiveBatch();
  const activeKey = active?.batch_key ?? null;
  useEffect(() => {
    if (activeKey && activeKey !== batchKey) {
      localStorage.setItem(BATCH_KEY_STORAGE, activeKey);
      setBatchKey(activeKey);
    }
  }, [activeKey, batchKey]);

  function dismissBatch() {
    localStorage.removeItem(BATCH_KEY_STORAGE);
    setBatchKey(null);
  }

  const sizeOf = (id: number) => usage?.projects?.[String(id)];
  const selectedProjects = (projects ?? []).filter((p) => selected.has(p.id));
  const selectedBytes = selectedProjects.reduce((sum, p) => sum + (sizeOf(p.id) ?? 0), 0);

  function toggle(project: Project) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(project.id)) next.delete(project.id);
      else next.add(project.id);
      return next;
    });
  }

  function exitSelectMode() {
    setSelectMode(false);
    setSelected(new Set());
  }

  function runBulkDelete() {
    const ids = selectedProjects.map((p) => p.id);
    setConfirmBulk(false);
    deleteProject.reset();
    bulkDelete.mutate(ids, {
      // Bỏ chọn những project đã xoá xong; project lỗi/bận vẫn được chọn để thử lại.
      onSuccess: (res) =>
        setSelected((prev) => {
          const next = new Set(prev);
          res.results.forEach((r) => r.ok && next.delete(r.id));
          return next;
        }),
    });
  }

  const bulkError =
    (bulkDelete.error as any)?.response?.data?.detail ??
    (bulkDelete.error as Error | null)?.message ??
    null;
  const bulkWarnings = (bulkDelete.data?.results ?? []).flatMap((r) =>
    (r.warnings ?? []).map((w) => `«${r.name}»: ${w}`)
  );

  const batchError =
    (createMixSet.error as any)?.response?.data?.detail ??
    (createMixSet.error as Error | null)?.message ??
    null;

  return (
    <AppShell
      title="Projects"
      actions={
        <div className="flex flex-wrap justify-end gap-2">
          <button
            onClick={() => setShowCleanup(true)}
            className="whitespace-nowrap rounded-lg bg-surface-2 px-4 py-2 text-sm text-gray-200 hover:bg-surface-2/70"
            title="Quét & dọn dữ liệu thừa (mix cũ, file tạm, dữ liệu project đã xoá…)"
          >
            🧹 Dọn dữ liệu thừa
          </button>
          <button
            onClick={() => setShowTrash(true)}
            className="whitespace-nowrap rounded-lg bg-surface-2 px-4 py-2 text-sm text-gray-200 hover:bg-surface-2/70"
            title={`Project đã xoá được giữ ${retentionDays} ngày — khôi phục hoặc xoá vĩnh viễn`}
          >
            🗑 Thùng rác{trashCount > 0 ? ` (${trashCount})` : ''}
          </button>
          {projects && projects.length > 0 && (
            <button
              onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
              className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm ${
                selectMode
                  ? 'bg-red-600/80 text-white hover:bg-red-600'
                  : 'bg-surface-2 text-gray-200 hover:bg-surface-2/70'
              }`}
            >
              {selectMode ? '✕ Thoát chọn' : '☑ Chọn nhiều để xoá'}
            </button>
          )}
          <button
            onClick={() => setShowForm(true)}
            className="whitespace-nowrap rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
          >
            + New Project
          </button>
        </div>
      }
    >
      {batchKey && (
        <BatchProgressBar batchKey={batchKey} onDismiss={dismissBatch} />
      )}
      {usage && (
        <p className="mb-3 text-sm text-gray-400">
          💾 Dữ liệu project trên ổ đĩa: <b className="text-gray-200">{formatBytes(usage.total_bytes)}</b>
        </p>
      )}
      {usageError && (
        <p className="mb-3 text-sm text-amber-300">
          💾 Chưa đo được dung lượng: {(usageError as Error).message}
        </p>
      )}
      {selectMode && (
        <div className="sticky top-0 z-20 mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-red-500/30 bg-surface/95 p-3 backdrop-blur">
          <span className="text-sm text-gray-200">
            Đã chọn <b className="text-white">{selectedProjects.length}</b> project
            {selectedProjects.length > 0 && <> (≈ {formatBytes(selectedBytes)})</>}
          </span>
          <button
            onClick={() => setSelected(new Set(shown.map((p) => p.id)))}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-300 hover:bg-surface-2"
          >
            Chọn tất cả
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-300 hover:bg-surface-2"
          >
            Bỏ chọn
          </button>
          <button
            onClick={() => setConfirmBulk(true)}
            disabled={selectedProjects.length === 0 || bulkDelete.isPending}
            className="ml-auto rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {bulkDelete.isPending
              ? 'Đang chuyển vào Thùng rác…'
              : `🗑 Xoá ${selectedProjects.length} project`}
          </button>
        </div>
      )}
      {bulkError && <p role="alert" className="mb-4 text-red-300">{bulkError}</p>}
      {bulkDelete.data && (
        <div role="status" className="mb-4 rounded-lg bg-surface-2/60 p-3 text-sm">
          <p className="text-emerald-300">
            {bulkDelete.data.trashed === false
              ? <>Đã xoá vĩnh viễn {bulkDelete.data.deleted} project, giải phóng {formatBytes(bulkDelete.data.freed_bytes)}.</>
              : <>Đã chuyển {bulkDelete.data.deleted} project vào 🗑 Thùng rác — tự xoá vĩnh viễn sau{' '}
                  {bulkDelete.data.retention_days ?? retentionDays} ngày, khôi phục được trong thời gian đó.</>}
          </p>
          {bulkDelete.data.results.filter((r) => !r.ok).map((r) => (
            <p key={r.id} className="text-red-300">Không xoá được «{r.name}»: {r.error}</p>
          ))}
          {bulkWarnings.length > 0 && (
            <div className="mt-1 text-amber-300">
              Một số thư mục đang bị khoá nên còn nằm tại chỗ (vẫn được khôi phục / dọn cùng project):
              {bulkWarnings.map((w) => <p key={w}>{w}</p>)}
            </div>
          )}
        </div>
      )}
      {deleteProject.error && <p role="alert" className="mb-4 text-red-300">
        {(deleteProject.error as any)?.response?.data?.detail ?? deleteProject.error.message}
      </p>}
      {deleteProject.data?.trashed && <p role="status" className="mb-4 text-sm text-emerald-300">
        Đã chuyển project vào 🗑 Thùng rác — tự xoá vĩnh viễn sau {retentionDays} ngày, mở
        «Thùng rác» để khôi phục.
      </p>}
      {deleteProject.data?.warnings?.length > 0 && <div role="alert" className="mb-4 text-amber-300">
        {deleteProject.data?.trashed
          ? 'Một số thư mục đang bị khoá nên còn nằm tại chỗ (vẫn được khôi phục / dọn cùng project):'
          : 'Project đã xoá khỏi danh sách nhưng còn file chờ dọn:'}
        {deleteProject.data.warnings.map((warning: string) => <p key={warning}>{warning}</p>)}
      </div>}
      {isLoading ? (
        <p className="text-gray-400">Loading…</p>
      ) : !projects || projects.length === 0 ? (
        <div className="mt-20 text-center text-gray-400">
          <p className="mb-2 text-lg">No projects yet.</p>
          <p className="text-sm">Create your first one to get started.</p>
        </div>
      ) : (
        <>
        <div className="mb-4 flex flex-wrap gap-2">
          {([
            ['all', 'Tất cả'],
            ['mixset', '🎼 Mix Set'],
            ['single', '🎵 Track lẻ'],
          ] as const).map(([g, label]) => (
            <button
              key={g}
              onClick={() => setGroup(g)}
              className={`rounded-lg px-4 py-1.5 text-sm ${
                group === g
                  ? 'bg-accent font-medium text-white'
                  : 'bg-surface-2 text-gray-300 hover:bg-surface-2/70'
              }`}
            >
              {label} ({countOf(g)})
            </button>
          ))}
        </div>
        {shown.length === 0 && (
          <p className="mt-10 text-center text-sm text-gray-400">
            Chưa có project nào trong nhóm này.
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onDelete={setToDelete}
              sizeBytes={usageError ? null : usage ? sizeOf(p.id) ?? 0 : undefined}
              selectable={selectMode}
              selected={selected.has(p.id)}
              onToggle={toggle}
              progress={progressOf(p.id)}
              progressUnavailable={Boolean(progressError)}
            />
          ))}
        </div>
        </>
      )}

      <ProjectFormModal
        open={showForm}
        saving={createProject.isPending}
        batchSaving={createMixSet.isPending}
        batchError={batchError}
        onSubmit={(data, { autoRun }) =>
          createProject.mutate(data, {
            onSuccess: (p) => {
              setShowForm(false);
              // Tự bấm «✨ AI viết prompt & CHẠY TỰ ĐỘNG» ở tab Suno của project mới.
              if (autoRun) navigate(`/projects/${p.id}?tab=suno&autorun=1`);
            },
          })
        }
        onSubmitBatch={(data) =>
          createMixSet.mutate(data, {
            onSuccess: (run) => {
              localStorage.setItem(BATCH_KEY_STORAGE, run.batch_key);
              setBatchKey(run.batch_key);
              setGroup(data.kind === 'single' ? 'single' : 'mixset');
              setShowForm(false);
            },
          })
        }
        onClose={() => {
          createMixSet.reset();
          setShowForm(false);
        }}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Xoá project"
        message={`Chuyển "${toDelete?.name}" vào Thùng rác? Toàn bộ dữ liệu (ảnh, clip, video final, nhạc, mix, stems, Suno) được dời sang thư mục Trash/ và tự xoá vĩnh viễn sau ${retentionDays} ngày — trong thời gian đó có thể khôi phục.`}
        confirmLabel="🗑 Chuyển vào Thùng rác"
        danger
        onConfirm={() => {
          if (toDelete) {
            bulkDelete.reset();
            deleteProject.mutate(toDelete.id);
          }
          setToDelete(null);
        }}
        onCancel={() => setToDelete(null)}
      />

      {confirmBulk && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-xl bg-surface p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-white">
              Xoá {selectedProjects.length} project?
            </h3>
            <p className="mb-3 text-sm text-gray-300">
              Chuyển vào 🗑 Thùng rác cùng TOÀN BỘ dữ liệu (ảnh, clip, video final, mix, stems,
              nhạc Suno…). Tool tự xoá vĩnh viễn sau {retentionDays} ngày — trong thời gian đó có
              thể khôi phục. File nhạc gốc import từ thư mục ngoài không bị đụng tới.
            </p>
            <ul className="mb-3 min-h-0 flex-1 space-y-1 overflow-y-auto rounded-lg bg-surface-2/50 p-3 text-sm text-gray-200">
              {selectedProjects.map((p) => (
                <li key={p.id} className="flex justify-between gap-3">
                  <span>{p.name}</span>
                  <span className="whitespace-nowrap text-gray-400">{formatBytes(sizeOf(p.id))}</span>
                </li>
              ))}
            </ul>
            <p className="mb-4 text-sm text-gray-300">
              Tổng ≈ <b className="text-white">{formatBytes(selectedBytes)}</b> (giải phóng khi
              hết hạn hoặc khi xoá vĩnh viễn trong Thùng rác). Project đang chạy tác vụ sẽ được
              bỏ qua.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmBulk(false)}
                className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-surface-2"
              >
                Huỷ
              </button>
              <button
                onClick={runBulkDelete}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
              >
                🗑 Chuyển {selectedProjects.length} project vào Thùng rác
              </button>
            </div>
          </div>
        </div>
      )}

      <StorageCleanupModal open={showCleanup} onClose={() => setShowCleanup(false)} />
      <TrashModal open={showTrash} onClose={() => setShowTrash(false)} />
    </AppShell>
  );
}
