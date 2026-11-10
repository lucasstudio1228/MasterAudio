import { useState } from "react";

const CUSTOM_LINE = "__custom__";

// Dòng nhạc (tool Cover, 2026-10-28): của kênh (Settings — thay cho «nhạc cụ» của
// tool cũ) và của project (cùng thể loại để tự chọn kênh đăng nháp).
export const MUSIC_LINES: { value: string; label: string }[] = [
  { value: "VN", label: "Nhạc Việt (VN)" },
  { value: "US-UK", label: "US-UK" },
  { value: "Chinese", label: "Chinese — nhạc Hoa" },
  { value: "Japanese", label: "Japanese — nhạc Nhật" },
  { value: "Korean", label: "Korean — nhạc Hàn" },
  { value: "Thai", label: "Thai — nhạc Thái" },
];

const norm = (s: string) => s.trim().toLowerCase();

/** Chọn dòng nhạc: danh sách có sẵn (+ dòng nhạc tự nhập của các kênh) + «Khác». */
export default function MusicLineSelect({
  value,
  onChange,
  className,
  emptyLabel = "— dòng nhạc (tuỳ chọn) —",
  title = "Dòng nhạc của kênh",
  extra = [],
}: {
  value: string;
  onChange: (v: string) => void;
  className: string;
  emptyLabel?: string;
  title?: string;
  /** Dòng nhạc tự nhập ở các kênh (Settings) — hiện thành lựa chọn có sẵn. */
  extra?: string[];
}) {
  const known = new Set(MUSIC_LINES.map((l) => norm(l.value)));
  const fromChannels = [...new Set(extra.map((v) => v.trim()))]
    .filter((v) => v && !known.has(norm(v)));
  const options = [...MUSIC_LINES, ...fromChannels.map((v) => ({ value: v, label: v }))];
  const match = options.find((l) => norm(l.value) === norm(value));
  const [custom, setCustom] = useState(Boolean(value.trim()) && !match);
  return (
    <div>
      <select className={className} value={custom ? CUSTOM_LINE : (match?.value ?? "")}
              title={title}
              onChange={(e) => {
                if (e.target.value === CUSTOM_LINE) { setCustom(true); onChange(""); }
                else { setCustom(false); onChange(e.target.value); }
              }}>
        <option value="">{emptyLabel}</option>
        {MUSIC_LINES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        {fromChannels.length > 0 && (
          <optgroup label="Từ kênh đã cấu hình">
            {fromChannels.map((v) => <option key={v} value={v}>{v}</option>)}
          </optgroup>
        )}
        <option value={CUSTOM_LINE}>Khác (tự nhập)…</option>
      </select>
      {custom && (
        <input className={`${className} mt-1`} value={value} autoFocus
               placeholder="vd: Indonesia, Latin…"
               onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}
