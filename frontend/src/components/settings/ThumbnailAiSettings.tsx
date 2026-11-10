import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useThumbAISettings, useUpdateThumbAISettings, useTestThumbAI,
  useChatGPTWindow, useOpenChatGPT, useCloseChatGPT,
} from "../../api/settings";
import { apiErrorText } from "../../api/youtube";
import type { ThumbAITestResult, ThumbEngine } from "../../types";

const inputCls =
  "w-full bg-[#0f1117] border border-white/10 rounded-lg px-2.5 py-1.5 text-sm " +
  "text-white placeholder:text-gray-600 focus:outline-none focus:border-violet-500";

const CUSTOM = "__custom__";
const VND_PER_USD = 3250;

export function vndText(usd?: number | null): string {
  if (usd == null) return "";
  return `≈ ${(Math.round((usd * VND_PER_USD) / 10) * 10).toLocaleString("vi-VN")}đ`;
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString("vi-VN");
}

/** Trạng thái đăng nhập ChatGPT (lần kiểm tra gần nhất) — Cài đặt + tab Video. */
export function ChatGPTLoginBadge({ loggedIn, checkedAt }: {
  loggedIn?: boolean | null; checkedAt?: string | null;
}) {
  const when = checkedAt ? ` (kiểm tra ${fmtTime(checkedAt)})` : "";
  if (loggedIn === true)
    return <span className="text-green-400">✅ Đã đăng nhập ChatGPT{when}</span>;
  if (loggedIn === false)
    return <span className="text-yellow-400">⚠️ Chưa đăng nhập ChatGPT{when}</span>;
  return (
    <span className="text-gray-400">
      Chưa kiểm tra đăng nhập ChatGPT — bấm «Mở ChatGPT để đăng nhập».
    </span>
  );
}

/** Nút mở / đóng cửa sổ ChatGPT (profile riêng của tool) để bạn TỰ đăng nhập. */
export function ChatGPTLoginPanel({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const { data: s } = useThumbAISettings();
  const { data: win } = useChatGPTWindow();
  const open = useOpenChatGPT();
  const close = useCloseChatGPT();
  const [error, setError] = useState("");
  const wasOpen = useRef(false);

  // Cửa sổ vừa đóng → làm mới trạng thái đăng nhập ở mọi nơi.
  useEffect(() => {
    if (wasOpen.current && !win?.open) {
      qc.invalidateQueries({ queryKey: ["settings", "thumbnail-ai"] });
      qc.invalidateQueries({ queryKey: ["video"] });
    }
    wasOpen.current = !!win?.open;
  }, [win?.open, qc]);

  const loggedIn = win?.logged_in ?? s?.chatgpt_logged_in;
  const checkedAt = win?.checked_at ?? s?.chatgpt_checked_at;

  async function act(fn: typeof open) {
    setError("");
    try {
      await fn.mutateAsync();
    } catch (e) {
      setError(apiErrorText(e));
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {!win?.open ? (
          <button
            onClick={() => act(open)}
            disabled={open.isPending}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            {open.isPending ? "⏳ Đang mở…" : "🤖 Mở ChatGPT để đăng nhập"}
          </button>
        ) : (
          <button
            onClick={() => act(close)}
            disabled={close.isPending}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            {close.isPending ? "⏳ Đang đóng…" : "✖ Đóng cửa sổ ChatGPT"}
          </button>
        )}
        <ChatGPTLoginBadge loggedIn={loggedIn} checkedAt={checkedAt} />
      </div>
      {win?.open && win.message && (
        <p className="rounded-lg bg-emerald-500/10 p-2 text-xs text-emerald-200">{win.message}</p>
      )}
      {error && <p className="text-xs text-red-400">❌ {error}</p>}
      {!compact && (
        <ul className="list-disc space-y-0.5 pl-5 text-[11px] text-gray-500">
          <li>
            Cửa sổ dùng profile RIÊNG của tool — bạn <b>tự đăng nhập</b>; tool không nhập
            email / mật khẩu / mã OTP và không bấm ô xác minh (Cloudflare) thay bạn.
          </li>
          <li>
            Nên đăng nhập bằng <b>email + mật khẩu</b>: «Continue with Google» có thể bị Google
            chặn trong trình duyệt tự động.
          </li>
          <li>
            Tool vẽ trong <b>chat thường</b> (chat tạm thời của ChatGPT không có công cụ vẽ ảnh)
            — mỗi thumbnail để lại 1 cuộc chat trong lịch sử ChatGPT; tool không đọc các cuộc
            chat khác. Tài khoản miễn phí chỉ được ít ảnh/ngày; hết lượt → tool tự dùng API
            (nếu bật) hoặc chữ font.
          </li>
          <li>
            Tự động hoá ChatGPT web có thể trái điều khoản của OpenAI — tool chỉ vẽ ~1 ảnh mỗi
            video để hạn chế rủi ro tài khoản.
          </li>
          <li>
            Đăng nhập xong bấm «Đóng cửa sổ ChatGPT» (hoặc tự đóng cửa sổ) để giải phóng trình
            duyệt cho tác vụ video.
          </li>
        </ul>
      )}
    </div>
  );
}

// Máy vẽ thumbnail (ChatGPT web mặc định) + key RIÊNG cho model ảnh qua API
// (ShopAIKey…): cả hai vẽ ảnh lẫn chữ tiêu đề thumbnail.
export function ThumbnailAiSettings() {
  const { data: s } = useThumbAISettings();
  const update = useUpdateThumbAISettings();
  const test = useTestThumbAI();

  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelSel, setModelSel] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [verify, setVerify] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [result, setResult] = useState<ThumbAITestResult | null>(null);
  const [error, setError] = useState("");
  const [engineErr, setEngineErr] = useState("");

  useEffect(() => {
    if (!s) return;
    const known = s.models.some((m) => m.key === s.model);
    setBaseUrl(s.base_url);
    setModelSel(known ? s.model : CUSTOM);
    setCustomModel(known ? "" : s.model);
    setEnabled(s.enabled);
    setVerify(s.verify);
    setApiKey("");
    setDirty(false);
  }, [s]);

  const model = modelSel === CUSTOM ? customModel.trim() : modelSel;
  const info = s?.models.find((m) => m.key === model);
  const touch = () => { setDirty(true); setResult(null); setError(""); };

  async function save() {
    setError("");
    try {
      await update.mutateAsync({
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model: model || undefined,
        enabled,
        verify,
      });
    } catch (e) {
      setError(apiErrorText(e));
    }
  }

  async function setEngine(engine: ThumbEngine) {
    setEngineErr("");
    try {
      await update.mutateAsync({ engine });
    } catch (e) {
      setEngineErr(apiErrorText(e));
    }
  }

  async function check() {
    setResult(null);
    setError("");
    try {
      setResult(await test.mutateAsync({
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model: model || undefined,
      }));
    } catch (e) {
      setError(apiErrorText(e));
    }
  }

  return (
    <div className="space-y-3 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">
        🖼️ Thumbnail — AI vẽ ảnh + chữ tiêu đề
      </h3>
      <p className="text-xs text-gray-400">
        AI nhận <b>ảnh bìa sạch</b> (ảnh 0) và tự vẽ tiêu đề + dòng phụ lên ảnh theo công thức
        thumbnail riêng của video (bố cục, kiểu chữ, màu, font). Tool đọc lại chữ trên ảnh:
        sai chính tả → thử lại 1 lần, vẫn sai → chuyển cách kế tiếp. Ảnh đã tạo được lưu lại —
        ghép lại video không vẽ lại.
      </p>

      <div className="space-y-2 rounded-lg border border-white/5 bg-[#0f1117] p-3">
        <label className="block text-xs font-medium text-gray-400">
          Máy vẽ thumbnail khi ghép video
        </label>
        <div className="space-y-1.5">
          {(s?.engines ?? []).map((e) => (
            <label key={e.key} className="flex items-start gap-2 text-xs text-gray-300">
              <input
                type="radio"
                name="thumb-engine"
                checked={s?.engine === e.key}
                disabled={update.isPending}
                onChange={() => setEngine(e.key)}
                className="mt-0.5"
              />
              <span>
                <b className="text-gray-100">{e.label}</b>
                {e.key === "chatgpt" && <span className="text-emerald-400"> · mặc định</span>}
                <span className="block text-[11px] text-gray-500">{e.note}</span>
              </span>
            </label>
          ))}
        </div>
        {s?.engine === "api" && !(s.has_api_key && s.enabled) && (
          <p className="text-[11px] text-yellow-300">
            ⚠️ Đã chọn API nhưng chưa có key hoặc đang tắt «Tự dùng API khi ghép video» → ghép
            video sẽ vẽ chữ bằng font.
          </p>
        )}
        {engineErr && <p className="text-xs text-red-400">❌ {engineErr}</p>}
        <p className="text-[11px] text-gray-600">Chọn là lưu ngay.</p>
      </div>

      <div className="space-y-2 rounded-lg border border-emerald-500/20 bg-[#0f1117] p-3">
        <h4 className="text-sm font-semibold text-emerald-300">
          🤖 ChatGPT web (tài khoản ChatGPT của bạn)
        </h4>
        <p className="text-xs text-gray-400">
          Tool mở chatgpt.com, đính kèm ảnh bìa và nhờ ChatGPT vẽ thumbnail kèm chữ — không tốn
          phí API. Chỉ cần đăng nhập ChatGPT <b>một lần</b> trong cửa sổ của tool.
        </p>
        <ChatGPTLoginPanel />
      </div>

      <h4 className="pt-1 text-sm font-semibold text-white">
        🎨 API model ảnh (ShopAIKey…) — tuỳ chọn, tốn phí
      </h4>
      <p className="text-xs text-amber-300/80">
        ⓘ Phần này lưu bằng nút <b>💾 Lưu Thumbnail AI</b> riêng bên dưới.
      </p>

      <div className="space-y-2 rounded-lg border border-white/5 bg-[#0f1117] p-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">
            API key model ảnh{" "}
            {s?.has_api_key
              ? <span className="text-green-400">(đã lưu — để trống = giữ nguyên)</span>
              : <span className="text-yellow-400">(chưa có)</span>}
          </label>
          <div className="flex gap-2">
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); touch(); }}
              placeholder={s?.has_api_key ? "••••••••  (đã lưu)" : "Dán key ShopAIKey (sk-…)"}
              className={inputCls}
            />
            {s?.has_api_key && (
              <button
                onClick={() => update.mutate({ clear_api_key: true })}
                disabled={update.isPending}
                className="shrink-0 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-40"
              >
                Xoá key
              </button>
            )}
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">Base URL</label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => { setBaseUrl(e.target.value); touch(); }}
            placeholder="https://api.shopaikey.com"
            className={inputCls}
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">Model ảnh</label>
          <select
            value={modelSel}
            onChange={(e) => { setModelSel(e.target.value); touch(); }}
            className={inputCls}
          >
            {(s?.models ?? []).map((m) => (
              <option key={m.key} value={m.key}>
                {m.label} — ${m.price_usd}/ảnh {vndText(m.price_usd)} · nhóm {m.group}
              </option>
            ))}
            <option value={CUSTOM}>✏️ Model khác (tự gõ tên)…</option>
          </select>
          {modelSel === CUSTOM && (
            <input
              type="text"
              value={customModel}
              onChange={(e) => { setCustomModel(e.target.value); touch(); }}
              placeholder="vd. gpt-image-2.5-flare-x"
              className={`${inputCls} mt-2`}
            />
          )}
          {info && (
            <p className="mt-1 text-[11px] text-gray-500">
              {info.note} · Cần key thuộc nhóm <b>{info.group}</b> trên ShopAIKey.
            </p>
          )}
          <p className="mt-1 text-[11px] text-gray-500">
            Lưu ý: nhóm <b>Image Only</b> KHÔNG có model Gemini (Nano Banana) — key nhóm
            đó hãy chọn GPT Image 2 (all).
          </p>
        </div>

        <label className="flex items-start gap-2 text-xs text-gray-300">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => { setEnabled(e.target.checked); touch(); }}
            className="mt-0.5"
          />
          <span>
            Tự dùng API khi ghép video (tốn phí): là máy chính nếu chọn «API» ở trên, hoặc{" "}
            <b>dự phòng</b> khi ChatGPT lỗi / hết lượt. Tắt = API chỉ chạy khi bạn bấm
            «🎨 Tạo bằng API» trong tab Video.
          </span>
        </label>
        <label className="flex items-start gap-2 text-xs text-gray-300">
          <input
            type="checkbox"
            checked={verify}
            onChange={(e) => { setVerify(e.target.checked); touch(); }}
            className="mt-0.5"
          />
          <span>
            Kiểm tra chính tả chữ trên ảnh (dùng model đọc ảnh của API chính ở trên, rất rẻ) —
            áp dụng cho cả ChatGPT lẫn API.
          </span>
        </label>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            onClick={save}
            disabled={update.isPending || !dirty || (modelSel === CUSTOM && !customModel.trim())}
            className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-40"
          >
            {update.isPending ? "⏳ Đang lưu…" : "💾 Lưu Thumbnail AI"}
          </button>
          <button
            onClick={check}
            disabled={test.isPending}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            {test.isPending ? "⏳ Đang kiểm tra…" : "🔌 Kiểm tra key (miễn phí)"}
          </button>
          {!dirty && update.isSuccess && <span className="text-xs text-green-400">✓ Đã lưu</span>}
        </div>

        {result && (
          <div className={`rounded-lg p-2 text-xs ${result.ok ? "bg-green-500/10 text-green-300" : "bg-red-500/10 text-red-300"}`}>
            {result.ok ? (
              <>
                <div>
                  ✅ Key hợp lệ ({result.model_count} model, {result.latency_ms ?? "?"} ms).{" "}
                  {result.model_available === true && <>Model «{result.model}» <b>có</b> trong nhóm của key.</>}
                  {result.model_available === false && (
                    <span className="text-yellow-300">
                      ⚠️ Model «{result.model}» <b>KHÔNG</b> có trong nhóm của key — chọn model khác ở ô «Model ảnh».
                    </span>
                  )}
                </div>
                {result.image_models.length > 0 && (
                  <div className="mt-1 text-gray-400">
                    Model ảnh key dùng được: {result.image_models.join(", ")}
                  </div>
                )}
              </>
            ) : (
              <>❌ {result.error}</>
            )}
          </div>
        )}
        {error && <p className="text-xs text-red-400">❌ {error}</p>}
        <p className="text-[11px] text-gray-600">
          «Kiểm tra key» chỉ đọc danh sách model (không tạo ảnh, không tốn phí). Mỗi lần tạo
          thumbnail qua API tốn phí theo giá model (tối đa 2 lượt nếu chữ sai).
        </p>
      </div>
    </div>
  );
}
