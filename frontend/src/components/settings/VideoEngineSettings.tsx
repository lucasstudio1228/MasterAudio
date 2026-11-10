import { useEffect, useState } from "react";
import { useVideoEngines, useUpdateVideoEngines, type VideoEnginesUpdate } from "../../api/settings";
import { useGpmProfiles, apiErrorText, type GpmProfile } from "../../api/youtube";

const inputCls =
  "w-full bg-[#0f1117] border border-white/10 rounded-lg px-2.5 py-1.5 text-sm " +
  "text-white placeholder:text-gray-600 focus:outline-none focus:border-violet-500";

const CLIP_HINT: Record<string, string> = {
  muse: "qua profile GPMLogin đã đăng nhập muse.ai",
  flow: "Google Flow (Veo) — qua profile GPMLogin đã đăng nhập Google",
  gemini: "Gemini web — Chrome riêng của tool, không qua GPM (giới hạn ~10 clip/ngày)",
};

type Form = Required<VideoEnginesUpdate>;

function ProfileSelect({ value, onChange, profiles, emptyLabel }: {
  value: string;
  onChange: (v: string) => void;
  profiles: GpmProfile[];
  emptyLabel: string;
}) {
  const known = profiles.some((p) => p.id === value);
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}</option>
      {value && !known && (
        <option value={value}>⚠️ {value} (không thấy trong GPMLogin)</option>
      )}
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}{p.group_name ? ` · ${p.group_name}` : ""}
        </option>
      ))}
    </select>
  );
}

/** Chọn máy tạo clip/ảnh + profile GPMLogin cho Muse.ai và Google Flow. */
export function VideoEngineSettings() {
  const { data } = useVideoEngines();
  const save = useUpdateVideoEngines();
  const { data: gpm, refetch, isFetching } = useGpmProfiles();
  const profiles = gpm?.profiles ?? [];
  const [form, setForm] = useState<Form | null>(null);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (data && !dirty) {
      setForm({
        clip_engine: data.clip_engine,
        image_engine: data.image_engine,
        image_fallback: data.image_fallback,
        muse_gpm_profile_id: data.muse_gpm_profile_id,
        flow_gpm_profile_id: data.flow_gpm_profile_id,
      });
    }
  }, [data, dirty]);

  if (!data || !form) return null;

  function set(patch: Partial<Form>) {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setDirty(true);
    setMsg("");
  }

  function submit() {
    save.mutate(form!, {
      onSuccess: () => { setDirty(false); setMsg("✅ Đã lưu — các video tạo sau sẽ dùng cài đặt này."); },
      onError: (e) => setMsg(`❌ ${apiErrorText(e)}`),
    });
  }

  const needMuse = form.clip_engine === "muse" || form.image_engine === "muse"
    || (form.image_fallback && form.image_engine !== "muse");

  return (
    <div className="space-y-4 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">🎬 Máy tạo video qua GPMLogin</h3>
      <p className="text-xs text-gray-400">
        Chọn máy làm <b>cảnh động (clip)</b> và profile GPMLogin cho từng máy. Mở profile đó
        trong GPMLogin, đăng nhập sẵn trang web (muse.ai / Google Flow) rồi đóng lại.
        Tool sẽ tự mở profile khi tạo video. Lưu bằng nút <b>💾 Lưu máy tạo video</b> bên dưới.
      </p>

      <div className="space-y-3 rounded-lg border border-white/5 bg-[#0f1117] p-3">
        <div>
          <div className="mb-1.5 text-xs font-medium text-gray-400">Máy tạo cảnh động (clip)</div>
          <div className="space-y-1.5">
            {data.clip_engines.map((o) => (
              <label key={o.key} className="flex cursor-pointer items-start gap-2 text-sm text-gray-200">
                <input type="radio" name="clip-engine" className="mt-1"
                       checked={form.clip_engine === o.key}
                       onChange={() => set({ clip_engine: o.key })} />
                <span>
                  <b>{o.label}</b>
                  <span className="text-xs text-gray-500"> — {CLIP_HINT[o.key] ?? ""}</span>
                </span>
              </label>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-gray-400">🟣 Profile GPMLogin cho Muse.ai</div>
          <ProfileSelect value={form.muse_gpm_profile_id} profiles={profiles}
                         emptyLabel="— chưa chọn —"
                         onChange={(v) => set({ muse_gpm_profile_id: v })} />
          {needMuse && !form.muse_gpm_profile_id && (
            <div className="mt-1 text-xs text-red-400">
              ⚠️ Đang dùng Muse.ai nhưng chưa chọn profile — tạo video sẽ báo lỗi.
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-gray-400">🔵 Profile GPMLogin cho Google Flow</div>
          <ProfileSelect value={form.flow_gpm_profile_id} profiles={profiles}
                         emptyLabel="— không dùng GPM (Chrome riêng của tool) —"
                         onChange={(v) => set({ flow_gpm_profile_id: v })} />
          {form.clip_engine === "flow" && !form.flow_gpm_profile_id && (
            <div className="mt-1 text-xs text-amber-300">
              Chưa chọn profile → Flow mở bằng Chrome riêng của tool (phải đăng nhập Google
              trong cửa sổ đó).
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-gray-400">
            Máy vẽ ảnh nền (ảnh bìa không chữ, bảng nhân vật)
          </div>
          <select className={inputCls} value={form.image_engine}
                  onChange={(e) => set({ image_engine: e.target.value })}>
            {data.image_engines.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}{o.key === "muse" ? " (qua profile Muse ở trên)"
                  : o.key === "chatgpt" ? " (cửa sổ ChatGPT của tool — vẽ ảnh bìa chế độ 1 ảnh)"
                  : " (Chrome riêng của tool)"}
              </option>
            ))}
          </select>
          <label className="mt-1.5 flex items-center gap-2 text-xs text-gray-300">
            <input type="checkbox" checked={form.image_fallback}
                   onChange={(e) => set({ image_fallback: e.target.checked })} />
            Máy vẽ ảnh lỗi → tự chuyển sang máy dự phòng (ChatGPT/Gemini → Muse.ai, Muse.ai → Gemini)
          </label>
        </div>

        {gpm && !gpm.ok && (
          <div className="text-xs text-red-400">
            ⚠️ Không đọc được danh sách profile GPMLogin: {gpm.error}. Mở GPMLogin rồi bấm
            «🔄 Tải lại profiles».
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <button onClick={submit} disabled={!dirty || save.isPending}
                  className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
            {save.isPending ? "⏳ Đang lưu…" : "💾 Lưu máy tạo video"}
          </button>
          <button onClick={() => refetch()} disabled={isFetching}
                  className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40">
            {isFetching ? "⏳ Đang tải…" : "🔄 Tải lại profiles"}
          </button>
          {dirty && (
            <button onClick={() => { setDirty(false); setMsg(""); }}
                    className="text-xs text-gray-400 hover:text-gray-200">Huỷ sửa</button>
          )}
          {msg && <span className="text-xs text-gray-300">{msg}</span>}
        </div>
      </div>
      <p className="text-xs text-amber-300/80">
        ⓘ Không chọn trùng profile đang được tool cũ dùng cùng lúc — 2 tool mở chung 1 profile
        sẽ lỗi.
      </p>
    </div>
  );
}
