import { useEffect, useState } from "react";
import {
  useGpmConfig, useUpdateGpmConfig, useGpmProfiles,
  useChannelMappings, useCreateMapping, useUpdateMapping, useDeleteMapping,
  useChannelLooks, useCreateLook, useUpdateLook, useDeleteLook, apiErrorText,
  type ChannelMapping, type MappingBody, type ChannelLook, type CustomLookBody,
} from "../../api/youtube";
import { useBatchStyles } from "../../api/projects";
import MusicLineSelect from "../shared/MusicLineSelect";

const inputCls =
  "w-full bg-[#0f1117] border border-white/10 rounded-lg px-2.5 py-1.5 text-sm " +
  "text-white placeholder:text-gray-600 focus:outline-none focus:border-violet-500";

const emptyMapping: MappingBody = {
  music_line: "",
  music_style: "",
  gpm_profile_id: "",
  channel_name: "",
  default_hashtags: "",
  language: "en-US",
  visual_look: "",
};

const NEW_LOOK = "__new__";
function channelNames(l: ChannelLook, mappings: ChannelMapping[], ownId: number | null) {
  return mappings
    .filter((m) => m.id !== ownId && l.used_by_ids.includes(m.id))
    .map((m) => m.channel_name || m.music_line || m.instrument);
}

// Thêm / sửa phong cách tự nhập: tên + mô tả (tiếng Việt được — backend dịch sang
// tiếng Anh cho model ảnh) + look gốc (font · màu · chuyển cảnh) + kiểu ảnh.
function LookEditor({
  editKey,
  initial,
  onDone,
  onCancel,
}: {
  editKey?: string;
  initial?: CustomLookBody;
  onDone: (key: string) => void;
  onCancel: () => void;
}) {
  const { data: looks } = useChannelLooks();
  const { data: styleData } = useBatchStyles();
  const create = useCreateLook();
  const update = useUpdateLook();
  const [body, setBody] = useState<CustomLookBody>(
    initial ?? { label: "", description: "", base: "painterly", styles: [], icon: "🎨" });
  const builtins = (looks ?? []).filter((l) => !l.custom);
  const baseLook = builtins.find((l) => l.key === body.base);
  const busy = create.isPending || update.isPending;
  const err = create.error ?? update.error;

  function toggleStyle(key: string) {
    setBody((b) => ({
      ...b,
      styles: b.styles.includes(key) ? b.styles.filter((x) => x !== key) : [...b.styles, key],
    }));
  }
  function save() {
    const done = (r: { data: unknown }) => onDone((r.data as ChannelLook).key);
    if (editKey) update.mutate({ key: editKey, body }, { onSuccess: done });
    else create.mutate(body, { onSuccess: done });
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-violet-500/30 bg-violet-950/10 p-2.5">
      <div className="text-xs font-semibold text-violet-200">
        {editKey ? "✏️ Sửa phong cách tự nhập" : "✍️ Nhập phong cách mới"}
      </div>
      <div className="flex gap-2">
        <input className={`${inputCls} !w-14 text-center`} value={body.icon} maxLength={8}
               title="Biểu tượng" onChange={(e) => setBody({ ...body, icon: e.target.value })} />
        <input className={inputCls} placeholder="Tên phong cách * (vd: Đêm trăng cổ trấn)"
               value={body.label} maxLength={40}
               onChange={(e) => setBody({ ...body, label: e.target.value })} />
      </div>
      <textarea className={`${inputCls} min-h-[64px]`} maxLength={400}
                placeholder="Mô tả thẩm mỹ (gõ tiếng Việt được, tool tự dịch sang tiếng Anh cho model vẽ) — vd: màu chàm và vàng đèn lồng, sương mỏng, ánh trăng bạc, cảm giác tĩnh lặng hoài cổ"
                value={body.description}
                onChange={(e) => setBody({ ...body, description: e.target.value })} />
      <div>
        <div className="mb-0.5 text-[11px] font-medium text-gray-400">
          Kế thừa font chữ thumbnail · bảng màu · chuyển cảnh &amp; tốc độ video từ:
        </div>
        <select className={inputCls} value={body.base}
                onChange={(e) => setBody({ ...body, base: e.target.value })}>
          {builtins.map((l) => (
            <option key={l.key} value={l.key}>{l.icon} {l.label}</option>
          ))}
        </select>
      </div>
      <div>
        <div className="mb-1 text-[11px] font-medium text-gray-400">
          Kiểu ảnh của phong cách (chọn 1 hoặc nhiều — video mới xoay vòng; bỏ trống = theo
          «{baseLook?.label ?? body.base}»{baseLook ? `: ${baseLook.styles.map((x) => x.label).join(" / ")}` : ""})
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(styleData?.styles ?? []).map((st) => {
            const on = body.styles.includes(st.key);
            return (
              <button key={st.key} type="button" onClick={() => toggleStyle(st.key)}
                      title={st.desc}
                      className={`rounded-full border px-2 py-0.5 text-[11px] ${on
                        ? "border-violet-400 bg-violet-600/30 text-violet-100"
                        : "border-white/15 text-gray-400 hover:bg-white/5"}`}>
                {st.icon} {st.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={save} disabled={busy || !body.label.trim()}
                className="rounded-lg bg-violet-600 px-3 py-1 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40">
          {busy ? "⏳ Đang lưu (dịch mô tả)…" : "💾 Lưu phong cách"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}
                className="rounded-lg border border-white/15 px-3 py-1 text-xs text-gray-300 hover:bg-white/5 disabled:opacity-40">
          Huỷ
        </button>
      </div>
      {err && <p className="text-xs text-red-300">{apiErrorText(err)}</p>}
    </div>
  );
}

// Chọn phong cách hình ảnh của kênh — chọn TỰ DO (kênh khác đang dùng chỉ là gợi ý,
// vẫn chọn được) hoặc ✍️ nhập phong cách mới ngay tại đây.
function LookSelect({
  value,
  looks,
  mappings,
  ownId,
  allowAuto,
  onChange,
}: {
  value: string;
  looks: ChannelLook[];
  mappings: ChannelMapping[];
  ownId: number | null;
  allowAuto?: boolean;
  onChange: (v: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const cur = looks.find((l) => l.key === value);
  const shared = cur ? channelNames(cur, mappings, ownId) : [];
  const option = (l: ChannelLook) => {
    const names = channelNames(l, mappings, ownId);
    return (
      <option key={l.key} value={l.key}>
        {l.icon} {l.label}
        {names.length ? ` · đang dùng: ${names.join(", ")}` : ""}
      </option>
    );
  };
  const builtins = looks.filter((l) => !l.custom);
  const customs = looks.filter((l) => l.custom);
  return (
    <div>
      <select value={creating ? NEW_LOOK : value} className={inputCls}
              onChange={(e) => {
                if (e.target.value === NEW_LOOK) setCreating(true);
                else { setCreating(false); onChange(e.target.value); }
              }}>
        {allowAuto && <option value="">🎲 Tự gán phong cách còn trống</option>}
        {!allowAuto && !cur && <option value="">— chọn phong cách kênh —</option>}
        <optgroup label="Phong cách có sẵn">{builtins.map(option)}</optgroup>
        {customs.length > 0 && <optgroup label="Phong cách tự nhập">{customs.map(option)}</optgroup>}
        <option value={NEW_LOOK}>✍️ Nhập phong cách mới…</option>
      </select>
      {cur && !creating && (
        <p className="mt-1 text-[11px] text-gray-500">
          {cur.desc} · Ảnh: {cur.styles.map((s) => `${s.icon} ${s.label}`).join(" / ")}
          {shared.length > 0 && (
            <span className="text-amber-300/80">
              {" "}· Dùng chung với {shared.join(", ")} — cùng kiểu ảnh / màu video (mỗi video
              vẫn tự đổi bố cục, font, màu chữ).
            </span>
          )}
        </p>
      )}
      {creating && (
        <LookEditor onCancel={() => setCreating(false)}
                    onDone={(key) => { setCreating(false); onChange(key); }} />
      )}
    </div>
  );
}

// Danh sách phong cách tự nhập: thêm / sửa / xoá (kênh đang dùng → về look gốc).
function CustomLooks({ looks }: { looks: ChannelLook[] }) {
  const del = useDeleteLook();
  const [editing, setEditing] = useState<string | null>(null);
  const customs = looks.filter((l) => l.custom);
  const label = (key: string) => looks.find((l) => l.key === key)?.label ?? key;
  return (
    <div className="mt-3 rounded-lg border border-white/10 bg-[#0f1117] p-2.5">
      <div className="mb-1 flex items-center justify-between">
        <div className="text-xs font-semibold text-gray-400">🎨 Phong cách tự nhập</div>
        {editing !== NEW_LOOK && (
          <button type="button" onClick={() => setEditing(NEW_LOOK)}
                  className="rounded-lg border border-white/15 px-2.5 py-1 text-xs text-gray-200 hover:bg-white/5">
            ✍️ Nhập phong cách mới
          </button>
        )}
      </div>
      {customs.length === 0 && editing !== NEW_LOOK && (
        <p className="text-xs text-gray-500">
          Chưa có. Phong cách tự nhập = tên + mô tả thẩm mỹ của bạn + kiểu ảnh tự chọn, kế thừa
          font / màu / chuyển cảnh của 1 phong cách có sẵn.
        </p>
      )}
      {editing === NEW_LOOK && (
        <LookEditor onCancel={() => setEditing(null)} onDone={() => setEditing(null)} />
      )}
      <div className="space-y-1.5">
        {customs.map((l) => (
          <div key={l.key} className="rounded border border-white/5 p-2">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1 text-xs">
                <div className="text-gray-200">
                  {l.icon} <b>{l.label}</b>
                  <span className="text-gray-500"> · gốc {label(l.base)}</span>
                  {l.used_by && <span className="text-gray-500"> · kênh: {l.used_by}</span>}
                </div>
                <div className="text-[11px] text-gray-500">
                  {l.user_desc || "(không mô tả — dùng mô tả của phong cách gốc)"} · Ảnh:{" "}
                  {l.styles.map((s) => `${s.icon} ${s.label}`).join(" / ")}
                </div>
              </div>
              <button type="button" onClick={() => setEditing(l.key)}
                      className="rounded border border-white/15 px-2 py-0.5 text-[11px] text-gray-300 hover:bg-white/5">
                ✏️ Sửa
              </button>
              <button type="button" disabled={del.isPending}
                      onClick={() => {
                        const n = l.used_by_ids.length;
                        if (confirm(`Xoá phong cách «${l.label}»?` + (n
                          ? ` ${n} kênh đang dùng sẽ về phong cách gốc «${label(l.base)}».` : "")))
                          del.mutate(l.key);
                      }}
                      className="rounded border border-red-500/30 px-2 py-0.5 text-[11px] text-red-400 hover:bg-red-950/30 disabled:opacity-40">
                🗑
              </button>
            </div>
            {editing === l.key && (
              <LookEditor editKey={l.key}
                          initial={{ label: l.label, description: l.user_desc, base: l.base,
                                     styles: l.styles.map((s) => s.key), icon: l.icon }}
                          onCancel={() => setEditing(null)} onDone={() => setEditing(null)} />
            )}
          </div>
        ))}
      </div>
      {del.isError && <p className="mt-1 text-xs text-red-300">{apiErrorText(del.error)}</p>}
    </div>
  );
}

function ProfileSelect({
  value,
  profiles,
  onChange,
}: {
  value: string;
  profiles: { id: string; name: string; group_name?: string }[];
  onChange: (v: string) => void;
}) {
  // Nếu profile đã chọn không còn trong danh sách (API tắt), vẫn hiển thị id.
  const known = profiles.some((p) => p.id === value);
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputCls}
    >
      <option value="">— chọn profile —</option>
      {!known && value && <option value={value}>{value} (không thấy)</option>}
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.group_name ? `[${p.group_name}] ` : ""}
          {p.name}
        </option>
      ))}
    </select>
  );
}

export function YoutubeGpmSettings() {
  const { data: config } = useGpmConfig();
  const updateConfig = useUpdateGpmConfig();
  const { data: profilesResult, refetch: refetchProfiles, isFetching } =
    useGpmProfiles();
  const { data: mappings } = useChannelMappings();
  const { data: looks } = useChannelLooks();
  const createMapping = useCreateMapping();
  const updateMapping = useUpdateMapping();
  const deleteMapping = useDeleteMapping();

  const [baseUrl, setBaseUrl] = useState("");
  const [exePath, setExePath] = useState("");
  const [cfgDirty, setCfgDirty] = useState(false);

  useEffect(() => {
    if (config) {
      setBaseUrl(config.gpm_api_base_url);
      setExePath(config.gpm_exe_path ?? "");
      setCfgDirty(false);
    }
  }, [config]);

  const profiles = profilesResult?.profiles ?? [];

  // Form thêm mới.
  const [newRow, setNewRow] = useState<MappingBody>(emptyMapping);
  // Bản nháp khi sửa từng dòng (id → body).
  const [edits, setEdits] = useState<Record<number, MappingBody>>({});

  function rowBody(m: ChannelMapping): MappingBody {
    return (
      edits[m.id] ?? {
        music_line: m.music_line ?? "",
        music_style: m.music_style,
        gpm_profile_id: m.gpm_profile_id,
        channel_name: m.channel_name,
        default_hashtags: m.default_hashtags,
        language: m.language,
        visual_look: m.visual_look ?? "",
      }
    );
  }
  function setRow(id: number, patch: Partial<MappingBody>) {
    setEdits((prev) => ({
      ...prev,
      [id]: { ...rowBody(mappings!.find((x) => x.id === id)!), ...prev[id], ...patch },
    }));
  }

  return (
    <div className="space-y-4 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">
        📺 YouTube / GPMLogin
      </h3>
      <p className="text-xs text-amber-300/80">
        ⓘ Phần này lưu bằng các nút riêng bên dưới (💾 Lưu cấu hình GPM · 💾 Lưu từng
        kênh · ➕ Thêm) — <b>không</b> dùng nút "Lưu API (OpenAI)" ở cuối.
      </p>

      {/* GPM config */}
      <div className="space-y-2 rounded-lg border border-white/5 bg-[#0f1117] p-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">
            GPMLogin Local API base URL
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => { setBaseUrl(e.target.value); setCfgDirty(true); }}
            placeholder="http://localhost:9495"
            className={inputCls}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">
            Đường dẫn GPMLogin.exe (tuỳ chọn — để tự mở app nếu chưa chạy)
          </label>
          <input
            type="text"
            value={exePath}
            onChange={(e) => { setExePath(e.target.value); setCfgDirty(true); }}
            placeholder="C:\\...\\GPMLogin.exe"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() =>
              updateConfig.mutate(
                { gpm_api_base_url: baseUrl.trim(), gpm_exe_path: exePath.trim() },
                { onSuccess: () => { setCfgDirty(false); refetchProfiles(); } }
              )
            }
            disabled={updateConfig.isPending || !cfgDirty}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            💾 Lưu cấu hình GPM
          </button>
          <button
            onClick={() => refetchProfiles()}
            disabled={isFetching}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            {isFetching ? "⏳ Đang kiểm tra…" : "🔌 Test kết nối / tải profiles"}
          </button>
        </div>
        {profilesResult && (
          <p className={`text-xs ${profilesResult.ok ? "text-green-400" : "text-yellow-400"}`}>
            {profilesResult.ok
              ? `✅ Thấy ${profiles.length} profile (nguồn: ${profilesResult.base_url}).`
              : `⚠️ Không gọi được API (${profilesResult.error ?? "lỗi"}). Đã thử đọc từ database.db.`}
          </p>
        )}
        <p className="text-xs text-gray-600">
          Bật <b>Local API</b> trong GPMLogin để tool mở profile + lấy CDP. Khi API tắt,
          danh sách profile vẫn đọc được từ database.db (đủ để cấu hình ánh xạ).
        </p>
      </div>

      {/* Mapping table */}
      <div>
        <div className="mb-2 text-sm font-medium text-gray-300">
          Kênh YouTube — tên kênh → profile GPMLogin
        </div>
        <p className="mb-2 text-xs text-gray-500">
          Mỗi project / lô <b>tự chọn kênh</b> để đăng nháp (form New Project hoặc tab Video) —{' '}
          <b>kênh nào cũng nhận dòng nhạc / thể loại nào</b>. Ô «dòng nhạc» (VN, US-UK,
          Chinese, Japanese…) và «thể loại gợi ý» để tool <b>tự chọn kênh</b> cho project
          chưa chọn kênh: ưu tiên kênh cùng dòng nhạc của bài, rồi tới thể loại (cả 2 tuỳ
          chọn, bỏ trống được).
        </p>
        <p className="mb-2 text-xs text-gray-500">
          Mỗi kênh chọn <b>1 phong cách hình ảnh</b> (kiểu ảnh, kiểu chữ thumbnail, bảng
          màu, chuyển cảnh và tốc độ video) — <b>chọn tự do</b>, nhiều kênh dùng chung được,
          hoặc <b>✍️ tự nhập phong cách mới</b>. Trong cùng kênh, mỗi video lại tự đổi bố
          cục / font / màu chữ để không lặp lại.
        </p>

        <div className="space-y-2">
          {(mappings ?? []).map((m) => {
            const body = rowBody(m);
            const dirty = JSON.stringify(body) !== JSON.stringify({
              music_line: m.music_line ?? "", music_style: m.music_style,
              gpm_profile_id: m.gpm_profile_id, channel_name: m.channel_name,
              default_hashtags: m.default_hashtags, language: m.language,
              visual_look: m.visual_look ?? "",
            });
            return (
              <div key={m.id} className="rounded-lg border border-white/10 bg-[#0f1117] p-2.5">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <MusicLineSelect className={inputCls} value={body.music_line}
                                   onChange={(v) => setRow(m.id, { music_line: v })} />
                  <input className={inputCls} placeholder="thể loại gợi ý (tuỳ chọn)" value={body.music_style}
                         onChange={(e) => setRow(m.id, { music_style: e.target.value })} />
                  <input className={inputCls} placeholder="ngôn ngữ (en-US)" value={body.language}
                         onChange={(e) => setRow(m.id, { language: e.target.value })} />
                  <div className="col-span-2 sm:col-span-3">
                    <ProfileSelect value={body.gpm_profile_id} profiles={profiles}
                                   onChange={(v) => setRow(m.id, { gpm_profile_id: v })} />
                  </div>
                  <input className={inputCls} placeholder="tên kênh *" value={body.channel_name}
                         onChange={(e) => setRow(m.id, { channel_name: e.target.value })} />
                  <input className={`${inputCls} col-span-1 sm:col-span-2`} placeholder="#hashtag mặc định"
                         value={body.default_hashtags}
                         onChange={(e) => setRow(m.id, { default_hashtags: e.target.value })} />
                  <div className="col-span-2 sm:col-span-3">
                    <div className="mb-0.5 text-[11px] font-medium text-gray-400">
                      🎨 Phong cách hình ảnh của kênh (thumbnail · ảnh · màu & chuyển cảnh video)
                    </div>
                    <LookSelect value={body.visual_look ?? ""} looks={looks ?? []}
                                mappings={mappings ?? []} ownId={m.id}
                                onChange={(v) => setRow(m.id, { visual_look: v })} />
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={() => updateMapping.mutate({ id: m.id, body },
                      { onSuccess: () => setEdits((p) => { const n = { ...p }; delete n[m.id]; return n; }) })}
                    disabled={!dirty || updateMapping.isPending}
                    className="rounded-lg border border-white/15 px-2.5 py-1 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
                  >
                    💾 Lưu
                  </button>
                  <button
                    onClick={() => { if (confirm("Xoá kênh này? Project đã chọn kênh này sẽ về «Tự chọn kênh».")) deleteMapping.mutate(m.id); }}
                    disabled={deleteMapping.isPending}
                    className="rounded-lg border border-red-500/30 px-2.5 py-1 text-xs font-medium text-red-400 hover:bg-red-950/30 disabled:opacity-40"
                  >
                    🗑 Xoá
                  </button>
                  {dirty && <span className="text-xs text-yellow-300">chưa lưu</span>}
                </div>
                {updateMapping.isError && updateMapping.variables?.id === m.id && (
                  <p className="mt-1 text-xs text-red-300">{apiErrorText(updateMapping.error)}</p>
                )}
              </div>
            );
          })}
          {(mappings ?? []).length === 0 && (
            <p className="text-xs text-gray-500">Chưa có kênh nào.</p>
          )}
        </div>

        {/* Add new */}
        <div className="mt-3 rounded-lg border border-dashed border-white/15 bg-[#0f1117] p-2.5">
          <div className="mb-1.5 text-xs font-semibold text-gray-400">➕ Thêm kênh mới</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <MusicLineSelect className={inputCls} key={createMapping.submittedAt} value={newRow.music_line}
                             onChange={(v) => setNewRow({ ...newRow, music_line: v })} />
            <input className={inputCls} placeholder="thể loại gợi ý (tuỳ chọn)" value={newRow.music_style}
                   onChange={(e) => setNewRow({ ...newRow, music_style: e.target.value })} />
            <input className={inputCls} placeholder="ngôn ngữ (en-US)" value={newRow.language}
                   onChange={(e) => setNewRow({ ...newRow, language: e.target.value })} />
            <div className="col-span-2 sm:col-span-3">
              <ProfileSelect value={newRow.gpm_profile_id} profiles={profiles}
                             onChange={(v) => setNewRow({ ...newRow, gpm_profile_id: v })} />
            </div>
            <input className={inputCls} placeholder="tên kênh *" value={newRow.channel_name}
                   onChange={(e) => setNewRow({ ...newRow, channel_name: e.target.value })} />
            <input className={`${inputCls} col-span-1 sm:col-span-2`} placeholder="#hashtag mặc định"
                   value={newRow.default_hashtags}
                   onChange={(e) => setNewRow({ ...newRow, default_hashtags: e.target.value })} />
            <div className="col-span-2 sm:col-span-3">
              <LookSelect value={newRow.visual_look ?? ""} looks={looks ?? []}
                          mappings={mappings ?? []} ownId={null} allowAuto
                          onChange={(v) => setNewRow({ ...newRow, visual_look: v })} />
            </div>
          </div>
          <button
            onClick={() => createMapping.mutate(
              { ...newRow, language: newRow.language.trim() || "en-US" },
              { onSuccess: () => setNewRow(emptyMapping) })}
            disabled={
              createMapping.isPending ||
              !(newRow.channel_name.trim() || newRow.music_line.trim()) || !newRow.gpm_profile_id
            }
            className="mt-2 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40"
          >
            ➕ Thêm
          </button>
          {createMapping.isError && (
            <p className="mt-1 text-xs text-red-300">{apiErrorText(createMapping.error)}</p>
          )}
        </div>

        <CustomLooks looks={looks ?? []} />
      </div>
    </div>
  );
}
