import { useState } from "react";
import { StemWaveform, type StemKey } from "./StemWaveform";
import {
  useStemInfo, useAnalyzeStem,
  useUpdateStemVolumes, useUpdateStemDenoise, useDeleteStems
} from "../../api/stems";
import type { StemVolumes, StemDenoise } from "../../types";

// Thứ tự hiển thị: như app trong ảnh
const STEM_ORDER = ["other", "vocals", "bass", "drums"] as const;

const STATUS_LABEL: Record<string, string> = {
  not_started: "Chưa phân tích",
  pending:     "Đang chờ...",
  running:     "Đang tách...",
  completed:   "Sẵn sàng",
  failed:      "Lỗi",
};
const STATUS_COLOR: Record<string, string> = {
  not_started: "text-gray-500",
  pending:     "text-yellow-400",
  running:     "text-blue-400",
  completed:   "text-green-400",
  failed:      "text-red-400",
};

const DEFAULT_DEN: StemDenoise = { other: 0, vocals: 0, bass: 0, drums: 0 };

interface Props {
  trackId:   number;
  trackName: string;
  // Player props — từ useStemPlayer hook ở parent
  isActiveTrack: boolean;   // track này đang được preview
  isPlaying:     boolean;
  onPlayTrack:   (trackId: number) => void;
  onSetVolume:   (stem: StemKey, value: number) => void;
}

export function StemPanel({
  trackId, trackName,
  isActiveTrack, isPlaying, onPlayTrack, onSetVolume,
}: Props) {
  const { data: stem }   = useStemInfo(trackId);
  const analyze          = useAnalyzeStem(trackId);
  const updateVolumes    = useUpdateStemVolumes(trackId);
  const updateDenoise    = useUpdateStemDenoise(trackId);
  const deleteStems      = useDeleteStems(trackId);
  const [expanded, setExpanded]   = useState(false);
  const [localVols, setLocalVols] = useState<StemVolumes | null>(null);
  const [localDen, setLocalDen]   = useState<StemDenoise | null>(null);

  const status = stem?.status ?? "not_started";
  const vols   = localVols ?? stem?.volumes ?? {
    other: 1, vocals: 1, bass: 1, drums: 1
  };
  const dens   = localDen ?? stem?.denoise ?? { ...DEFAULT_DEN };

  function handleChange(key: keyof StemVolumes, val: number) {
    setLocalVols(v => ({ ...(v ?? vols), [key]: val }));
    // Cập nhật real-time nếu đang play track này
    if (isActiveTrack) {
      onSetVolume(key, val);
    }
  }
  function handleCommit(key: keyof StemVolumes, val: number) {
    updateVolumes.mutate({ [key]: val });
  }
  // Denoise không có preview real-time (spectral xử lý lúc render) — chỉ lưu.
  function handleDenChange(key: keyof StemDenoise, val: number) {
    setLocalDen(d => ({ ...(d ?? dens), [key]: val }));
  }
  function handleDenCommit(key: keyof StemDenoise, val: number) {
    updateDenoise.mutate({ [key]: val });
  }
  function resetVolumes() {
    const defVols: StemVolumes = { other: 1, vocals: 1, bass: 1, drums: 1 };
    const defDen:  StemDenoise = { ...DEFAULT_DEN };
    setLocalVols(defVols);
    setLocalDen(defDen);
    updateVolumes.mutate(defVols);
    updateDenoise.mutate(defDen);
  }

  return (
    <div className="border border-white/5 rounded-lg overflow-hidden
                    hover:border-white/10 transition-colors">

      {/* Header */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(e => !e)}
        onKeyDown={e => {
          if (e.key === "Enter" || e.key === " ") setExpanded(x => !x);
        }}
        className="w-full flex items-center justify-between px-3 py-2.5
                   text-left hover:bg-white/5 transition-colors cursor-pointer
                   outline-none focus-visible:ring-1 focus-visible:ring-violet-500"
      >
        <div className="flex items-center gap-2 min-w-0">
          {status === "completed" && (
            <button
              onClick={(e) => {
                e.stopPropagation();   // Không toggle expand
                onPlayTrack(trackId);
              }}
              className={`w-7 h-7 flex items-center justify-center rounded-full
                          text-sm transition-colors shrink-0 ${
                            isActiveTrack && isPlaying
                              ? "bg-violet-500 text-white shadow-lg shadow-violet-500/40"
                              : "bg-violet-600/80 text-white hover:bg-violet-500"
                          }`}
              title={isActiveTrack && isPlaying ? "Đang phát" : "Nghe thử"}
            >
              {isActiveTrack && isPlaying ? "🔊" : "▶"}
            </button>
          )}
          <span className="text-sm text-gray-300 truncate max-w-[170px]"
                title={trackName}>
            {trackName}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {status === "running" && (
            <span className="inline-block w-1.5 h-1.5 rounded-full
                             bg-blue-400 animate-pulse" />
          )}
          <span className={`text-xs ${STATUS_COLOR[status]}`}>
            {STATUS_LABEL[status]}
          </span>
          <span className="text-gray-600 text-xs">
            {expanded ? "▲" : "▼"}
          </span>
        </div>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-white/5
                        bg-black/20">

          {/* Action buttons */}
          <div className="flex gap-2 mb-3 mt-1">
            {status !== "completed" ? (
              <button
                onClick={() => analyze.mutate()}
                disabled={
                  status === "running" || status === "pending"
                  || analyze.isPending
                }
                className="flex-1 py-1.5 text-xs rounded-lg font-medium
                           bg-violet-700 hover:bg-violet-600
                           disabled:opacity-40 text-white"
              >
                {(status === "running" || status === "pending")
                  ? "⏳ Đang xử lý..."
                  : status === "failed"
                  ? "🔄 Thử lại"
                  : "🔬 Phân tích Stems"}
              </button>
            ) : (
              <>
                <button
                  onClick={resetVolumes}
                  className="px-3 py-1.5 text-xs rounded-lg
                             border border-white/10 hover:border-white/20
                             text-gray-400 hover:text-white"
                >
                  Reset
                </button>
                <button
                  onClick={() => deleteStems.mutate()}
                  disabled={deleteStems.isPending}
                  className="px-3 py-1.5 text-xs rounded-lg
                             border border-red-500/30 hover:border-red-500/60
                             text-red-400 hover:text-red-300
                             disabled:opacity-40"
                >
                  Xóa stems
                </button>
              </>
            )}
          </div>

          {/* Error message */}
          {status === "failed" && stem?.error_message && (
            <p className="text-xs text-red-400 bg-red-950/30 rounded
                          p-2 mb-3 break-all">
              {stem.error_message.slice(0, 200)}
            </p>
          )}

          {/* Loading hint */}
          {(status === "pending" || status === "running") && (
            <p className="text-xs text-gray-500 text-center py-3">
              ⚡ GPU đang chạy Demucs htdemucs_ft...<br />
              <span className="text-gray-600">
                Mỗi track ~5–10 phút (chất lượng tối đa) trên RTX 3060
              </span>
            </p>
          )}

          {/* Stem volume + denoise controls */}
          {status === "completed" && stem?.waveform_data && (
            <div className="space-y-4">
              {STEM_ORDER.map(key => (
                <StemWaveform
                  key={key}
                  stemKey={key}
                  data={stem.waveform_data![key] ?? []}
                  volume={vols[key]}
                  onVolumeChange={v  => handleChange(key, v)}
                  onVolumeCommit={v  => handleCommit(key, v)}
                  denoise={dens[key]}
                  onDenoiseChange={v => handleDenChange(key, v)}
                  onDenoiseCommit={v => handleDenCommit(key, v)}
                />
              ))}

              <p className="text-[10px] text-gray-600 leading-relaxed">
                🧹 <b>Khử noise</b>: <b>Auto</b> = tự khử theo mức giảm volume.
                Kéo lên để ép khử mạnh hơn (kể cả khi để nguyên). Chỉ ảnh hưởng
                file render cuối, không nghe được ở preview.
              </p>

              {/* LUFS info */}
              {stem.lufs_gain_applied != null && (
                <p className="text-xs text-gray-600 text-right">
                  LUFS gain: {
                    (20 * Math.log10(stem.lufs_gain_applied)).toFixed(1)
                  } dB applied
                </p>
              )}
            </div>
          )}

        </div>
      )}
    </div>
  );
}
