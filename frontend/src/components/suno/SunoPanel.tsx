import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useSunoConfig, useSunoStatus, useSunoDryRun, useSunoStart,
  useSunoResume, useSunoPause, useSunoCancel, useSunoGenerateStyles,
  useSunoOpenBrowser, useSunoCloseBrowser,
} from "../../api/suno";
import { useProject, useUpdateProject } from "../../api/projects";
import type { SunoPhase, SunoStartBody, SunoSelectorCheck } from "../../types";

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

/** 1 ô đếm — value/target. `done` bật màu xanh khi đạt/ vượt target. */
function Counter(
  { label, value, target, done }:
  { label: string; value: number; target?: number; done?: boolean },
) {
  return (
    <div className="rounded-lg border border-white/5 bg-background px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-sm font-semibold ${done ? "text-green-400" : "text-gray-200"}`}>
        {value}{target != null ? `/${target}` : ""}
      </div>
    </div>
  );
}

// Bảng kết quả tự kiểm tra selector (mở UI đăng nhập → dò/heal selector).
const SELCHECK_STYLE: Record<string, string> = {
  ok:          "bg-green-950/30 text-green-400",
  healed:      "bg-sky-950/30 text-sky-300",
  missing:     "bg-red-950/30 text-red-400",
  skipped:     "bg-white/5 text-gray-500",
  conditional: "bg-white/5 text-gray-400",
};
const SELCHECK_ICON: Record<string, string> = {
  ok: "✓", healed: "🛠", missing: "✕", skipped: "–", conditional: "○",
};

function SelectorCheck({ data }: { data: SunoSelectorCheck }) {
  const s = data.summary;
  const page = data.groups.filter(
    (g) => g.kind === "page" || g.kind === "conditional",
  );
  const interactive = data.groups.filter((g) => g.kind === "interactive");
  return (
    <div className="space-y-2 rounded-lg border border-white/5 bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-gray-300">
          Kiểm tra selector Suno
        </span>
        <span className="text-[10px] text-gray-600">{data.checked_at}</span>
      </div>
      <p className="text-[11px] text-gray-400">
        Selector page-level OK <b className="text-gray-200">{s.page_ok}/{s.page_total}</b>
        {data.logged_in === false && " · chưa đăng nhập"}
      </p>
      {s.healed.length > 0 && (
        <p className="rounded bg-sky-950/30 p-1.5 text-[11px] text-sky-300 break-words">
          🛠 Tự sửa & đã lưu vào video_overrides.json: {s.healed.join(", ")}
        </p>
      )}
      {s.missing.length > 0 && (
        <p className="rounded bg-red-950/30 p-1.5 text-[11px] text-red-300 break-words">
          ✕ Không tự đoán được (cần sửa tay trong config.py): {s.missing.join(", ")}
        </p>
      )}
      {data.save_error && (
        <p className="rounded bg-red-950/30 p-1.5 text-[11px] text-red-400 break-words">
          Lỗi lưu override: {data.save_error}
        </p>
      )}
      <div className="flex flex-wrap gap-1">
        {page.map((g) => (
          <span key={g.name}
                title={g.matched ?? undefined}
                className={`rounded px-1.5 py-0.5 text-[10px] ${SELCHECK_STYLE[g.status] ?? ""}`}>
            {SELCHECK_ICON[g.status]} {g.name}
          </span>
        ))}
      </div>
      {interactive.length > 0 && (
        <details className="text-[10px] text-gray-600">
          <summary className="cursor-pointer">
            {interactive.length} selector tương tác (bỏ qua khi kiểm tĩnh)
          </summary>
          <div className="mt-1 flex flex-wrap gap-1">
            {interactive.map((g) => (
              <span key={g.name} className="rounded bg-white/5 px-1.5 py-0.5 text-gray-500">
                {g.name}
              </span>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

// Nhãn phase tiếng Việt + phân loại trạng thái (blocking cần người dùng xử lý).
const PHASE_LABEL: Record<SunoPhase, string> = {
  PREFLIGHT: "Chuẩn bị (điền form, đọc credits)",
  GENERATING: "Đang tạo bài trên Suno",
  DOWNLOADING: "Đang tải WAV",
  VALIDATING: "Đang xác thực WAV",
  READY_FOR_IMPORT: "Sẵn sàng import",
  IMPORTED: "Đã import vào project",
  DOWNSTREAM_RUNNING: "Đang chạy bước tiếp theo",
  COMPLETED: "Hoàn thành",
  WAITING_FOR_LOGIN: "Cần đăng nhập Suno",
  WAITING_FOR_HUMAN: "Cần thao tác thủ công",
  SUBMISSION_UNCERTAIN: "Không chắc đã gửi Create — kiểm tra thủ công",
  INSUFFICIENT_GENERATION_CREDITS: "Hết credit tạo bài",
  INSUFFICIENT_DOWNLOAD_ALLOWANCE: "Hết lượt tải",
  UI_CHANGED: "Giao diện Suno đã đổi — cần cập nhật selector",
  FAILED: "Lỗi",
  PAUSED: "Đã tạm dừng",
  CANCELLED: "Đã huỷ",
};

const BLOCKING: SunoPhase[] = [
  "WAITING_FOR_LOGIN", "WAITING_FOR_HUMAN", "SUBMISSION_UNCERTAIN",
  "INSUFFICIENT_GENERATION_CREDITS", "INSUFFICIENT_DOWNLOAD_ALLOWANCE",
  "UI_CHANGED", "FAILED",
];
const RESUMABLE: SunoPhase[] = [...BLOCKING, "PAUSED"];
const TERMINAL: SunoPhase[] = ["COMPLETED", "CANCELLED"];

/** Lỗi HTTP → câu tiếng Việt (ưu tiên detail của backend, vd 409 worker bận). */
const errText = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  ?? (e as Error)?.message ?? fallback;

export default function SunoPanel({ projectId }: { projectId: number }) {
  const { data: cfg, isLoading: cfgLoading } = useSunoConfig(projectId);
  const { data: status } = useSunoStatus(projectId);
  const { data: project } = useProject(projectId);
  const updateProject = useUpdateProject(projectId);

  const dryRun = useSunoDryRun(projectId);
  const start  = useSunoStart(projectId);
  const resume = useSunoResume(projectId);
  const pause  = useSunoPause(projectId);
  const cancel = useSunoCancel(projectId);
  const genStyles = useSunoGenerateStyles(projectId);
  const openBrowser  = useSunoOpenBrowser(projectId);
  const closeBrowser = useSunoCloseBrowser(projectId);

  // ── Ý tưởng + prompt Styles/Exclusions (AI viết hoặc tự sửa) ──
  const [idea, setIdea] = useState("");
  const [ideaInited, setIdeaInited] = useState(false);
  const [styles, setStyles] = useState("");
  const [exclusions, setExclusions] = useState("");
  const [genNote, setGenNote] = useState<string | null>(null);
  const [channel, setChannel] = useState<import("../../types").SunoChannelMatch | null>(null);

  // ── Form override (khởi tạo từ defaults khi config tải xong) ──
  const [preset, setPreset] = useState("");
  const [model, setModel] = useState("");
  const [allowFallback, setAllowFallback] = useState(false);
  // Max Mode LUÔN bật (chốt 2026-10-08) — backend cũng ép True, ô tick chỉ để hiển thị.
  const maxMode = true;
  const [target, setTarget] = useState(15);
  const [durMin, setDurMin] = useState(180);
  const [durMax, setDurMax] = useState(300);
  const [durMinimum, setDurMinimum] = useState(120);
  const [maxCreate, setMaxCreate] = useState(10);
  const [maxCredits, setMaxCredits] = useState(100);
  const [maxDownloads, setMaxDownloads] = useState(15);
  const [autoContinue, setAutoContinue] = useState(true);
  const [inited, setInited] = useState(false);

  useEffect(() => {
    if (cfg && !inited) {
      const d = cfg.defaults;
      setPreset(d.preset);
      setModel(d.preferred_model);
      setAllowFallback(d.allow_model_fallback);
      setTarget(d.target_tracks);
      setDurMin(d.preferred_duration_seconds_min);
      setDurMax(d.preferred_duration_seconds_max);
      setDurMinimum(d.minimum_duration_seconds);
      setMaxCreate(d.max_create_actions);
      setMaxCredits(d.max_generation_credits);
      setMaxDownloads(d.max_new_song_downloads);
      setAutoContinue(d.auto_continue_workflow);
      // Nạp sẵn Styles/Exclusions từ preset mặc định (người dùng có thể sửa / để AI viết).
      const dp = cfg.presets.find((p) => p.key === d.preset);
      if (dp) {
        setStyles(dp.styles);
        setExclusions(dp.exclusions);
      }
      setInited(true);
    }
  }, [cfg, inited]);

  // Nạp ý tưởng đã lưu của project vào textbox (nhớ giữa các phiên).
  useEffect(() => {
    if (project && !ideaInited) {
      setIdea(project.suno_idea ?? "");
      setIdeaInited(true);
    }
  }, [project, ideaInited]);

  const batch = status?.batch ?? null;
  const job = status?.job ?? null;
  const busy = job?.status === "running" || job?.status === "pending";
  const phase = batch?.phase;

  // Job "suno-login" = đang mở UI đăng nhập + kiểm tra selector (giữ cửa sổ mở).
  const loginBusy = job?.kind === "suno-login" &&
    (job.status === "running" || job.status === "pending");
  const selectorCheck = status?.selector_check ?? null;

  const failedAction = [start, dryRun, resume, openBrowser].find((m) => m.isError);
  const actionError = failedAction ? errText(failedAction.error, "Không gửi được lệnh") : null;
  const queued = job?.status === "pending" ? job.message : "";

  /** «✨ AI viết prompt & CHẠY TỰ ĐỘNG»: viết Styles + phân kênh rồi Start LIVE
   *  ngay → import → Mix → Video → lưu nháp YouTube (không hỏi lại). */
  async function runAuto() {
    try {
      const res = await genStyles.mutateAsync({ idea, preset, save: true });
      setStyles(res.styles);
      setExclusions(res.exclusions);
      setGenNote(res.note);
      setChannel(res.channel);
      start.mutate({
        ...buildBody(),
        styles: res.styles,
        exclusions: res.exclusions,
      }, {
        onError: (e) => setGenNote(
          "Đã viết prompt nhưng KHÔNG khởi động được Suno: " +
          errText(e, "lỗi không rõ")),
      });
    } catch (e) {
      setGenNote("Không gọi được AI viết prompt: " +
        errText(e, "kiểm tra API key ở ⚙️ Settings."));
    }
  }

  // New Project → «Tạo & CHẠY TỰ ĐỘNG» mở trang với ?autorun=1 → tự bấm nút ✨
  // đúng 1 lần khi cấu hình/ý tưởng/trạng thái đã tải xong.
  const [searchParams, setSearchParams] = useSearchParams();
  const autorunRequested = searchParams.get("autorun") === "1";
  const autoFired = useRef(false);
  useEffect(() => {
    if (!autorunRequested || autoFired.current) return;
    if (!inited || !ideaInited || !project || status === undefined) return;
    autoFired.current = true;
    const next = new URLSearchParams(searchParams);
    next.delete("autorun");
    setSearchParams(next, { replace: true });
    if (busy) {
      setGenNote("Suno của project đang bận nên chưa tự chạy — bấm nút ✨ khi rảnh.");
      return;
    }
    void runAuto();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autorunRequested, inited, ideaInited, project, status, busy]);

  const canResume = !busy && phase != null && RESUMABLE.includes(phase);
  const isTerminal = phase != null && TERMINAL.includes(phase);
  const isBlocking = phase != null && BLOCKING.includes(phase);

  const btn = "rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const primary = `${btn} bg-accent text-white hover:bg-accent-hover`;
  const ghost = `${btn} bg-surface-2 text-gray-200 hover:bg-white/10`;
  const danger = `${btn} border border-red-500/30 bg-red-950/20 text-red-300 hover:bg-red-950/40`;

  if (cfgLoading || !cfg) {
    return <p className="text-gray-400">Đang tải cấu hình Suno…</p>;
  }

  function buildBody(): SunoStartBody {
    return {
      preset,
      styles,
      exclusions,
      suno_idea: idea,
      target_tracks: target,
      preferred_model: model,
      allow_model_fallback: allowFallback,
      max_mode: maxMode,
      preferred_duration_seconds_min: durMin,
      preferred_duration_seconds_max: durMax,
      minimum_duration_seconds: durMinimum,
      max_create_actions: maxCreate,
      max_generation_credits: maxCredits,
      max_new_song_downloads: maxDownloads,
      auto_continue_workflow: autoContinue,
    };
  }

  const numField = (
    label: string, value: number, set: (n: number) => void, hint?: string,
  ) => (
    <label className="block">
      <span className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">
        {label}
      </span>
      <input
        type="number"
        value={value}
        disabled={busy}
        onChange={(e) => set(Number(e.target.value))}
        className="w-full rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none disabled:opacity-50"
      />
      {hint && <span className="mt-0.5 block text-[10px] text-gray-600">{hint}</span>}
    </label>
  );

  // Đã hoàn thành THỰC SỰ = phase IMPORTED/COMPLETED và đã import đủ target.
  const trulyDone =
    !!batch &&
    (batch.phase === "COMPLETED" || batch.phase === "IMPORTED") &&
    batch.imported_tracks >= batch.target_tracks;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Cột trái: preset + tiến độ + counters */}
      <div className="space-y-4">
        <Section title="Preset phong cách">
          <div className="space-y-1.5">
            {cfg.presets.map((p) => {
              const active = preset === p.key;
              return (
                <button
                  key={p.key}
                  disabled={busy}
                  onClick={() => {
                    setPreset(p.key);
                    // Quick-fill: nạp Styles/Exclusions của preset (có thể sửa lại).
                    setStyles(p.styles);
                    setExclusions(p.exclusions);
                    setGenNote(null);
                  }}
                  className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-40 ${
                    active
                      ? "border-accent bg-accent/15 text-white"
                      : "border-white/10 bg-background text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <span>{active ? "●" : "○"}</span>
                  <span className="text-sm font-semibold">{p.label}</span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-[11px] text-gray-600">
            Chọn preset để nạp nhanh Styles/Exclusions bên dưới — hoặc mô tả ý tưởng
            riêng để AI tự viết. Bạn luôn sửa lại được trước khi chạy.
          </p>
        </Section>

        <Section title="Ý tưởng của bạn → AI viết prompt (luôn KHÔNG LỜI)">
          <p className="mb-2 text-xs text-gray-500">
            AI tự đoán nhạc cụ &amp; không khí từ <b>Tên project (Project Title)</b> — bạn
            không cần chỉ dùng sáo trúc, có thể là piano, guitar, hang drum, kalimba, đàn
            tranh, harp, synth pad… Nếu muốn chính xác hơn, mô tả thêm ý tưởng/khung cảnh
            bên dưới. Khi bấm <b>AI viết prompt</b>, AI sẽ viết brief thật chi tiết cho Suno
            (BPM, tông/âm giai, vòng hợp âm, tính chất giai điệu, phối khí, mix) để chất
            lượng cao nhất. Nhạc luôn <b>instrumental, không giọng hát/lời</b>.
            Có thể gõ tiếng Việt — tool <b>tự dịch sang tiếng Anh</b> khi lưu để mọi
            prompt đồng nhất 100% tiếng Anh.
          </p>
          <textarea
            value={idea}
            disabled={busy}
            onChange={(e) => setIdea(e.target.value)}
            onBlur={() => {
              if ((project?.suno_idea ?? "") !== idea) {
                // Server lưu bản TIẾNG ANH (tự dịch) → hiện lại bản đã lưu.
                updateProject.mutate({ suno_idea: idea }, {
                  onSuccess: (p) => setIdea((cur) => (cur === idea ? p.suno_idea ?? cur : cur)),
                });
              }
            }}
            rows={2}
            placeholder="VD: sáo trúc thiền định cạnh dòng suối, nhẹ nhàng, ấm áp…"
            className="w-full resize-y rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none disabled:opacity-50"
          />
          <button
            className={`${primary} mt-2 w-full`}
            disabled={busy || genStyles.isPending || start.isPending}
            onClick={() => void runAuto()}
          >
            {genStyles.isPending
              ? "✨ Đang viết prompt…"
              : start.isPending
              ? "▶ Đang khởi động…"
              : "✨ AI viết prompt & CHẠY TỰ ĐỘNG (Suno → Mix → Video → nháp YouTube)"}
          </button>
          <p className="mt-1 text-[10px] text-gray-600">
            Bấm là chạy LIVE ngay (tốn credit Suno) và tự đăng <b>bản nháp</b>
            YouTube khi xong — không hỏi lại. Vẫn dừng nếu chạm hạn mức, gặp
            CAPTCHA hoặc cần đăng nhập. Chỉ muốn viết prompt rồi tự bấm Start?
            Dùng nút ▶ Start ở cột phải.
          </p>
          {genNote && (
            <p className="mt-2 rounded bg-amber-950/20 p-2 text-[11px] text-amber-300">
              {genNote}
            </p>
          )}
          {channel && (
            <div className="mt-2 rounded bg-background/60 p-2 text-[11px]">
              {channel.matched ? (
                <p className="text-green-400">
                  🎯 Kênh YouTube: <b>{channel.channel_name || "(chưa đặt tên)"}</b>
                  {channel.confidence === "chosen" ? " (kênh bạn chọn)" : " (tự chọn)"}
                  {" "}— nhạc cụ <b>{channel.instrument}</b> / thể loại{" "}
                  <b>{channel.music_style}</b>.
                </p>
              ) : (
                <p className="text-amber-300">⚠️ {channel.warning}</p>
              )}
            </div>
          )}

          <label className="mt-3 block">
            <span className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">
              Styles gốc (English, có thể sửa; nên dưới 600 ký tự)
            </span>
            <textarea
              value={styles}
              disabled={busy}
              onChange={(e) => setStyles(e.target.value)}
              rows={4}
              className="w-full resize-y rounded-lg border border-white/10 bg-background px-3 py-2 text-xs text-gray-300 focus:border-accent focus:outline-none disabled:opacity-50"
            />
          </label>
          <label className="mt-2 block">
            <span className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">
              Exclude styles (loại trừ — luôn giữ các từ khoá loại bỏ giọng hát)
            </span>
            <textarea
              value={exclusions}
              disabled={busy}
              onChange={(e) => setExclusions(e.target.value)}
              rows={2}
              className="w-full resize-y rounded-lg border border-white/10 bg-background px-3 py-2 text-xs text-gray-300 focus:border-accent focus:outline-none disabled:opacity-50"
            />
          </label>
          <p className="mt-1.5 text-[10px] text-gray-600">
            Tool bổ sung đặc trưng riêng của project và biến thể giai điệu cho từng lượt Create,
            lưu kế hoạch để chạy tiếp không đổi prompt. Prompt cuối phải dưới 1.000 ký tự;
            nếu quá dài Tool dừng, không tự cắt. Exclude luôn có các từ loại trừ giọng hát;
            vẫn cần nghe kiểm tra đầu ra.
          </p>
        </Section>

        <Section title="Tiến độ batch">
          {/* Trạng thái mở UI đăng nhập + kết quả kiểm tra selector (không phụ
              thuộc batch — hiện cả khi chưa có batch nào). */}
          {loginBusy && (
            <div className="mb-3 space-y-1.5 rounded-lg border border-sky-500/20 bg-sky-950/10 p-3">
              <div className="flex justify-between text-xs text-sky-300">
                <span>🌐 Đang mở UI Suno</span>
                <span className="tabular-nums">{Math.round(job?.percent ?? 0)}%</span>
              </div>
              {job?.message && (
                <p className="text-[11px] text-gray-400 break-words">{job.message}</p>
              )}
              <p className="text-[10px] text-gray-600">
                Đăng nhập trong cửa sổ vừa mở nếu cần. Xong thì bấm <b>Đóng trình duyệt</b>.
              </p>
            </div>
          )}
          {selectorCheck && (
            <div className="mb-3">
              <SelectorCheck data={selectorCheck} />
            </div>
          )}

          {batch ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
                  isTerminal && batch.phase === "COMPLETED" ? "bg-green-950/40 text-green-400"
                  : batch.phase === "CANCELLED" ? "bg-white/10 text-gray-300"
                  : isBlocking ? "bg-amber-950/40 text-amber-300"
                  : "bg-accent/20 text-accent"
                }`}>
                  {phase ? PHASE_LABEL[phase] : "—"}
                </span>
                {batch.dry_run && (
                  <span className="rounded bg-white/10 px-2 py-0.5 text-[10px] text-gray-400">
                    dry-run
                  </span>
                )}
              </div>

              {job && (
                <>
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>{busy ? "Đang chạy" : job.status === "completed" ? "Job xong" : job.status === "failed" ? "Job lỗi" : "—"}</span>
                    <span className="tabular-nums">{Math.round(job.percent)}%</span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded bg-background">
                    <div
                      className={`h-full transition-all ${job.status === "failed" ? "bg-red-500" : "bg-accent"}`}
                      style={{ width: `${Math.min(job.percent, 100)}%` }}
                    />
                  </div>
                </>
              )}

              {(batch.message || job?.message) && (
                <p className="text-xs text-gray-500 break-words">
                  {job?.message || batch.message}
                </p>
              )}
              {job?.status === "failed" && job.error && (
                <p className="rounded bg-red-950/30 p-2 text-xs text-red-400 break-words">
                  {job.error.slice(0, 300)}
                </p>
              )}

              {/* Counters — phản ánh ĐÚNG từng giai đoạn, không gộp thành "15/15". */}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <Counter label="Đã tạo (candidate)" value={batch.generated_candidates} />
                <Counter label="Đã chọn" value={batch.selected_tracks} target={batch.target_tracks} />
                <Counter label="Đã tải WAV" value={batch.downloaded_tracks} target={batch.target_tracks} />
                <Counter label="Đã xác thực" value={batch.validated_tracks} target={batch.target_tracks} />
                <Counter label="Đã import" value={batch.imported_tracks} target={batch.target_tracks} done={trulyDone} />
                <Counter label="Lượt Create đã dùng" value={batch.create_actions_used} />
              </div>

              {trulyDone ? (
                <p className="rounded bg-green-950/30 p-2 text-xs text-green-400">
                  ✓ Hoàn thành: {batch.imported_tracks}/{batch.target_tracks} WAV hợp lệ đã import vào project.
                </p>
              ) : (
                <p className="text-[11px] text-gray-600">
                  Chưa coi là hoàn thành cho tới khi <b>đã import</b> đủ {batch.target_tracks} WAV hợp lệ
                  (submit / tạo card / bắt đầu tải đều CHƯA phải xong).
                </p>
              )}
              {isBlocking && (
                <p className="rounded bg-amber-950/20 p-2 text-xs text-amber-300 break-words">
                  ⚠️ {phase ? PHASE_LABEL[phase] : ""}. Xử lý (đăng nhập / kiểm tra) rồi bấm
                  <b> Tiếp tục</b>. Tool không tự bấm lại Create khi không chắc đã gửi.
                </p>
              )}
            </div>
          ) : queued ? null : (
            <p className="text-xs text-gray-500">Chưa có batch Suno nào cho project này.</p>
          )}
          {queued && !batch && (
            <p className="mt-2 rounded bg-sky-950/30 p-2 text-xs text-sky-300 break-words">
              {queued}
            </p>
          )}
        </Section>
      </div>

      {/* Cột phải: model/params/limits + hành động */}
      <div className="space-y-4">
        <Section title="Model & thông số tạo">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">
                Model ưu tiên
              </span>
              <input
                type="text"
                value={model}
                disabled={busy}
                onChange={(e) => setModel(e.target.value)}
                placeholder="v6"
                className="w-full rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 focus:border-accent focus:outline-none disabled:opacity-50"
              />
            </label>
            {numField("Số track mục tiêu", target, setTarget)}
            {numField("Thời lượng min (s)", durMin, setDurMin, "ưu tiên (mềm)")}
            {numField("Thời lượng max (s)", durMax, setDurMax, "ưu tiên (mềm)")}
            {numField("Tối thiểu (s)", durMinimum, setDurMinimum, "loại bài ngắn hơn")}
          </div>
          <div className="mt-3 space-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" className="h-4 w-4 accent-accent" checked={allowFallback}
                     disabled={busy} onChange={(e) => setAllowFallback(e.target.checked)} />
              Cho phép dùng model khác nếu không chọn được model ưu tiên
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" className="h-4 w-4 accent-accent" checked={maxMode}
                     disabled readOnly />
              Max Mode: luôn bật (giữ nhất quán nhạc cụ suốt bài — tốn gấp đôi credit, ~20/lượt Create)
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" className="h-4 w-4 accent-accent" checked={autoContinue}
                     disabled={busy} onChange={(e) => setAutoContinue(e.target.checked)} />
              Tự chạy tiếp sau khi import đủ 15 WAV: Mix 15 track → render Video
            </label>
          </div>
        </Section>

        <Section title="Ngân sách (chốt an toàn — xác nhận trước khi chạy live)">
          <div className="grid grid-cols-3 gap-3">
            {numField("Max lượt Create", maxCreate, setMaxCreate)}
            {numField("Max credit tạo", maxCredits, setMaxCredits, "tham khảo · 20/lượt Create")}
            {numField("Max lượt tải", maxDownloads, setMaxDownloads)}
          </div>
          <p className="mt-2 text-[11px] text-gray-600">
            Tool dừng khi chạm trần lượt Create / lượt tải, khi còn &lt;20 credit hoặc khi
            Suno báo hết lượt tải. WAV tải qua Studio (Premier: không giới hạn lượt tải).
            Không tự mua thêm credit/lượt tải.
          </p>
        </Section>

        <Section title="Thao tác">
          <div className="space-y-2">
            {/* Mở UI Suno để đăng nhập + tự kiểm tra/sửa selector khi giao diện đổi. */}
            {loginBusy ? (
              <button
                className={`${danger} w-full`}
                disabled={closeBrowser.isPending}
                onClick={() => closeBrowser.mutate()}
              >
                ✕ Đóng trình duyệt (đang mở để đăng nhập / kiểm tra selector)
              </button>
            ) : (
              <button
                className={`${ghost} w-full`}
                disabled={busy || openBrowser.isPending}
                onClick={() => openBrowser.mutate()}
              >
                🌐 Mở UI Suno để đăng nhập &amp; kiểm tra/cập nhật selector (không tốn credit)
              </button>
            )}

            <button
              className={`${ghost} w-full`}
              disabled={busy || dryRun.isPending}
              onClick={() => dryRun.mutate(buildBody())}
            >
              🧪 Dry-run (điền form + đọc credits — KHÔNG tạo/tải, không tốn credit)
            </button>

            <button
              className={`${primary} w-full`}
              disabled={busy || start.isPending}
              onClick={() => {
                if (
                  confirm(
                    `Chạy LIVE: mở Suno, tạo tới ${maxCreate} lượt Create để đủ ${target} bài, ` +
                    `tải WAV, xác thực, import. Hạn mức: ${maxCredits} credit, ${maxDownloads} lượt tải. Tiếp tục?`,
                  )
                ) {
                  start.mutate(buildBody());
                }
              }}
            >
              ▶ Start Suno Project (LIVE — tạo & tải 15 WAV)
            </button>
            {actionError && (
              <p className="rounded bg-red-950/30 p-2 text-xs text-red-300 break-words">
                ⚠️ {actionError}
              </p>
            )}

            <div className="grid grid-cols-3 gap-2 pt-1">
              <button className={ghost} disabled={!busy || pause.isPending}
                      onClick={() => pause.mutate()}>
                ⏸ Tạm dừng
              </button>
              <button className={ghost} disabled={!canResume || resume.isPending}
                      onClick={() => resume.mutate()}>
                ⏵ Tiếp tục
              </button>
              <button className={danger}
                      disabled={(!busy && (batch == null || isTerminal)) || cancel.isPending}
                      onClick={() => {
                        if (confirm("Huỷ hẳn batch Suno này? (không thể resume)")) {
                          cancel.mutate();
                        }
                      }}>
                ✕ Huỷ
              </button>
            </div>
          </div>
        </Section>

        <Section title="An toàn & lưu ý">
          <ul className="list-disc space-y-1 pl-4 text-xs text-gray-500">
            <li>Lần đầu, cửa sổ trình duyệt mở để bạn <b>tự đăng nhập Suno</b> (session lưu lại, profile riêng — không dùng chung Chrome cá nhân).</li>
            <li>Chỉ WAV tải chính chủ từ Suno + xác thực bằng ffprobe (đúng container WAV, có audio, đủ thời lượng, không lặng, không trùng) mới được import.</li>
            <li>Dry-run an toàn tuyệt đối — không bấm Create, không tải, không tốn credit.</li>
            <li>Gặp CAPTCHA / yêu cầu xác minh / bị chặn → tool dừng và chờ bạn xử lý.</li>
            <li>Vẫn giữ nguyên khả năng import nhạc thủ công ở tab <b>Audio</b>.</li>
          </ul>
        </Section>
      </div>
    </div>
  );
}
