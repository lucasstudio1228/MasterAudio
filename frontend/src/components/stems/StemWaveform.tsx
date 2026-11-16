import { useEffect, useRef } from "react";

// Màu và nhãn tiếng Việt — đúng theo spec
export const STEM_META = {
  other:  { label: "Âm nhạc",   color: "#4ade80" },
  vocals: { label: "Giọng hát", color: "#818cf8" },
  bass:   { label: "Âm trầm",  color: "#facc15" },
  drums:  { label: "Trống",     color: "#f87171" },
} as const;

export type StemKey = keyof typeof STEM_META;

interface Props {
  stemKey:         StemKey;
  data:            number[];    // 200 RMS points 0–1
  volume:          number;      // 0.0–2.0
  onVolumeChange:  (v: number) => void;
  onVolumeCommit:  (v: number) => void;  // gọi API khi thả chuột
  denoise?:        number;      // 0.0–1.0 (độ mạnh khử noise thủ công)
  onDenoiseChange?: (v: number) => void;
  onDenoiseCommit?: (v: number) => void;
  disabled?:       boolean;
}

export function StemWaveform({
  stemKey, data, volume,
  onVolumeChange, onVolumeCommit,
  denoise = 0, onDenoiseChange, onDenoiseCommit,
  disabled = false
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { label, color } = STEM_META[stemKey];

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data.length) return;
    const ctx = canvas.getContext("2d")!;
    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);

    const barW = width / data.length;

    // Vẽ waveform (dimmed nếu mute)
    const alpha = volume < 0.02 ? "22" : "99";
    ctx.fillStyle = color + alpha;
    data.forEach((v, i) => {
      const h = Math.max(1, v * height * 0.9);
      ctx.fillRect(i * barW, (height - h) / 2, Math.max(barW - 0.5, 0.5), h);
    });

    // Volume level line (vertical bar ở vị trí volume/2 * width)
    if (volume > 0) {
      const xLine = Math.min((volume / 2) * width, width - 1);
      ctx.strokeStyle = color + "cc";
      ctx.lineWidth   = 1.5;
      ctx.beginPath();
      ctx.moveTo(xLine, 0);
      ctx.lineTo(xLine, height);
      ctx.stroke();
    }
  }, [data, color, volume]);

  const pct    = Math.round(volume * 100);
  const denPct = Math.round(denoise * 100);
  const showDenoise = onDenoiseChange !== undefined;

  return (
    <div className={`flex flex-col gap-1.5 ${disabled ? "opacity-40" : ""}`}>
      {/* Label + % */}
      <div className="flex justify-between items-center">
        <span className="text-xs font-semibold" style={{ color }}>
          {label}
        </span>
        <span className={`text-xs tabular-nums ${
          pct === 0    ? "text-red-400" :
          pct > 150    ? "text-yellow-300" : "text-gray-400"
        }`}>
          {pct === 0 ? "MUTE" : `${pct}%`}
        </span>
      </div>

      {/* Waveform canvas */}
      <canvas
        ref={canvasRef}
        width={300}
        height={40}
        className="rounded w-full"
        style={{ background: "rgba(255,255,255,0.03)" }}
      />

      {/* Volume slider */}
      <input
        type="range"
        min={0} max={200} step={1}
        value={pct}
        disabled={disabled}
        onChange={e => onVolumeChange(Number(e.target.value) / 100)}
        onMouseUp={e =>
          onVolumeCommit(Number((e.target as HTMLInputElement).value) / 100)
        }
        onTouchEnd={e =>
          onVolumeCommit(
            Number((e.target as HTMLInputElement).value) / 100
          )
        }
        className="w-full h-1.5 rounded cursor-pointer accent-violet-500
                   disabled:cursor-not-allowed"
      />

      {/* Denoise slider (độ mạnh khử noise thủ công) */}
      {showDenoise && (
        <>
          <div className="flex justify-between items-center mt-0.5">
            <span className="text-[10px] uppercase tracking-wide text-gray-500">
              🧹 Khử noise
            </span>
            <span className={`text-[10px] tabular-nums ${
              denPct === 0 ? "text-gray-600" :
              denPct >= 70 ? "text-amber-300" : "text-amber-400/80"
            }`}>
              {denPct === 0 ? "Auto" : `+${denPct}%`}
            </span>
          </div>
          <input
            type="range"
            min={0} max={100} step={1}
            value={denPct}
            disabled={disabled}
            onChange={e => onDenoiseChange?.(Number(e.target.value) / 100)}
            onMouseUp={e =>
              onDenoiseCommit?.(Number((e.target as HTMLInputElement).value) / 100)
            }
            onTouchEnd={e =>
              onDenoiseCommit?.(Number((e.target as HTMLInputElement).value) / 100)
            }
            className="w-full h-1 rounded cursor-pointer accent-amber-500
                       disabled:cursor-not-allowed"
          />
        </>
      )}
    </div>
  );
}
