import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import AppShell from '../components/layout/AppShell';
import TrackList from '../components/tracks/TrackList';
import AddTracksPanel from '../components/tracks/AddTracksPanel';
import MixSettingsPanel from '../components/mixes/MixSettingsPanel';
import MixProgressModal from '../components/mixes/MixProgressModal';
import MixHistoryList from '../components/mixes/MixHistoryList';
import LoudnessPanel from '../components/mixes/LoudnessPanel';
import ProjectFormModal from '../components/projects/ProjectFormModal';
import RenameProjectDialog from '../components/projects/RenameProjectDialog';
import ConfirmDialog from '../components/shared/ConfirmDialog';
import { StemPlayerBar } from '../components/stems/StemPlayerBar';
import VideoPanel from '../components/video/VideoPanel';
import CoverPanel from '../components/cover/CoverPanel';
import SourcePanel from '../components/cover/SourcePanel';
import SunoCoverPanel from '../components/cover/SunoCoverPanel';
import RejectedLyricsDialog from '../components/cover/RejectedLyricsDialog';
import PublishPanel from '../components/cover/PublishPanel';
import WorkflowStatusBar from '../components/shared/WorkflowStatusBar';
import { useStemPlayer } from '../hooks/useStemPlayer';
import { useProject, useUpdateProject } from '../api/projects';
import { useTracks, useDeleteTrack } from '../api/tracks';
import { useMixes, useCreateMix, useDeleteMix } from '../api/mixes';
import type { Mix, MixCreate } from '../types';

// Thứ tự theo workflow cover: tải bài gốc → Suno cover → lời → mix → video → duyệt & đăng.
type Tab = 'source' | 'suno' | 'lyrics' | 'audio' | 'video' | 'publish';
const TABS: { key: Tab; label: string }[] = [
  { key: 'source', label: '① Tải nhạc gốc' },
  { key: 'suno', label: '② Suno Cover' },
  { key: 'lyrics', label: '③ Lời bài hát' },
  { key: 'audio', label: '④ Mix' },
  // Cover Audio Tool: không có ⑤ Video / ⑥ Duyệt & đăng.
];
const isTab = (t: string | null): t is Tab => TABS.some((x) => x.key === t);

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/5 bg-surface p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
        {title}
      </h2>
      {children}
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id } = useParams();
  const projectId = Number(id);

  const { data: project, isLoading } = useProject(projectId);
  const { data: tracks } = useTracks(projectId);
  const { data: mixes } = useMixes(projectId);
  const updateProject = useUpdateProject(projectId);
  const deleteTrack = useDeleteTrack(projectId);
  const createMix = useCreateMix(projectId);
  const deleteMix = useDeleteMix(projectId);

  const [searchParams] = useSearchParams();
  const urlTab = searchParams.get('tab');
  const requireLyrics = searchParams.get('requiredLyrics') === '1';
  const [tab, setTab] = useState<Tab>(isTab(urlTab) ? urlTab : 'source');
  // Bấm tác vụ ở thanh dưới đáy → /projects/:id?tab=… → mở đúng tab.
  useEffect(() => {
    if (isTab(urlTab)) setTab(urlTab);
  }, [urlTab, projectId]);
  const isSingle = project?.kind === 'single';
  // Track lẻ không tạo mix: mọi đường dẫn cũ tới tab Mix đều chuyển thẳng Video.
  useEffect(() => {
    if (isSingle && tab === 'audio') setTab('lyrics');
  }, [isSingle, tab]);
  const [editing, setEditing] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [activeMixId, setActiveMixId] = useState<number | null>(null);
  const [mixToDelete, setMixToDelete] = useState<Mix | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [balanceLoudness, setBalanceLoudness] = useState(true);

  const [playerState, playerControls] = useStemPlayer();

  if (isLoading || !project) {
    return (
      <AppShell title="Project">
        <p className="text-gray-400">Loading…</p>
      </AppShell>
    );
  }

  const trackList = tracks ?? [];
  const mixList = mixes ?? [];

  function startMix(settings: MixCreate) {
    setStartError(null);
    createMix.mutate({ ...settings, loudness_balance: balanceLoudness }, {
      onSuccess: (mix) => setActiveMixId(mix.id),
      onError: (err: any) =>
        setStartError(err?.response?.data?.detail ?? 'Could not start mix'),
    });
  }

  const tabBtn = (t: Tab) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
      isSingle && t === 'audio'
        ? 'cursor-not-allowed bg-surface-2/40 text-gray-600 grayscale'
        :
      tab === t
        ? 'bg-accent text-white'
        : 'bg-surface-2 text-gray-300 hover:bg-white/10'
    }`;

  return (
    <AppShell
      title={project.name}
      actions={
        <>
          <Link
            to="/projects"
            className="rounded-lg px-3 py-2 text-sm text-gray-300 hover:bg-surface-2"
          >
            ← Back
          </Link>
          <button
            onClick={() => setRenaming(true)}
            title="Đổi tên project + chữ tiêu đề trên thumbnail/intro"
            className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-gray-200 hover:bg-white/10"
          >
            ✏️ Đổi tên
          </button>
          <button
            onClick={() => setEditing(true)}
            className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-gray-200 hover:bg-white/10"
          >
            Edit
          </button>
        </>
      }
    >
      <div className="mb-5 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button key={t.key} className={tabBtn(t.key)}
            disabled={isSingle && t.key === 'audio'}
            title={isSingle && t.key === 'audio'
              ? 'Bài lẻ dùng trực tiếp file cover đã master, không cần Mix.'
              : undefined}
            onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Suno từ chối lyrics → hiện ngay ở MỌI tab để dán lyrics khác trước khi tạo lại. */}
      <RejectedLyricsDialog projectId={projectId} />

      {tab === 'source' && (
        <SourcePanel projectId={projectId} onGoToLyrics={() => setTab('lyrics')}
          requireLyrics={requireLyrics} />
      )}

      {tab === 'suno' && (
        <SunoCoverPanel projectId={projectId} onGoToSource={() => setTab('source')} />
      )}

      {tab === 'audio' && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="lg:col-span-2">
            <Section title="🔊 Cân bằng âm lượng các bài">
              <LoudnessPanel projectId={projectId} balance={balanceLoudness}
                onBalanceChange={setBalanceLoudness} />
            </Section>
          </div>
          {/* Left column: Tracks */}
          <div className="space-y-4">
            <Section title={`Bài trong video (${trackList.length})`}>
              <TrackList tracks={trackList} onDelete={(tid) => {
                if (window.confirm('Đưa bài này vào thùng rác? Bạn có thể khôi phục lại sau.')) {
                  deleteTrack.mutate(tid);
                }
              }} />
            </Section>
            <Section title="Thêm file nhạc khác">
              <AddTracksPanel
                projectId={projectId}
                onGoToVideo={() => setTab('lyrics')}
              />
            </Section>

          </div>

          {/* Right column: Mix settings + history */}
          <div className="space-y-4">
            <Section title="Mix Settings">
              <MixSettingsPanel
                trackCount={trackList.length}
                disabled={createMix.isPending}
                onStart={startMix}
                savedDuration={project?.mix_duration_minutes}
                savedCrossfade={project?.mix_crossfade_seconds}
                onSave={(d, c) =>
                  updateProject.mutate({ mix_duration_minutes: d, mix_crossfade_seconds: c })
                }
              />
              {startError && (
                <p className="mt-2 text-xs text-red-400">{startError}</p>
              )}
            </Section>
            <Section title={`Mix History (${mixList.length})`}>
              <MixHistoryList mixes={mixList} onDelete={setMixToDelete} />
            </Section>
          </div>
        </div>
      )}

      {tab === 'lyrics' && <CoverPanel projectId={projectId} />}

      {tab === 'video' && <VideoPanel projectId={projectId} />}

      {tab === 'publish' && <PublishPanel onGoToVideo={() => setTab('video')} />}

      {editing && (
        <ProjectFormModal
          open={editing}
          initial={project}
          saving={updateProject.isPending}
          onSubmit={(data) =>
            updateProject.mutate(data, {
              onSuccess: () => setEditing(false),
              onError: (err: any) =>
                window.alert(err?.response?.data?.detail ?? 'Không lưu được project.'),
            })
          }
          onClose={() => setEditing(false)}
        />
      )}

      <RenameProjectDialog
        open={renaming}
        projectId={projectId}
        currentName={project.name}
        onClose={() => setRenaming(false)}
        onJobStarted={() => setTab('video')}
      />

      {activeMixId !== null && (
        <MixProgressModal
          mixId={activeMixId}
          projectId={projectId}
          onClose={() => setActiveMixId(null)}
        />
      )}

      <ConfirmDialog
        open={mixToDelete !== null}
        title="Delete mix"
        message={`Delete "${mixToDelete?.title}" and its output files?`}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (mixToDelete) deleteMix.mutate(mixToDelete.id);
          setMixToDelete(null);
        }}
        onCancel={() => setMixToDelete(null)}
      />

      <WorkflowStatusBar projectId={projectId} onReopenMix={setActiveMixId} />

      {playerState.activeTrackId !== null && (
        <StemPlayerBar
          trackName={
            trackList.find(t => t.id === playerState.activeTrackId)?.filename ?? ""
          }
          isPlaying={playerState.isPlaying}
          isLoading={playerState.isLoading}
          currentTime={playerState.currentTime}
          duration={playerState.duration}
          error={playerState.error}
          onPlay={() => playerControls.play(playerState.activeTrackId!)}
          onPause={playerControls.pause}
          onResume={playerControls.resume}
          onStop={playerControls.stop}
          onSeek={playerControls.seek}
        />
      )}
    </AppShell>
  );
}
