import { useEffect, useState } from "react";
import {
  useVideoStatus, useGenImages, useGenClips, useAssemble, useRunAll, useRebuild,
  useRegenImage, useRegenClip, useRegenMotions, useVideoStyles,
  useThumbnailFonts, useSetThumbnailFont, thumbnailFontPreviewUrl, useSetClipEngine,
  usePreparePrompts, usePromptManifest,
  useUploadFootage, useDeleteFootage, videoFootageUrl, useRenderWithFootage,
  useUploadYoutube, useUploadShort, useShortSettings, useSaveShortSettings,
  useYoutubeHistory, useOpenVideoBrowser, useCloseVideoBrowser, useMakeThumbnail,
  videoDownloadUrl, videoThumbnailUrl, videoImageUrl, videoClipUrl,
} from "../../api/video";
import {
  useOpenProjectFolder,
  useProject,
  useProjectFolders,
  useUpdateProject,
} from "../../api/projects";
import ManualClipsPanel from "./ManualClipsPanel";
import { apiErrorText, useChannelMappings } from "../../api/youtube";
import { vndText, ChatGPTLoginPanel } from "../settings/ThumbnailAiSettings";

function fmtDur(sec?: number | null): string {
  if (!sec || sec <= 0) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return h > 0
    ? `${h}h ${m}m ${ss}s`
    : `${m}m ${ss.toString().padStart(2, "0")}s`;
}

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

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border border-white/5 bg-background px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-sm font-semibold ${ok ? "text-green-400" : "text-gray-200"}`}>
        {value}
      </div>
    </div>
  );
}

export default function VideoPanel({ projectId }: { projectId: number }) {
  const { data: st, isLoading } = useVideoStatus(projectId);
  const { data: project } = useProject(projectId);
  const { data: styleData } = useVideoStyles(projectId);
  const { data: fontData } = useThumbnailFonts(projectId);
  const setFont = useSetThumbnailFont(projectId);
  const setEngine = useSetClipEngine(projectId);
  const updateProject = useUpdateProject(projectId);
  const genImages = useGenImages(projectId);
  const preparePrompts = usePreparePrompts(projectId);
  const { data: promptData, refetch: refetchPrompts, error: promptError } = usePromptManifest(projectId);
  const genClips  = useGenClips(projectId);
  const assemble  = useAssemble(projectId);
  const runAll    = useRunAll(projectId);
  const rebuild   = useRebuild(projectId);
  const regenImage   = useRegenImage(projectId);
  const regenClip    = useRegenClip(projectId);
  const regenMotions = useRegenMotions(projectId);
  const uploadFootage = useUploadFootage(projectId);
  const deleteFootage = useDeleteFootage(projectId);
  const renderFootage = useRenderWithFootage(projectId);
  const uploadYoutube = useUploadYoutube(projectId);
  const uploadShort = useUploadShort(projectId);
  const makeThumb = useMakeThumbnail(projectId);
  const { data: shortCfg } = useShortSettings(projectId);
  const saveShortCfg = useSaveShortSettings(projectId);
  const { data: ytHistory, refetch: refetchYtHistory } = useYoutubeHistory(projectId);
  const { data: folders } = useProjectFolders(projectId);
  const openFolder = useOpenProjectFolder(projectId);
  const openBrowser  = useOpenVideoBrowser(projectId);
  const closeBrowser = useCloseVideoBrowser(projectId);
  const [idea, setIdea] = useState("");
  // Nạp ý tưởng đã lưu của project vào ô nhập (khi tải xong / đổi project).
  const savedIdea = project?.video_idea ?? "";
  useEffect(() => { setIdea(savedIdea); }, [savedIdea, projectId]);
  // Cache-buster: sau mỗi lần 1 job hoàn thành, đổi query ?t= để trình duyệt tải
  // lại ảnh/clip mới (thay vì bản cache cũ) trong gallery.
  const [bust, setBust] = useState(() => Date.now());
  const jobStatus = st?.job?.status;
  useEffect(() => {
    if (jobStatus === "completed") {
      setBust(Date.now());
      refetchYtHistory();   // cập nhật lịch sử đăng nháp sau khi job xong
      refetchPrompts();
    }
  }, [jobStatus, refetchYtHistory, refetchPrompts]);
  // Kênh YouTube đăng nháp: người dùng chọn (kênh nào cũng nhận nhạc cụ nào);
  // chưa chọn thì backend tự khớp theo nhạc cụ + thể loại (project cũ).
  const { data: channelMappings } = useChannelMappings();
  const chosenChannelId = project?.channel_id ?? null;
  const effectiveChannelId = project?.effective_channel_id ?? null;
  const effectiveChannelName = project?.effective_channel_name ?? "";
  const autoUpload = project?.auto_upload ?? false;
  const ytChannelSet = effectiveChannelId != null;
  const autoVideo = project?.auto_video ?? false;
  const videoStyle = project?.video_style ?? "real";
  const styleList = styleData?.styles ?? [];
  const currentStyle = styleList.find((s) => s.key === videoStyle);
  const ideaDirty = idea.trim() !== savedIdea.trim();
  // Lựa chọn của project: nội dung video + kiểu intro 00:00.
  const castMode = project?.cast_mode ?? "character_pet";
  const introMode = project?.intro_mode ?? "thumbnail";
  const footageSceneMode = project?.footage_scene_mode ?? "auto";
  const scenery = castMode === "scenery";

  if (isLoading || !st) {
    return <p className="text-gray-400">Đang tải trạng thái video…</p>;
  }

  const p = st.params;
  const job = st.job;
  const busy = job?.status === "running" || job?.status === "pending";
  const browserOpen = busy && !!job?.kind?.startsWith("browser-");
  const imagesDone = st.image_count >= p.image_count;
  const clipsDone  = st.clip_count >= p.total_clips;
  const hasAudio   = !!st.audio_path;
  const thumbAi    = st.thumbnail_ai ?? null;
  const hasCover   = (st.image_indices ?? []).includes(0);
  // Bộ ảnh đã chuẩn bị theo chế độ khác lựa chọn hiện tại → cần «Làm lại từ đầu».
  const castStale  = st.image_count > 0 && !!p.cast_mode && p.cast_mode !== castMode;
  const aiPrice    = thumbAi?.price_usd != null
    ? `~$${thumbAi.price_usd}/ảnh ${vndText(thumbAi.price_usd)}` : "giá theo model";
  function makeAiThumbnail() {
    if (!thumbAi) return;
    const ok = window.confirm(
      `Tạo thumbnail bằng ${thumbAi.model_label}?

` +
      `Tốn phí API: ${aiPrice} (tối đa 2 lượt nếu chữ sai chính tả). ` +
      "Lỗi / chữ sai → tự quay về chữ font (miễn phí).");
    if (ok) makeThumb.mutate({ engine: "ai" });
  }
  const thumbEngineText = (e?: string) =>
    e === "chatgpt" ? "🤖 Vẽ bằng ChatGPT" : e === "ai" ? "🎨 Vẽ bằng AI (API)" : "🔤 Chữ vẽ bằng font";
  const browserName: Record<string, string> = {
    "browser-muse": "Muse.ai", "browser-flow": "Flow",
    "browser-gemini": "Gemini", "browser-chatgpt": "ChatGPT",
  };

  const btn = "rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const primary = `${btn} bg-accent text-white hover:bg-accent-hover`;
  const ghost = `${btn} bg-surface-2 text-gray-200 hover:bg-white/10`;
  const warn = `${btn} border border-amber-500/30 bg-amber-950/20 text-amber-300 hover:bg-amber-950/40`;

  const kindLabel: Record<string, string> = {
    prompts: "Đang chuẩn bị hồ sơ và bộ prompt",
    images: "Đang tạo ảnh", clips: "Đang tạo clip",
    assemble: "Đang ghép video", full: "Đang chạy toàn bộ",
    rebuild: "Đang tạo lại video từ ảnh",
    motions: "Đang sinh lại prompt cảnh (clip)",
    upload: "Đang đăng nháp lên YouTube",
    thumbnail: "Đang tạo thumbnail (ChatGPT / AI / font)",
    upload_short: "Đang tạo & đăng nháp YouTube Short",
    continue: "Đang chạy tiếp chu trình (phần thiếu → ghép → nháp YouTube → Short)",
    "browser-flow": "Cửa sổ Flow đang mở",
    "browser-muse": "Cửa sổ Muse.ai (đúng profile GPM) đang mở",
    "browser-gemini": "Cửa sổ Gemini đang mở",
    "browser-chatgpt": "Cửa sổ ChatGPT đang mở",
    "clips-manual": "Chế độ thủ công — chờ bạn tạo clip trên Flow",
  };

  const imageIndices = st.image_indices ?? [];
  const clipIndices  = st.clip_indices ?? [];
  // Tool Cover: ảnh bìa → 2 bảng (vẽ từ ảnh bìa); clip 00 intro, 01 loop, 02–05 góc quay.
  const single = !!p.single_image;
  // Nhãn vai trò của 3 ảnh — backend trả về trong params.image_roles.
  const roleOf = (i: number) =>
    p.image_roles?.[String(i)] ??
    (i === 0 ? "Ảnh bìa (thumbnail)" : i === 1 ? "Bảng nhân vật chính" : "Bảng linh thú");

  return (
    <div className="space-y-6">
    <Section title="Hồ sơ sáng tạo và bộ prompt">
      {single ? (
      <p className="mb-3 text-sm text-gray-400">
        Tool viết <b>{p.image_count} prompt ảnh</b>: ảnh bìa không chữ với nhân vật nam, nữ
        hoặc cặp song ca theo phân tích giọng hát rồi
        {" "}<b>bảng phân tích nhân vật</b> và <b>bảng tham chiếu bối cảnh</b> vẽ TỪ ảnh bìa; cùng
        {" "}<b>{p.total_clips} prompt chuyển động</b>: clip 00 dùng chính thumbnail có chữ
        làm tham chiếu intro; clip 01–05 dùng hai bảng phân tích/tham chiếu,
        clip 02–05 là 4 góc quay khác nhau của nhân vật. Bối cảnh lấy
        theo tên, ý tưởng và thể loại nhạc của project. Bước này chỉ viết chữ, chưa tạo ảnh hoặc
        video — khi bấm tạo ảnh, Tool cũng tự làm bước này.
      </p>
      ) : (
      <p className="mb-3 text-sm text-gray-400">
        Một hồ sơ chung cho nhạc, nhân vật và bối cảnh. Tool viết <b>{p.image_count} prompt ảnh</b>
        {" "}(bìa · bảng phân tích nhân vật chính · bảng phân tích linh thú) và
        {" "}<b>{p.total_clips} prompt cảnh</b> khác bối cảnh nhưng khoá chung hai bảng nhân vật,
        kiểm tra trùng prompt giữa project và lưu lại để chạy tiếp. Bước này chỉ dùng AI viết chữ,
        chưa tạo nhạc, ảnh hoặc video. Khi bấm tạo ảnh, Tool cũng tự thực hiện bước này.
      </p>
      )}
      <button className={ghost} disabled={busy || preparePrompts.isPending || ideaDirty}
        onClick={() => preparePrompts.mutate({ idea })}>
        Chuẩn bị / kiểm tra bộ prompt
      </button>
      {ideaDirty && <p className="mt-2 text-xs text-amber-300">Lưu ý tưởng bên dưới trước khi chuẩn bị prompt.</p>}
      {(preparePrompts.error || promptError) && <p className="mt-2 text-sm text-red-300">
        {String((preparePrompts.error || promptError) as Error)}
      </p>}
      {promptData?.manifest && <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-accent">
          Xem bộ đã lưu: {Object.keys(promptData.manifest.prompts ?? {}).length} ảnh / {Object.keys(promptData.manifest.motions ?? {}).length} prompt cảnh
          {(promptData.manifest.workflow_version ?? 0) < (single ? 4 : 2) && " (bộ cũ — hãy chuẩn bị lại)"}
        </summary>
        <p className="my-2 text-xs text-amber-200">Kiểm tra đủ cấu trúc không bảo đảm đầu ra giống tuyệt đối; duyệt ảnh và nhạc mẫu trước khi chạy cả bộ.</p>
        {promptData.manifest.continuity && <div className="my-3 whitespace-pre-wrap text-gray-300">
          {promptData.manifest.continuity.scene && <p>Bối cảnh: {promptData.manifest.continuity.scene}</p>}
          <p>{promptData.manifest.continuity.character_sheet}</p>
          {promptData.manifest.continuity.pet_sheet && <p className="mt-2">
            Linh thú{promptData.manifest.continuity.pet_name
              ? ` (${promptData.manifest.continuity.pet_name})` : ""}: {promptData.manifest.continuity.pet_sheet}
          </p>}
          {promptData.manifest.continuity.scene_sheet && <p className="mt-2">{promptData.manifest.continuity.scene_sheet}</p>}
        </div>}
        <div className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Prompt ảnh ({Object.keys(promptData.manifest.prompts ?? {}).length})
        </div>
        <div className="max-h-72 space-y-3 overflow-y-auto">
          {Object.entries(promptData.manifest.prompts ?? {}).map(([key, value]) => <details key={key}>
            <summary className="cursor-pointer">Ảnh {key} — {roleOf(Number(key))}</summary>
            <p className="whitespace-pre-wrap text-gray-400">{value}</p>
          </details>)}
        </div>
        <div className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Prompt cảnh ({Object.keys(promptData.manifest.motions ?? {}).length}) — {single
            ? "clip 00 dùng thumbnail có chữ; clip 01–05 dùng ảnh 1 + 2"
            : "mỗi clip dùng chung 2 bảng nhân vật, chỉ khác bối cảnh"}
        </div>
        <div className="max-h-72 space-y-3 overflow-y-auto">
          {Object.entries(promptData.manifest.motions ?? {})
            .sort((a, b) => Number(a[0]) - Number(b[0]))
            .map(([key, value]) => <details key={key}>
              <summary className="cursor-pointer">Clip {String(key).padStart(2, "0")}{key === "0" ? " — mở đầu duy nhất" : single && key === "1" ? " — chuyển động nhẹ" : single ? " — góc quay nhân vật" : ""}</summary>
              <p className="whitespace-pre-wrap text-blue-200">{value}</p>
            </details>)}
        </div>
      </details>}
    </Section>
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Cột trái: trạng thái + tiến độ */}
      <div className="space-y-4">
        <Section title="Trạng thái media">
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Ảnh (bìa + 2 bảng)" value={`${st.image_count}/${p.image_count}`} ok={imagesDone} />
            <Stat label="Clip" value={`${st.clip_count}/${p.total_clips}`} ok={st.clip_count >= p.total_clips} />
            <Stat label="Nhạc (audio)" value={hasAudio ? fmtDur(st.audio_duration) : "Chưa có"} ok={hasAudio} />
            <Stat label="Video final" value={st.final_exists ? "Đã có" : "Chưa"} ok={st.final_exists} />
          </div>
          {hasAudio && (
            <p className="mt-2 text-xs text-gray-500">
              {single
                ? <>Thumbnail có chữ do ChatGPT tạo là ảnh tham chiếu trực tiếp để tạo clip 00; clip này chỉ phát một lần tại 00:00. Clip 01–{String(p.total_clips - 1).padStart(2, "0")} dùng hai bảng tham chiếu 1 + 2 rồi xáo trộn nối mềm để phủ hết {fmtDur(st.audio_duration)} nhạc.</>
                : <>Clip 00 chỉ phát một lần ở đầu; clip 01–{p.total_clips - 1} chạy cycle random T+{p.t_window} để phủ hết {fmtDur(st.audio_duration)} nhạc.</>}
            </p>
          )}
          {!hasAudio && (
            <p className="mt-2 text-xs text-yellow-300">
              ⚠️ Chưa có bản mix audio. Hãy render 1 bản ở tab <b>Audio</b> trước khi ghép video.
            </p>
          )}
        </Section>

        <Section title="Tiến độ">
          {job ? (
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-gray-400">
                <span>
                  {busy ? (kindLabel[job.kind] ?? "Đang chạy")
                        : job.status === "completed" ? "Hoàn thành"
                        : job.status === "failed" ? "Lỗi" : "—"}
                </span>
                <span className="tabular-nums">{Math.round(job.percent)}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-background">
                <div
                  className={`h-full transition-all ${
                    job.status === "failed" ? "bg-red-500" : "bg-accent"
                  }`}
                  style={{ width: `${Math.min(job.percent, 100)}%` }}
                />
              </div>
              {job.message && (
                <p className="text-xs text-gray-500 break-words">{job.message}</p>
              )}
              {job.status === "failed" && job.error && (
                <p className="rounded bg-red-950/30 p-2 text-xs text-red-400 break-words">
                  {job.error.slice(0, 300)}
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-gray-500">Chưa chạy tác vụ nào.</p>
          )}
        </Section>

        <Section title="Theo dõi trình duyệt (Muse / Flow / Gemini / ChatGPT)">
          <p className="mb-3 text-xs text-gray-400">
            Mở đúng cửa sổ + profile tool dùng cho Muse / Flow / Gemini (Gemini = máy ảnh dự phòng) /
            ChatGPT (vẽ thumbnail) — xem tài khoản, tự đăng nhập hoặc kiểm tra cảnh báo. Tool
            không tự nhập mật khẩu.
          </p>
          {browserOpen ? (
            <button className={`${btn} border border-red-500/30 bg-red-950/30 text-red-300 hover:bg-red-950/50`}
                    disabled={closeBrowser.isPending}
                    onClick={() => closeBrowser.mutate(undefined)}>
              ✕ Đóng trình duyệt {browserName[job?.kind ?? ""] ?? "Flow"}
            </button>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button className={ghost} disabled={busy || openBrowser.isPending}
                      onClick={() => openBrowser.mutate("muse")}>
                🟣 Mở UI Muse (đúng profile)
              </button>
              <button className={ghost} disabled={busy || openBrowser.isPending}
                      onClick={() => openBrowser.mutate("flow")}>
                🌐 Mở UI Flow
              </button>
              <button className={ghost} disabled={busy || openBrowser.isPending}
                      onClick={() => openBrowser.mutate("gemini")}>
                🌐 Mở UI Gemini
              </button>
              <button className={ghost} disabled={busy || openBrowser.isPending}
                      onClick={() => openBrowser.mutate("chatgpt")}>
                🤖 Mở UI ChatGPT
              </button>
            </div>
          )}
          {busy && !browserOpen && (
            <p className="mt-2 text-xs text-amber-300">
              Đang có tác vụ video chạy — khoá nút để tránh 2 cửa sổ tranh cùng 1 profile.
            </p>
          )}
          {openBrowser.isError && (
            <p className="mt-2 text-xs text-red-400">
              {(openBrowser.error as any)?.response?.data?.detail ?? "Không mở được trình duyệt."}
            </p>
          )}
        </Section>

        {(st.final_exists || st.thumbnail_path) && (
          <Section title="Video hoàn chỉnh">
            {st.thumbnail_path && (
              <div className="mb-3">
                <img
                  src={videoThumbnailUrl(projectId, st.thumbnail_mtime ?? bust)}
                  alt="Thumbnail (kèm chữ)"
                  className="w-full rounded-lg border border-white/10"
                />
                {thumbAi && (
                  <p className="mt-1 text-[11px] text-gray-500">
                    {thumbEngineText(thumbAi.engine)}
                    {thumbAi.note && <> · {thumbAi.note}</>}
                  </p>
                )}
                <a
                  href={videoThumbnailUrl(projectId, st.thumbnail_mtime ?? bust)}
                  className={`${ghost} mt-2 inline-block`}
                  download
                >
                  ⬇ Tải thumbnail (kèm chữ)
                </a>
              </div>
            )}
            {st.final_exists && (
              <a
                href={videoDownloadUrl(projectId)}
                className={`${primary} inline-block`}
                download
              >
                ⬇ Tải video final.mp4
              </a>
            )}
            {st.final_exists && folders?.video && (
              <button
                type="button"
                onClick={() => openFolder.mutate("video")}
                disabled={openFolder.isPending}
                title={`Mở thư mục trên máy tính, chọn sẵn file: ${folders.video}`}
                className={`${primary} mt-2 block`}
              >
                📂 Mở thư mục video final
              </button>
            )}
          </Section>
        )}
      </div>

      {/* Cột phải: thao tác */}
      <div className="space-y-4">
        <Section title="Thông số">
          <div className="flex flex-wrap gap-2 text-xs">
            {[
              `Model: ${p.model}`,
              `Tỉ lệ ${p.aspect_ratio}`,
              `${p.clip_seconds}s/clip`,
              single
                ? `Thumbnail ChatGPT → video intro; 2 bảng tham chiếu → ${p.total_clips} clip AI`
                : `${p.image_count} ảnh (bìa + ${p.cast_mode === "scenery" ? "bảng bối cảnh + bảng ánh sáng" : "2 bảng nhân vật"}) → ${p.total_clips} clip`,
              ...(single ? [] : [`Shuffle T+${p.t_window}`]),
              `Fade ${p.blend_seconds}s · slow ${p.slow_speed}x`,
            ].map((t) => (
              <span key={t} className="rounded-full border border-white/10 bg-background px-2.5 py-1 text-gray-300">
                {t}
              </span>
            ))}
          </div>
        </Section>

        <Section title="Phong cách hình ảnh & video">
          <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
            {styleList.length === 0 && (
              <p className="text-xs text-gray-500">Đang tải danh sách phong cách…</p>
            )}
            {styleList.map((s) => {
              const active = videoStyle === s.key;
              return (
                <button
                  key={s.key}
                  disabled={busy || updateProject.isPending}
                  onClick={() => updateProject.mutate({ video_style: s.key })}
                  className={`flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    active
                      ? "border-accent bg-accent/15 text-white"
                      : "border-white/10 bg-background text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <span className="mt-0.5 text-lg leading-none">{s.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-sm font-semibold">
                      <span>{active ? "●" : "○"}</span>
                      {s.label}
                      {fontData?.recipe?.look_styles?.includes(s.key) && (
                        <span className="text-[10px] font-normal text-accent">
                          (hợp phong cách kênh)
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-gray-400">{s.desc}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-gray-500">
            Cuộn để chọn phong cách. Lựa chọn này ép thẳng vào prompt tạo <b>ảnh</b> ({p.image_engine_label ?? "Muse.ai"})
            và <b>video</b> ({p.clip_engine_label ?? "Flow/Veo"}). Đổi phong cách rồi bấm <b>Tạo lại từ đầu</b> để áp dụng
            cho toàn bộ.
          </p>
        </Section>

        <Section title="Intro mở đầu video (00:00)">
          <div className="grid grid-cols-2 gap-2">
            {([
              ["thumbnail", "🖼️ Thumbnail động", "Mặc định — mở đầu bằng chính ảnh thumbnail (chữ AI thiết kế), đẩy máy chậm rồi hoà tan vào clip đầu"],
              ["font", "🔤 Chữ font có sẵn", "Tuỳ chọn — chồng tiêu đề bằng font máy lên clip đầu (kiểu cũ)"],
            ] as const).map(([key, label, desc]) => {
              const active = introMode === key;
              return (
                <button
                  key={key}
                  disabled={busy || updateProject.isPending}
                  onClick={() => updateProject.mutate({ intro_mode: key })}
                  className={`rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    active
                      ? "border-accent bg-accent/15 text-white"
                      : "border-white/10 bg-background text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <span className="flex items-center gap-1.5 text-sm font-semibold">
                    <span>{active ? "●" : "○"}</span>{label}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-gray-400">{desc}</span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-gray-500">
            Áp dụng ở lần <b>ghép video</b> kế tiếp. Ghép video luôn tạo thumbnail trước; nếu
            chưa có thumbnail thì intro tự dùng chữ font.
          </p>
        </Section>

        <Section title="Thumbnail — ảnh + chữ">
          <p className="mb-2 text-xs text-gray-500">
            <b>🤖 ChatGPT</b> (mặc định): ChatGPT web vẽ lại ảnh bìa kèm tiêu đề + dòng phụ theo
            kiểu chữ của project — dùng tài khoản ChatGPT bạn đã đăng nhập, không tốn phí API.{" "}
            <b>🎨 API</b>: model ảnh qua API (tốn phí). <b>🔤 Font</b>: vẽ chữ bằng font máy
            (miễn phí). Không ghép lại video — {introMode === "thumbnail" && <>intro dùng thumbnail
            nên bấm «Ghép video» để mở đầu bằng bản mới; </>}bấm «Đăng nháp YouTube» nếu muốn cập
            nhật bản nháp.
          </p>
          <div className="mb-2 rounded-lg border border-emerald-500/20 bg-background px-3 py-2">
            <ChatGPTLoginPanel compact />
          </div>
          {thumbAi && (
            <div className="mb-2 rounded-lg border border-white/10 bg-background px-3 py-2 text-[11px] text-gray-400">
              Máy vẽ khi ghép video:{" "}
              <b className="text-gray-200">
                {thumbAi.default_engine === "api" ? "API model ảnh"
                  : thumbAi.default_engine === "pil" ? "Chữ font" : "ChatGPT web"}
              </b>
              {thumbAi.default_engine !== "pil" && (
                <> · dự phòng API:{" "}
                  {thumbAi.ai_active ? <b className="text-green-400">Bật</b>
                    : <b>Tắt{!thumbAi.ai_ready && " (chưa có key)"}</b>}
                </>
              )}
              <span className="block">
                API: <b className="text-gray-200">{thumbAi.model_label}</b> · {aiPrice}
              </span>
              {thumbAi.last_ai && (
                <span className="block">
                  Bản AI gần nhất{thumbAi.last_ai.label ? ` (${thumbAi.last_ai.label})` : ""}:{" "}
                  {new Date(thumbAi.last_ai.created_at).toLocaleString("vi-VN")}
                  {" · "}{thumbAi.last_ai.attempts} lượt
                  {thumbAi.last_ai.verified ? " · chữ đã kiểm tra khớp" : ""}
                </span>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              className={primary}
              disabled={busy || !hasCover || makeThumb.isPending}
              onClick={() => makeThumb.mutate({ engine: "chatgpt" })}
              title="Mở ChatGPT web (profile của tool) và vẽ thumbnail kèm chữ"
            >
              🤖 Tạo thumbnail bằng ChatGPT
            </button>
            <button
              className={ghost}
              disabled={busy || !hasCover || !thumbAi?.ai_ready || makeThumb.isPending}
              onClick={makeAiThumbnail}
              title={!thumbAi?.ai_ready ? "Chưa có key — mở ⚙️ Cài đặt → Thumbnail AI" : undefined}
            >
              🎨 Tạo bằng API (tốn phí)
            </button>
            <button
              className={ghost}
              disabled={busy || !hasCover || makeThumb.isPending}
              onClick={() => makeThumb.mutate({ engine: "pil" })}
            >
              🔤 Vẽ lại chữ bằng font (miễn phí)
            </button>
          </div>
          {!hasCover && (
            <p className="mt-2 text-xs text-gray-500">Cần có ảnh bìa (ảnh 0) trước.</p>
          )}
          {hasCover && thumbAi && thumbAi.chatgpt_logged_in === false && (
            <p className="mt-2 text-xs text-amber-300/80">
              Chưa đăng nhập ChatGPT — bấm «🤖 Mở ChatGPT để đăng nhập» ở trên. Nếu bấm tạo khi
              chưa đăng nhập, tool mở cửa sổ và chờ bạn tự đăng nhập (tối đa 10 phút).
            </p>
          )}
          {makeThumb.error && (
            <p className="mt-2 text-xs text-red-300">{apiErrorText(makeThumb.error)}</p>
          )}
        </Section>

        <Section title="Font chữ thumbnail">
          <p className="mb-2 text-xs text-gray-500">
            Mỗi kênh dùng <b>một font nhận diện được khóa xuyên suốt</b> thumbnail, tên kênh
            trên video, intro và Short. Có thể đổi font tại đây; thay đổi sẽ áp dụng cho mọi
            project thuộc cùng kênh ở lần tạo/ghép tiếp theo. Màu và hiệu ứng chữ vẫn được
            cách điệu linh hoạt theo nội dung video.
          </p>
          {fontData?.recipe && (
            <div className="mb-2 rounded-lg border border-white/10 bg-background px-3 py-2 text-[11px] text-gray-400">
              <div className="text-xs font-semibold text-gray-200">
                {fontData.recipe.look_icon} Phong cách kênh: {fontData.recipe.look_label}
              </div>
              {fontData.recipe.ai_text && fontData.recipe.ai_lettering_label ? (
                <>
                  Thumbnail: <b>AI tự thiết kế chữ</b> — kiểu chữ{" "}
                  <b>«{fontData.recipe.ai_lettering_label}»</b> · style{" "}
                  <b>«{fontData.recipe.ai_design_label}»</b> · bố cục{" "}
                  <b>{fontData.recipe.layout}</b> · chuyển cảnh{" "}
                  <b>{fontData.recipe.transition}</b>
                </>
              ) : (
                <>
                  Chữ: <b>{fontData.recipe.layout}</b> · <b>{fontData.recipe.treatment}</b> · màu{" "}
                  <b>{fontData.recipe.palette}</b> · chuyển cảnh <b>{fontData.recipe.transition}</b>
                </>
              )}
              {fontData.recipe.subtitle && <> · dòng phụ «{fontData.recipe.subtitle}»</>}
              {fontData.recipe.legacy && (
                <span className="block text-amber-300/80">
                  Ảnh bìa vẽ theo bố cục cũ (chừa trái) → chữ giữ bên trái; «Tạo lại từ đầu»
                  để có bố cục mới.
                </span>
              )}
            </div>
          )}
          <div className="grid max-h-[28rem] grid-cols-2 gap-2 overflow-y-auto pr-1">
            {fontData?.recipe && (
              <button
                key="__auto"
                disabled={setFont.isPending}
                onClick={() => setFont.mutate("auto")}
                className={`overflow-hidden rounded-lg border text-left transition-colors disabled:opacity-60 ${
                  fontData.auto
                    ? "border-accent ring-2 ring-accent"
                    : "border-white/10 hover:border-white/30"
                }`}
              >
                {fontData.auto_font && (
                  <img
                    src={thumbnailFontPreviewUrl(projectId, fontData.auto_font, bust)}
                    alt="Mẫu font tự động"
                    loading="lazy"
                    className="aspect-video w-full bg-background object-cover"
                  />
                )}
                <span className="block bg-background px-2 py-1.5">
                  <span className="flex items-center gap-1 text-xs font-semibold text-gray-200">
                    <span>{fontData.auto ? "●" : "○"}</span>
                    🔒 Font nhận diện của kênh
                  </span>
                  <span className="block text-[11px] text-gray-500">
                    Tool chọn một lần theo phong cách kênh rồi giữ cố định cho mọi video
                  </span>
                </span>
              </button>
            )}
            {(fontData?.fonts ?? []).map((f) => {
              const active = !fontData?.auto && fontData?.selected === f.key;
              return (
                <button
                  key={f.key}
                  disabled={setFont.isPending}
                  onClick={() => setFont.mutate(f.key)}
                  className={`overflow-hidden rounded-lg border text-left transition-colors disabled:opacity-60 ${
                    active
                      ? "border-accent ring-2 ring-accent"
                      : "border-white/10 hover:border-white/30"
                  }`}
                >
                  <img
                    src={thumbnailFontPreviewUrl(projectId, f.key, bust)}
                    alt={`Mẫu font ${f.label}`}
                    loading="lazy"
                    className="aspect-video w-full bg-background object-cover"
                  />
                  <span className="block bg-background px-2 py-1.5">
                    <span className="flex items-center gap-1 text-xs font-semibold text-gray-200">
                      <span>{active ? "●" : "○"}</span>
                      {f.label}
                      {fontData?.suggested?.includes(f.key) ? (
                        <span className="text-[10px] font-normal text-accent">(hợp kênh)</span>
                      ) : !fontData?.recipe && f.key === fontData?.default && (
                        <span className="text-[10px] font-normal text-gray-500">(mặc định)</span>
                      )}
                    </span>
                    <span className="block text-[11px] text-gray-500">{f.note}</span>
                  </span>
                </button>
              );
            })}
            {!fontData && <p className="text-xs text-gray-500">Đang tải danh sách font…</p>}
          </div>
          {setFont.error && (
            <p className="mt-2 text-xs text-red-300">{String(setFont.error as Error)}</p>
          )}
        </Section>

        <Section title="Ý tưởng của bạn">
          <div className="mb-2 grid grid-cols-2 gap-2">
            {(single ? [
              ["character_pet", "👤 Có người (nhìn vào camera)", "Mặc định — như thumbnail mixtape: 1 cô gái trẻ (hư cấu) mặc gợi cảm, bịt mặt / khẩu trang, nhìn thẳng camera; trang phục theo thể loại (DJ remix / váy dạ hội ballad / ôm đàn acoustic)"],
              ["scenery", "🏞️ Chỉ cảnh vật", "Không người, không con vật — chỉ bối cảnh với chuyển động nhẹ"],
            ] as const : [
              ["character_pet", "👤 Nhân vật + linh thú/thú nuôi", "Mặc định — nhân vật chính + bạn đồng hành đồng bộ xuyên suốt"],
              ["scenery", "🏞️ Chỉ cảnh vật", "Không nhân vật, không con vật — khung cảnh ĐỘNG theo ý tưởng (nước chảy, mây trôi, gió, máy quay chuyển động bình thường)"],
            ] as const).map(([key, label, desc]) => {
              const active = castMode === key;
              return (
                <button
                  key={key}
                  disabled={busy || updateProject.isPending}
                  onClick={() => updateProject.mutate({ cast_mode: key })}
                  className={`rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    active
                      ? "border-accent bg-accent/15 text-white"
                      : "border-white/10 bg-background text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <span className="flex items-center gap-1.5 text-sm font-semibold">
                    <span>{active ? "●" : "○"}</span>{label}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-gray-400">{desc}</span>
                </button>
              );
            })}
          </div>
          {castStale && (
            <p className="mb-2 text-xs text-amber-300/80">
              ⚠️ Bộ ảnh hiện tại được vẽ theo chế độ{" "}
              «{p.cast_mode === "scenery" ? "🏞️ Chỉ cảnh vật" : single ? "👤 Có 1 cô gái" : "👤 Nhân vật + linh thú"}» — bấm{" "}
              <b>Làm lại từ đầu</b> (bước ① ảnh) để vẽ lại theo lựa chọn mới.
            </p>
          )}
          <div className="mb-3 rounded-lg border border-white/10 bg-background/60 p-3">
            <p className="mb-2 text-xs font-semibold text-gray-200">
              📺 Cảnh dùng footage trong ảnh / video
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {([
                ["auto", "✨ Tự động", "Có người → nhân vật xem màn hình; chỉ cảnh vật → TV/máy chiếu."],
                ["character_screen", "👤 Người xem TV", "Tạo cảnh nhân vật đang xem TV hoặc máy chiếu phát footage."],
                ["screen_only", "📺 Chỉ màn chiếu", "Không có người; footage nằm trong TV hoặc màn chiếu điện ảnh."],
              ] as const).map(([key, label, desc]) => {
                const active = footageSceneMode === key;
                return <button key={key} disabled={busy || updateProject.isPending}
                  onClick={() => updateProject.mutate({ footage_scene_mode: key })}
                  className={`rounded-lg border px-3 py-2 text-left disabled:opacity-40 ${active
                    ? "border-accent bg-accent/15 text-white"
                    : "border-white/10 bg-surface text-gray-300 hover:bg-white/5"}`}>
                  <span className="block text-xs font-semibold">{active ? "● " : "○ "}{label}</span>
                  <span className="mt-1 block text-[11px] text-gray-500">{desc}</span>
                </button>;
              })}
            </div>
            <p className="mt-2 text-[11px] text-gray-500">
              Lựa chọn này được đưa vào prompt phân tích nhân vật/bối cảnh và dùng lại khi
              ghép footage. Nếu đã tạo ảnh, hãy tạo lại bộ ảnh để cảnh xem màn hình đồng bộ.
            </p>
          </div>
          <textarea
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            disabled={busy}
            rows={4}
            placeholder={scenery
              ? "Ví dụ: Rừng trúc xanh sau mưa, suối nhỏ chảy qua đá rêu, sương sớm và nắng xuyên qua lá…"
              : "Ví dụ: Một chú cáo nhỏ đi lang thang trong rừng mùa thu, dừng lại uống trà bên suối lúc hoàng hôn…"}
            className="w-full resize-y rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none disabled:opacity-50"
          />
          {single ? (
          <p className="mt-2 text-xs text-gray-500">
            AI bám theo ý tưởng + thể loại nhạc để viết <b>ảnh bìa</b> tỉ lệ <b>16:9</b>, phong cách{" "}
            <b>{currentStyle?.label ?? videoStyle}</b>, không chữ; rồi vẽ <b>bảng phân tích nhân vật</b>
            {" "}và <b>bảng tham chiếu bối cảnh</b> dựa trên chính ảnh bìa. {p.clip_engine_label ?? "Flow/Veo"} làm
            {" "}<b>clip 00</b> (ảnh bìa sống dậy — giới thiệu), <b>clip 01</b> (chuyển động nhẹ) và
            {" "}<b>clip 02–05</b> (4 góc quay: cận mặt, đi ngang, góc thấp, toàn cảnh xoay quanh — dùng 2 bảng
            để giữ đúng nhân vật). Danh sách bài (tên bài + ca sĩ, đánh dấu bài đang phát), lời từng câu
            và sóng âm được gắn lên video khi ghép. {scenery ? "Chế độ chỉ cảnh vật: không có người." : "Nhân vật luôn là nữ, mặc gợi cảm, bịt mặt hoặc đeo khẩu trang."}
          </p>
          ) : scenery ? (
          <p className="mt-2 text-xs text-gray-500">
            AI sẽ bám theo ý tưởng để viết prompt cho từng cảnh — tỉ lệ <b>16:9</b>, phong cách{" "}
            <b>{currentStyle?.label ?? videoStyle}</b>, <b>không nhân vật, không con vật</b>.
            AI vẽ <b>bảng thiết kế bối cảnh</b> và <b>bảng ánh sáng / không khí</b> của nơi chốn
            trong ý tưởng rồi đưa cả hai vào {p.clip_engine_label ?? "Flow/Veo"} làm nguyên liệu
            cho {p.total_clips} clip — mỗi clip một góc khác của cùng khung cảnh, là{" "}
            <b>cảnh động</b> chứ không phải ảnh tĩnh: nước chảy, mây trôi, gió lùa cây cỏ, mưa /
            tuyết / cánh hoa rơi, ánh sáng đổi, máy quay chuyển động mượt (dolly, lia, cẩu…),
            ghép ở tốc độ thật. Ảnh bìa không chứa chữ; thumbnail có chữ được tạo khi ghép video.
          </p>
          ) : (
          <p className="mt-2 text-xs text-gray-500">
            AI sẽ bám theo ý tưởng để viết prompt cho từng cảnh — mặc định tỉ lệ
            <b> 16:9</b>, phong cách <b>{currentStyle?.label ?? videoStyle}</b>,
            nhân vật <b>đồng bộ xuyên suốt</b> như một bộ phim ngắn chill/relaxing.
            AI vẽ <b>bảng phân tích nhân vật chính</b> và <b>bảng linh thú</b> (có nhãn chữ tiếng Anh,
            turnaround trước/nghiêng/sau, biểu cảm, chú thích chi tiết, dải màu) rồi đưa cả hai vào
            {p.clip_engine_label ?? "Flow/Veo"} làm nguyên liệu cho {p.total_clips} clip khác bối cảnh. Nếu ý tưởng chưa có linh thú,
            AI tự nghĩ một con hợp bối cảnh. Riêng ảnh bìa không chứa chữ; thumbnail có chữ được
            tạo khi ghép video và {introMode === "thumbnail" ? "mở đầu video từ 00:00" : "intro dùng chữ font"}.
          </p>
          )}
          <p className="mt-1 text-xs text-gray-600">
            Để trống → dùng bộ prompt mặc định. Cần cấu hình API key ở
            ⚙️ Settings để AI viết prompt.
          </p>

          <div className="mt-3 flex items-center gap-2">
            <button
              className={`${btn} bg-surface-2 text-gray-200 hover:bg-white/10`}
              disabled={updateProject.isPending || !ideaDirty}
              onClick={() => updateProject.mutate({ video_idea: idea.trim() })}
            >
              💾 Lưu ý tưởng
            </button>
            {updateProject.isSuccess && !ideaDirty && !updateProject.isPending && (
              <span className="text-xs text-green-400">Đã lưu ✓</span>
            )}
            {ideaDirty && (
              <span className="text-xs text-yellow-300">Có thay đổi chưa lưu</span>
            )}
          </div>
          <p className="mt-1 text-xs text-gray-600">
            Ý tưởng được lưu theo project — dùng cho cả nút tạo ảnh/clip và khi
            tự động dựng video sau mix. Có thể gõ tiếng Việt — khi lưu, tool
            <b> tự dịch sang tiếng Anh</b> (mọi prompt gửi AI/Flow đều 100% tiếng Anh).
          </p>

          <label className="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-white/5 bg-background px-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 accent-accent"
              checked={autoVideo}
              disabled={updateProject.isPending}
              onChange={(e) => updateProject.mutate({ auto_video: e.target.checked })}
            />
            <span className="text-xs text-gray-300">
              <b>Tự động dựng video sau khi mix Audio xong</b>
              <br />
              <span className="text-gray-500">
                Khi render mix hoàn tất, hệ thống tự chạy ảnh → clip → ghép và
                xuất <code className="text-gray-400">final.mp4</code> vào thư mục
                tên project (dùng ý tưởng đã lưu ở trên).
              </span>
            </span>
          </label>
        </Section>

        <Section title="Footage cảnh quay đời thật (lặp tới hết video)">
          <p className="mb-3 text-xs text-gray-400">
            Upload một hoặc nhiều video quay thật. Khi có footage: mỗi clip AI chỉ chạy
            <b> đúng 1 lần</b>, sau đó các footage (màu phim, giảm độ rực màu, hạt nhiễu film,
            trong khung màn chiếu) phát lần lượt và <b>lặp lại liên tục tới hết video</b>.
            Âm thanh gốc của footage bị bỏ, nhạc giữ nguyên.
          </p>
          {st.footage_exists ? (
            <div className="mb-3 space-y-3">
              {(st.footage_list ?? []).map((f, i) => (
                <div key={f.name} className="space-y-1">
                  <video
                    src={videoFootageUrl(projectId, bust, f.name)}
                    controls
                    preload="metadata"
                    className="aspect-video w-full rounded-lg border border-white/10 bg-black"
                  />
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span className="min-w-0 truncate text-green-300">
                      ✓ {i + 1}. {f.name} · {fmtDur(f.duration)}
                    </span>
                    <button
                      className="rounded-md border border-red-500/30 px-3 py-1.5 text-red-300 disabled:opacity-40"
                      disabled={busy || deleteFootage.isPending}
                      onClick={() => {
                        if (confirm(`Bỏ footage «${f.name}» khỏi project này?`)) {
                          deleteFootage.mutate(f.name);
                        }
                      }}
                    >
                      Bỏ
                    </button>
                  </div>
                </div>
              ))}
              <p className="text-xs text-gray-400">
                {(st.footage_list ?? []).length} footage · tổng {fmtDur(st.footage_duration)} · lặp tới hết video
              </p>
            </div>
          ) : (
            <p className="mb-2 text-xs text-gray-500">
              Chưa có footage — video vẫn ghép bình thường.
            </p>
          )}
          <label className={`${ghost} inline-block cursor-pointer ${busy ? "pointer-events-none opacity-40" : ""}`}>
            {uploadFootage.isPending
              ? "Đang tải footage…"
              : st.footage_exists ? "➕ Thêm footage" : "⬆ Upload footage"}
            <input
              type="file"
              multiple
              accept="video/mp4,video/quicktime,video/x-m4v,video/webm,video/x-matroska"
              className="hidden"
              disabled={busy || uploadFootage.isPending}
              onChange={async (e) => {
                const input = e.target;
                const files = Array.from(input.files ?? []);
                for (const file of files) {
                  try {
                    await uploadFootage.mutateAsync(file);
                  } catch (err: any) {
                    alert(`${file.name}: ${apiErrorText(err)}`);
                  }
                }
                setBust(Date.now());
                input.value = "";
              }}
            />
          </label>
          {st.footage_exists && (
            <button
              className={`${primary} ml-2`}
              disabled={busy || renderFootage.isPending || !clipsDone}
              title={clipsDone ? "" : "Cần đủ clip trước khi tạo video"}
              onClick={() => {
                if (confirm("Tạo lại video final có footage?\n\n• Mỗi clip AI chỉ chạy 1 lần, sau đó footage lặp lại liên tục tới hết video (nhạc vẫn chạy bình thường).\n• final.mp4 hiện tại sẽ được ghép lại và thay thế.")) {
                  renderFootage.mutate(undefined, { onError: (err: any) => alert(apiErrorText(err)) });
                }
              }}
            >
              🎬 Tạo video có footage
            </button>
          )}
          {uploadFootage.isError && (
            <p className="mt-2 text-xs text-red-400">{apiErrorText(uploadFootage.error)}</p>
          )}
        </Section>

        <Section title="Tạo video (cách nhanh)">
          <p className="mb-3 text-xs text-gray-500">
            Đa số trường hợp chỉ cần bấm <b>Tạo toàn bộ video</b>. Nếu đã có bộ
            ảnh ưng ý và chỉ muốn dựng lại phần video, dùng nút bên dưới — giữ
            nguyên ảnh, chỉ làm lại clip rồi ghép.
          </p>
          <div className="space-y-2">
            <button className={`${primary} w-full`} disabled={busy}
                    onClick={() => runAll.mutate(idea.trim() ? { idea: idea.trim() } : undefined)}>
              ▶ Tạo toàn bộ video (ảnh → clip → ghép){idea.trim() ? " — theo ý tưởng" : ""}
            </button>
            <button className={`${ghost} w-full`} disabled={busy || !imagesDone || !hasAudio}
                    onClick={() => {
                      if (confirm(`Giữ nguyên ${p.image_count} ảnh đã có (bìa + 2 bảng ${single ? "vẽ từ ảnh bìa" : "nhân vật"}), tạo lại toàn bộ ${p.total_clips} clip rồi ghép video?`)) {
                        rebuild.mutate({ mode: "restart" });
                      }
                    }}>
              🎬 Tạo lại video từ ảnh đã có (giữ ảnh · clip → ghép)
            </button>
            {!hasAudio && (
              <p className="text-xs text-yellow-300">
                ⚠️ Cần có bản mix Audio trước khi dựng/ghép video.
              </p>
            )}
          </div>
        </Section>

        <Section title="Chạy từng bước">
          <p className="mb-3 text-xs text-gray-500">
            Dùng khi muốn kiểm soát từng phần hoặc tạo tiếp phần còn dở.
            <b> Tạo tiếp</b> = giữ cái đã có, làm phần còn thiếu rồi <b>chạy tiếp cả chu trình</b>
            (ảnh → clip → ghép video → đăng nháp YouTube → đăng nháp Short; bước nào xong rồi thì bỏ qua).
            <b> Làm lại</b> = xoá hết phần đó rồi tạo mới.
          </p>
          <div className="space-y-4">
            {/* Bước 1 — Ảnh */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ① Ảnh — {st.image_count}/{p.image_count} ({p.image_engine_label ?? "Muse.ai"}{p.image_fallback_label ? `, dự phòng ${p.image_fallback_label}` : ""} {single ? " · 0 bìa không chữ · 1 bảng nhân vật · 2 bảng bối cảnh (vẽ từ ảnh bìa)" : " · 0 bìa không chữ · 1 bảng nhân vật chính · 2 bảng linh thú"})
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button className={`${ghost} w-full`} disabled={busy}
                        onClick={() => genImages.mutate({ mode: "resume" })}>
                  ⏩ Tạo tiếp ảnh thiếu → … → Short
                </button>
                <button className={`${warn} w-full`} disabled={busy}
                        onClick={() => {
                          if (confirm(single
                              ? "Xoá ảnh cũ và tạo lại ảnh bìa + 2 bảng (vẽ từ ảnh bìa) từ đầu theo ý tưởng?"
                              : `Xoá toàn bộ ảnh cũ và tạo lại ${p.image_count} ảnh (bìa + 2 bảng nhân vật) từ đầu theo ý tưởng?`)) {
                            genImages.mutate({ idea: idea.trim() || undefined, mode: "restart" });
                          }
                        }}>
                  Làm lại từ đầu
                </button>
              </div>
            </div>
            {/* Bước 2 — Clip */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ② Clip — {st.clip_count}/{p.total_clips} ({(p.clip_engine ?? "muse") === "flow" ? `Flow · ${p.model}` : `${p.clip_engine_label ?? "Gemini Video"} · 1080p · 8s · không tiếng`})
              </div>
              {(p.clip_engines?.length ?? 0) > 1 && (
                <div className="mb-2 flex items-center gap-2 text-xs text-gray-400">
                  <span>Máy tạo clip:</span>
                  <select className="rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs text-gray-200"
                          value={p.clip_engine ?? "muse"} disabled={busy || setEngine.isPending}
                          title="Áp dụng cho mọi project. Ảnh (bìa + bảng nhân vật) do máy tạo ảnh riêng làm — mặc định Muse.ai, dự phòng Gemini."
                          onChange={(e) => setEngine.mutate(e.target.value, {
                            onError: (err: any) => alert(err?.response?.data?.detail ?? String(err)),
                          })}>
                    {p.clip_engines!.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                  </select>
                  {(p.clip_engine ?? "muse") === "muse" && (
                    <span className="text-[11px] text-violet-300">
                      qua GPM profile kênh · thông số ghi trong prompt · ~2 phút/clip
                    </span>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <button className={`${ghost} w-full`} disabled={busy || !imagesDone}
                        onClick={() => genClips.mutate({ mode: "resume" })}>
                  ⏩ Tạo tiếp clip thiếu → … → Short
                </button>
                <button className={`${warn} w-full`} disabled={busy || !imagesDone}
                        onClick={() => {
                          if (confirm(`Xoá toàn bộ clip cũ và tạo lại ${p.total_clips} clip từ ảnh đã có?`)) {
                            genClips.mutate({ mode: "restart" });
                          }
                        }}>
                  Làm lại từ đầu
                </button>
              </div>
              <button className={`${ghost} mt-2 w-full`} disabled={busy}
                      onClick={() => {
                        if (confirm(single
                            ? `Viết lại CHỈ ${p.total_clips} prompt chuyển động (intro, lặp nhẹ, 4 góc quay), giữ ảnh?\n\nKhông tạo lại ảnh/clip — lần tạo clip kế tiếp sẽ dùng prompt mới.`
                            : `Sinh lại CHỈ ${p.total_clips} prompt cảnh từ hai bảng nhân vật đã khoá?\n\nKhông tạo lại ảnh/clip — lần tạo clip kế tiếp sẽ dùng bối cảnh mới.`)) {
                          regenMotions.mutate(undefined);
                        }
                      }}>
                🔄 Sinh lại chỉ prompt cảnh (giữ ảnh/clip)
              </button>
              <div className="mt-2">
                <ManualClipsPanel projectId={projectId} st={st} busy={busy} imagesDone={imagesDone} />
              </div>
            </div>
            {/* Bước 3 — Ghép nhạc */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ③ Ghép video + gắn nhạc
              </div>
              <button className={`${ghost} w-full`} disabled={busy || !clipsDone || !hasAudio}
                      onClick={() => assemble.mutate(undefined)}>
                Ghép video ({introMode === "thumbnail" ? "intro thumbnail → " : ""}clip 00 một lần · T+{p.t_window} · fade {p.blend_seconds}s)
              </button>
            </div>
          </div>
        </Section>

        <Section title="Đăng nháp lên YouTube (qua GPMLogin)">
          <p className="mb-3 text-xs text-gray-500">
            Sau khi có <code className="text-gray-400">final.mp4</code>, tool mở đúng
            profile GPMLogin của <b>kênh YouTube</b> chọn bên dưới,
            AI đọc thumbnail để viết title/description (nhắc tên kênh, tránh trùng),
            đặt cấu hình chuẩn (không dành cho trẻ em · không quảng cáo · có yếu tố AI ·
            ngôn ngữ Anh–Mỹ) rồi <b>lưu bản nháp</b>. <b>KHÔNG bao giờ publish</b> —
            bạn tự đăng khi muốn.
          </p>

          <label className="block">
            <span className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">
              Kênh YouTube đăng nháp
            </span>
            <select
              value={chosenChannelId != null ? String(chosenChannelId) : ""}
              disabled={busy || updateProject.isPending}
              onChange={(e) =>
                updateProject.mutate({
                  channel_id: e.target.value ? Number(e.target.value) : null,
                })
              }
              className="w-full rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none disabled:opacity-50"
            >
              <option value="">🤖 Tự chọn kênh theo dòng nhạc + thể loại</option>
              {(channelMappings ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.channel_name || `(kênh #${m.id} chưa đặt tên)`}
                  {m.music_line ? ` · ${m.music_line}` : ""}
                  {m.look_label ? ` — ${m.look_label}` : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-1 text-xs text-gray-600">
            {chosenChannelId != null && effectiveChannelId != null ? (
              <>✅ Lưu nháp lên kênh <b className="text-gray-400">«{effectiveChannelName || `#${effectiveChannelId}`}»</b>{" "}
                — dòng nhạc / thể loại nào cũng được.</>
            ) : effectiveChannelId != null ? (
              <>Tự chọn → kênh <b className="text-gray-400">«{effectiveChannelName || `#${effectiveChannelId}`}»</b>{" "}
                (theo {[project?.music_line, project?.music_style].filter((v) => v?.trim()).join(" · ")}).
                Chọn kênh khác ở trên nếu muốn.</>
            ) : (
              <>Kênh quyết định profile GPMLogin + hashtag + ngôn ngữ (⚙️ Settings →
                YouTube/GPMLogin). <b>Kênh nào cũng nhận dòng nhạc / thể loại nào</b> — chỉ cần chọn kênh.</>
            )}
          </p>

          <button
            className={`${primary} mt-3 w-full`}
            disabled={busy || !st.final_exists || !ytChannelSet}
            onClick={() => {
              if (confirm("Mở GPMLogin, đăng NHÁP video lên YouTube (không publish)?")) {
                uploadYoutube.mutate(undefined);
              }
            }}
          >
            📤 Đăng nháp lên YouTube
          </button>
          <button
            className={`${ghost} mt-2 w-full`}
            disabled={busy || !st.final_exists || !ytChannelSet}
            onClick={() => {
              if (confirm(
                "Cắt 1 đoạn DỌC 9:16 ở GIỮA video → YouTube Short, AI viết tiêu đề/hook/mô tả ngắn, " +
                "rồi đăng NHÁP lên cùng kênh (không publish)?")) {
                uploadShort.mutate(undefined);
              }
            }}
          >
            🎬 Tạo &amp; đăng nháp YouTube Short
          </button>
          {!st.final_exists && (
            <p className="mt-1 text-xs text-yellow-300">
              ⚠️ Chưa có final.mp4 — hãy render video trước.
            </p>
          )}
          {st.final_exists && !ytChannelSet && (
            <p className="mt-1 text-xs text-yellow-300">
              ⚠️ Hãy <b>chọn kênh YouTube</b> ở trên trước khi đăng.
            </p>
          )}

          <label className="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-white/5 bg-background px-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 accent-accent"
              checked={autoUpload}
              disabled={updateProject.isPending}
              onChange={(e) => updateProject.mutate({ auto_upload: e.target.checked })}
            />
            <span className="text-xs text-gray-300">
              <b>Tự động đăng nháp sau khi render video xong</b>
              <br />
              <span className="text-gray-500">
                Khi <code className="text-gray-400">final.mp4</code> được tạo, hệ thống
                tự đăng nháp (chỉ chạy khi đã có kênh YouTube — chọn ở trên).
              </span>
            </span>
          </label>

          <label className="mt-2 flex cursor-pointer items-start gap-2 rounded-lg border border-white/5 bg-background px-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 accent-accent"
              checked={shortCfg?.auto_short ?? true}
              disabled={!shortCfg || saveShortCfg.isPending}
              onChange={(e) => saveShortCfg.mutate({ auto_short: e.target.checked })}
            />
            <span className="text-xs text-gray-300">
              <b>Tự động tạo Short sau khi đăng nháp video dài xong</b>
              <br />
              <span className="text-gray-500">
                Cắt {Math.round(shortCfg?.seconds ?? 45)}s dọc 9:16 ở giữa video (gọn trong 1 bài),
                chữ hook trên đầu, AI viết nội dung ngắn — lưu <b>NHÁP</b> cùng kênh
                (áp dụng cho mọi project).
              </span>
            </span>
          </label>

          {ytHistory && ytHistory.length > 0 && (
            <div className="mt-4">
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                Lịch sử đăng nháp
              </div>
              <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
                {ytHistory.map((h) => (
                  <div
                    key={h.id}
                    className="rounded-lg border border-white/10 bg-background px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-gray-200">
                        {h.kind === "short" && (
                          <span className="mr-1.5 rounded bg-fuchsia-950/50 px-1 py-0.5 text-[10px] text-fuchsia-300">
                            SHORT
                          </span>
                        )}
                        {h.title || "(chưa có title)"}
                      </span>
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          h.status === "draft"
                            ? "bg-green-950/40 text-green-400"
                            : h.status === "error"
                            ? "bg-red-950/40 text-red-400"
                            : "bg-white/10 text-gray-300"
                        }`}
                      >
                        {h.status}
                      </span>
                    </div>
                    <div className="mt-0.5 text-[11px] text-gray-500">
                      {h.channel_name && <span>📺 {h.channel_name} · </span>}
                      {h.created_at
                        ? new Date(h.created_at).toLocaleString()
                        : ""}
                    </div>
                    {h.hashtags && (
                      <div className="mt-0.5 truncate text-[11px] text-gray-600">
                        {h.hashtags}
                      </div>
                    )}
                    {h.error_message && (
                      <div className="mt-0.5 text-[11px] text-red-400 break-words">
                        {h.error_message.slice(0, 200)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Section>

        <Section title="Lưu ý">
          <ul className="list-disc space-y-1 pl-4 text-xs text-gray-500">
            <li>Lần đầu, cửa sổ trình duyệt sẽ mở ra để bạn <b>tự đăng nhập Gemini & Flow</b> (session được lưu lại).</li>
            <li>Automation Gemini/Flow là best-effort — nếu báo lỗi "không tìm thấy selector", cập nhật trong <code className="text-gray-400">backend/video/config.py</code>.</li>
            <li>Chỉ chạy 1 tác vụ video tại một thời điểm.</li>
            <li>Đăng YouTube cần bật <b>Local API</b> trong GPMLogin và mỗi profile đã đăng nhập sẵn kênh. Thêm kênh ở ⚙️ Settings, chọn kênh cho project ở mục «Đăng nháp YouTube».</li>
          </ul>
        </Section>
      </div>
    </div>

      {/* Gallery: xem trước + tạo lại từng ảnh/clip lẻ */}
      {imageIndices.length > 0 && (
        <Section title={`Ảnh đã tạo — ${imageIndices.length}/${p.image_count} (bấm 🔄 để tạo lại 1 ảnh)`}>
          <p className="mb-3 text-xs text-gray-500">
            Ảnh <b>0</b> tạo thumbnail có chữ và thumbnail này là tham chiếu cho riêng clip intro 00.
            Ảnh <b>1</b> và <b>2</b> là hai bảng phân tích — được nạp làm nguyên liệu cho các clip
            01 trở đi, nên sửa chúng là đổi diện mạo phần thân video.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {imageIndices.map((i) => (
              <div key={i} className="overflow-hidden rounded-lg border border-white/10 bg-background">
                <div className="relative">
                  <img
                    src={videoImageUrl(projectId, i, bust)}
                    alt={roleOf(i)}
                    loading="lazy"
                    className="aspect-video w-full object-cover"
                  />
                  <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {i} · {roleOf(i)}
                  </span>
                </div>
                <button
                  className={`${ghost} w-full rounded-none rounded-b-lg py-1.5 text-xs`}
                  disabled={busy}
                  onClick={() => {
                    if (confirm(`Tạo lại ảnh ${i} — ${roleOf(i)} (ghi đè), giữ nguyên các ảnh khác?\n\nDùng lại prompt đã lưu để nhân vật vẫn khớp cả bộ.`)) {
                      regenImage.mutate(i);
                    }
                  }}
                >
                  🔄 Tạo lại ảnh {i}
                </button>
              </div>
            ))}
          </div>
        </Section>
      )}

      {clipIndices.length > 0 && (
        <Section title={`Clip đã tạo — ${clipIndices.length}/${p.total_clips} (bấm 🔄 để tạo lại 1 clip)`}>
          <p className="mb-3 text-xs text-gray-500">
            {single
              ? "Clip 00–01 dựng từ ảnh bìa, clip 02–05 từ 2 bảng nhân vật/bối cảnh — tạo lại 1 clip lẻ chạy được ngay, không cần làm lại cả bộ."
              : "Mỗi clip đều nạp cùng hai bảng nhân vật (ảnh 1 + ảnh 2), chỉ khác prompt bối cảnh — nên tạo lại 1 clip lẻ chạy được ngay, không cần làm lại cả bộ."}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
            {clipIndices.map((i) => (
              <div key={i} className="overflow-hidden rounded-lg border border-white/10 bg-background">
                <div className="relative">
                  <video
                    src={videoClipUrl(projectId, i, bust)}
                    controls
                    preload="metadata"
                    muted
                    className="aspect-video w-full bg-black object-cover"
                  />
                  <span className="pointer-events-none absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                    {i === 0 ? "00 · intro" : `clip ${String(i).padStart(2, "0")}`}
                  </span>
                </div>
                <button
                  className={`${ghost} w-full rounded-none rounded-b-lg py-1.5 text-xs`}
                  disabled={busy}
                  onClick={() => {
                    if (confirm(`Tạo lại clip ${i} (ghi đè), giữ nguyên các clip khác?`)) {
                      regenClip.mutate(i);
                    }
                  }}
                >
                  🔄 Tạo lại clip {i}
                </button>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
