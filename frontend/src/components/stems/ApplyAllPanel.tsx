import { useState } from "react";
import { STEM_META, type StemKey } from "./StemWaveform";
import { useApplyAllVolumes } from "../../api/stems";
import type { StemVolumes, StemDenoise } from "../../types";

const STEM_ORDER: StemKey[] = ["other", "vocals", "bass", "drums"];
const DEFAULT_VOLS: StemVolumes = { other: 1, vocals: 1, bass: 1, drums: 1 };
const DEFAULT_DEN:  StemDenoise = { other: 0, vocals: 0, bass: 0, drums: 0 };

interface Props {
  projectId: number;
}

export function ApplyAllPanel({ projectId }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [vols, setVols]         = useState<StemVolumes>({ ...DEFAULT_VOLS });
  const [dens, setDens]         = useState<StemDenoise>({ ...DEFAULT_DEN });
  const [showConfirm, setShowConfirm] = useState(false);
  const applyAll = useApplyAllVolumes(projectId);

  function handleChange(key: StemKey, val: number) {
    setVols(v => ({ ...v, [key]: val }));
  }
  function handleDenChange(key: StemKey, val: number) {
    setDens(d => ({ ...d, [key]: val }));
  }

  function handleApply() {
    setShowConfirm(true);
  }

  async function handleConfirm() {
    await applyAll.mutateAsync({ volumes: vols, denoise: dens });
    setShowConfirm(false);
  }

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="w-full py-2 text-xs text-gray-500
                   hover:text-violet-300 border border-dashed
                   border-white/10 hover:border-violet-500/30
                   rounded-lg transition-colors"
      >
        🎛 Chỉnh thông số chung — Apply for All Tracks
      </button>
    );
  }

  return (
    <div className="border border-violet-500/30 rounded-lg p-4
                    bg-violet-950/20">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-violet-300">
          🎛 Thông số chung cho tất cả tracks
        </h4>
        <button onClick={() => setExpanded(false)}
                className="text-xs text-gray-500 hover:text-white">
          Đóng
        </button>
      </div>

      <p className="text-xs text-gray-500 mb-4">
        Set volume + độ mạnh khử noise cho từng stem, rồi "Apply for All" để
        áp cho tất cả tracks đã phân tích.
      </p>

      {/* Volume sliders */}
      <div className="space-y-3 mb-4">
        <p className="text-[11px] uppercase tracking-wide text-gray-500">Volume</p>
        {STEM_ORDER.map(key => {
          const { label, color } = STEM_META[key];
          const pct = Math.round(vols[key] * 100);
          return (
            <div key={key} className="flex items-center gap-3">
              <span className="text-xs font-semibold w-20 shrink-0"
                    style={{ color }}>
                {label}
              </span>
              <input
                type="range" min={0} max={200} step={1}
                value={pct}
                onChange={e =>
                  handleChange(key, Number(e.target.value) / 100)
                }
                className="flex-1 h-1.5 accent-violet-500 cursor-pointer"
              />
              <span className={`text-xs tabular-nums w-12 text-right ${
                pct === 0 ? "text-red-400" : "text-gray-400"
              }`}>
                {pct === 0 ? "MUTE" : `${pct}%`}
              </span>
            </div>
          );
        })}
      </div>

      {/* Denoise sliders */}
      <div className="space-y-3 mb-4">
        <p className="text-[11px] uppercase tracking-wide text-gray-500">
          🧹 Khử noise (0 = Auto theo volume)
        </p>
        {STEM_ORDER.map(key => {
          const { label, color } = STEM_META[key];
          const dpct = Math.round(dens[key] * 100);
          return (
            <div key={key} className="flex items-center gap-3">
              <span className="text-xs font-semibold w-20 shrink-0"
                    style={{ color }}>
                {label}
              </span>
              <input
                type="range" min={0} max={100} step={1}
                value={dpct}
                onChange={e =>
                  handleDenChange(key, Number(e.target.value) / 100)
                }
                className="flex-1 h-1.5 accent-amber-500 cursor-pointer"
              />
              <span className={`text-xs tabular-nums w-12 text-right ${
                dpct === 0 ? "text-gray-600" : "text-amber-400/90"
              }`}>
                {dpct === 0 ? "Auto" : `+${dpct}%`}
              </span>
            </div>
          );
        })}
      </div>

      {/* Buttons */}
      <div className="flex gap-2">
        <button
          onClick={() => { setVols({ ...DEFAULT_VOLS }); setDens({ ...DEFAULT_DEN }); }}
          className="px-3 py-1.5 text-xs rounded-lg
                     border border-white/10 hover:border-white/20
                     text-gray-400 hover:text-white"
        >
          Reset
        </button>
        <button
          onClick={handleApply}
          disabled={applyAll.isPending}
          className="flex-1 py-1.5 text-xs rounded-lg font-medium
                     bg-violet-600 hover:bg-violet-500
                     disabled:opacity-40 text-white"
        >
          {applyAll.isPending
            ? "Applying..."
            : "Apply for All Tracks"}
        </button>
      </div>

      {/* Confirm dialog */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center
                        justify-center z-50">
          <div className="bg-[#1a1d2e] rounded-xl p-5 w-full max-w-sm
                          shadow-2xl">
            <h3 className="text-sm font-semibold text-white mb-3">
              Áp dụng cho tất cả tracks?
            </h3>
            <p className="text-xs text-gray-400 mb-2">
              Volume + khử noise sẽ được apply cho tất cả tracks đã phân tích
              trong project. Không thể undo.
            </p>
            <div className="text-xs text-gray-500 mb-4 space-y-0.5">
              {STEM_ORDER.map(key => {
                const pct  = Math.round(vols[key] * 100);
                const dpct = Math.round(dens[key] * 100);
                return (
                  <div key={key} className="flex justify-between">
                    <span style={{color: STEM_META[key].color}}>
                      {STEM_META[key].label}
                    </span>
                    <span>
                      {pct === 0 ? "MUTE" : `${pct}%`}
                      {" · 🧹 "}
                      {dpct === 0 ? "Auto" : `+${dpct}%`}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 py-2 text-xs text-gray-400
                           hover:text-white border border-white/10
                           rounded-lg"
              >
                Hủy
              </button>
              <button
                onClick={handleConfirm}
                disabled={applyAll.isPending}
                className="flex-1 py-2 text-xs bg-violet-600
                           hover:bg-violet-500 disabled:opacity-40
                           rounded-lg text-white font-medium"
              >
                {applyAll.isPending ? "..." : "Xác nhận Apply"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Success message */}
      {applyAll.isSuccess && !showConfirm && (
        <p className="text-xs text-green-400 mt-2 text-center">
          ✅ Đã apply cho {(applyAll.data as any)?.data?.updated ?? ""} tracks
        </p>
      )}
    </div>
  );
}
