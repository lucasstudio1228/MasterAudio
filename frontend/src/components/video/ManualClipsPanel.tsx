import { useRef, useState } from "react";
import {
  useManualClips, useStartManualClips, useImportClip, useCancelVideo,
} from "../../api/video";
import type { VideoStatus } from "../../types";

// "Tạo video thủ công" — chế độ bán tự động khi Flow gắn cờ bot:
//   tool mở Flow + dựng project + đưa 2 bảng nhân vật (và ảnh bìa cho clip 00)
//   vào ô soạn → BẠN dán prompt + bấm Tạo → tool tự tải về, đặt tên clip_NN.mp4
//   rồi chuẩn bị clip kế. Tool KHÔNG gõ prompt, KHÔNG bấm Tạo.

const ING_LABEL: Record<number, string> = {
  0: "Ảnh bìa (0)", 1: "Bảng nhân vật (1)", 2: "Bảng linh thú (2)",
};

const PHASE_LABEL: Record<string, string> = {
  opening: "Đang mở Flow + dựng project…",
  preparing: "Đang đưa ảnh tham chiếu vào ô soạn…",
  waiting_user: "Chờ bạn dán prompt + bấm Tạo trên Flow",
  downloading: "Đang tải clip về…",
  done: "Xong",
  error: "Lỗi",
  stopped: "Đã dừng",
};

export default function ManualClipsPanel({
  projectId, st, busy, imagesDone,
}: {
  projectId: number;
  st: VideoStatus;
  busy: boolean;
  imagesDone: boolean;
}) {
  const manual = st.manual ?? null;
  const active = !!manual?.active;
  const isManualJob = busy && st.job?.kind === "clips-manual";
  const [open, setOpen] = useState(false);
  const list = useManualClips(projectId, active);
  const start = useStartManualClips(projectId);
  const importClip = useImportClip(projectId);
  const cancel = useCancelVideo(projectId);
  const [copied, setCopied] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [importIdx, setImportIdx] = useState<number | null>(null);

  const btn = "rounded-lg px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const primary = `${btn} bg-accent text-white hover:bg-accent-hover`;
  const ghost = `${btn} bg-surface-2 text-gray-200 hover:bg-white/10`;
  const small = "rounded px-2 py-1 text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";

  const copy = async (idx: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback khi clipboard API bị chặn (http không phải localhost…)
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(idx);
    setTimeout(() => setCopied((c) => (c === idx ? null : c)), 1500);
  };

  const pickFile = (idx: number) => {
    setImportIdx(idx);
    fileRef.current?.click();
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f && importIdx !== null) importClip.mutate({ index: importIdx, file: f });
  };

  // Được nhập file khi rảnh, hoặc khi chính phiên thủ công đang chạy.
  const canImport = !busy || isManualJob;
  const clips = list.data?.clips ?? [];
  const current = manual?.current ?? null;
  const errMsg = (e: unknown) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ?? (e as Error)?.message ?? String(e);

  return (
    <div className="rounded-lg border border-sky-500/20 bg-sky-950/10 p-3">
      <input ref={fileRef} type="file" accept="video/mp4,.mp4" className="hidden" onChange={onFile} />
      <div className="mb-1.5 text-xs font-semibold text-sky-300">
        ✋ Tạo video thủ công (khi Flow gắn cờ “hoạt động bất thường”)
      </div>
      <p className="mb-2 text-[11px] leading-relaxed text-gray-400">
        Tool mở Flow, dựng project, đưa <b>2 bảng phân tích nhân vật</b> (clip 00 thêm ảnh bìa)
        vào ô soạn. <b>Bạn</b> bấm <i>Copy</i> prompt bên dưới → dán vào Flow → bấm Tạo.
        Tool tự phát hiện clip mới, <b>tải về + đặt tên</b> <code>clip_NN.mp4</code> rồi chuẩn bị clip kế.
        Đủ {st.params.total_clips} clip + có nhạc → tự ghép video.
      </p>

      <div className="grid grid-cols-2 gap-2">
        {isManualJob ? (
          <button className={`${ghost} w-full`} disabled={cancel.isPending}
                  onClick={() => { if (confirm("Dừng chế độ thủ công? Clip đã tải vẫn giữ nguyên.")) cancel.mutate(); }}>
            ■ Dừng chế độ thủ công
          </button>
        ) : (
          <button className={`${primary} w-full`} disabled={busy || !imagesDone || start.isPending}
                  onClick={() => { setOpen(true); start.mutate({}); }}>
            ✋ Tạo video thủ công
          </button>
        )}
        <button className={`${ghost} w-full`} onClick={() => setOpen((o) => !o)}>
          {open ? "Ẩn danh sách prompt" : `Xem ${st.params.total_clips} prompt`}
        </button>
      </div>
      {!imagesDone && (
        <p className="mt-1.5 text-[11px] text-amber-300/80">{st.params.single_image ? "Cần đủ 3 ảnh (bìa + bảng nhân vật + bảng bối cảnh) trước." : "Cần đủ 3 ảnh (bìa + 2 bảng nhân vật) trước."}</p>
      )}
      {start.isError && (
        <p className="mt-1.5 text-[11px] text-red-400">Không bắt đầu được: {errMsg(start.error)}</p>
      )}
      {importClip.isError && (
        <p className="mt-1.5 text-[11px] text-red-400">Nhập file lỗi: {errMsg(importClip.error)}</p>
      )}
      {importClip.isSuccess && !importClip.isPending && (
        <p className="mt-1.5 text-[11px] text-green-400">Đã nhập file clip.</p>
      )}

      {manual && (active || manual.phase === "error") && (
        <div className="mt-3 space-y-2 rounded-lg border border-white/10 bg-background p-3">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-gray-200">
              {PHASE_LABEL[manual.phase] ?? manual.phase}
              {current !== null && <> — <span className="text-sky-300">clip_{String(current).padStart(2, "0")}</span></>}
            </span>
            <span className="text-gray-400">
              {manual.done.length}/{manual.total} clip · còn {manual.todo.length}
            </span>
          </div>
          {manual.message && <p className="text-[11px] text-gray-400">{manual.message}</p>}
          {manual.blocked && (
            <p className="rounded border border-amber-500/30 bg-amber-950/30 px-2 py-1.5 text-[11px] text-amber-300">
              ⚠ {manual.blocked}
            </p>
          )}
          {active && current !== null && (
            <>
              <div className="text-[11px] text-gray-400">
                Ảnh đã đưa vào ô soạn: {(manual.ingredients ?? []).map((i) => ING_LABEL[i] ?? i).join(" + ") || "—"}
              </div>
              <textarea readOnly value={manual.prompt}
                        className="h-28 w-full resize-y rounded border border-white/10 bg-surface-2 p-2 font-mono text-[11px] text-gray-200" />
              <div className="flex flex-wrap gap-2">
                <button className={`${small} bg-accent text-white hover:bg-accent-hover`}
                        onClick={() => copy(current, manual.prompt)}>
                  {copied === current ? "✓ Đã copy" : "📋 Copy prompt clip hiện tại"}
                </button>
                <button className={`${small} bg-surface-2 text-gray-200 hover:bg-white/10`}
                        disabled={!canImport || importClip.isPending}
                        onClick={() => pickFile(current)}>
                  📁 Nhập file cho clip này
                </button>
              </div>
              <p className="text-[11px] text-gray-500">
                Đừng đóng cửa sổ Cốc Cốc Flow của tool. Chỉ bấm Tạo 1 lần cho mỗi clip, chờ tool tải xong
                rồi mới dán prompt kế.
              </p>
            </>
          )}
        </div>
      )}

      {open && (
        <div className="mt-3 space-y-1.5">
          {list.isLoading && <p className="text-[11px] text-gray-400">Đang tải danh sách prompt…</p>}
          {list.isError && <p className="text-[11px] text-red-400">{errMsg(list.error)}</p>}
          {clips.map((c) => {
            const isCur = active && c.index === current;
            const tag = `clip_${String(c.index).padStart(2, "0")}`;
            return (
              <div key={c.index}
                   className={`rounded border px-2 py-1.5 ${isCur ? "border-sky-400/60 bg-sky-950/30" : c.done ? "border-green-500/20 bg-green-950/10" : "border-white/5 bg-background"}`}>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className={`font-mono font-semibold ${c.done ? "text-green-400" : "text-gray-200"}`}>
                    {c.done ? "✓" : "○"} {tag}
                  </span>
                  <span className="text-gray-500">
                    {c.ingredients.map((i) => ING_LABEL[i] ?? i).join(" + ")}
                  </span>
                  {isCur && <span className="text-sky-300">← đang chờ</span>}
                  <span className="ml-auto flex gap-1">
                    <button className={`${small} bg-surface-2 text-gray-200 hover:bg-white/10`}
                            onClick={() => copy(c.index, c.prompt)}>
                      {copied === c.index ? "✓ Đã copy" : "📋 Copy"}
                    </button>
                    <button className={`${small} bg-surface-2 text-gray-200 hover:bg-white/10`}
                            disabled={!canImport || importClip.isPending}
                            title="Chọn file .mp4 bạn tự tải từ Flow — tool đặt tên đúng clip này"
                            onClick={() => pickFile(c.index)}>
                      📁 Nhập file
                    </button>
                    {c.done && (
                      <button className={`${small} bg-surface-2 text-gray-200 hover:bg-white/10`}
                              disabled={busy || !imagesDone}
                              title="Làm lại riêng clip này ở chế độ thủ công (không ghép video)"
                              onClick={() => {
                                if (confirm(`Làm lại ${tag} ở chế độ thủ công? File cũ bị thay khi có clip mới.`)) {
                                  start.mutate({ only: [c.index], assemble: false });
                                }
                              }}>
                        ↻ thủ công
                      </button>
                    )}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] text-gray-400" title={c.prompt}>{c.prompt}</p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
