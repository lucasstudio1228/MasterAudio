import { useEffect, useState } from 'react';
import type { MixCreate } from '../../types';

interface Props {
  trackCount: number;
  disabled?: boolean;
  onStart: (settings: MixCreate) => void;
  // Crossfade đã lưu theo project — chuỗi tự động Suno→Mix→Video cũng dùng số
  // này. Mixset: mỗi bài phát 1 lần nên không còn ô Duration (giữ số đã lưu
  // để API cũ vẫn nhận đủ trường).
  savedDuration?: number;
  savedCrossfade?: number;
  onSave?: (durationMinutes: number, crossfadeSeconds: number) => void;
}

// 0 = Auto (lấy sample rate gốc cao nhất của library, không upsample giả).
const SAMPLE_RATES = [0, 48000, 88200, 96000, 176400, 192000];
const SR_LABEL: Record<number, string> = {
  0: 'Auto (gốc cao nhất)',
  48000: '48000 Hz',
  88200: '88200 Hz',
  96000: '96000 Hz',
  176400: '176400 Hz',
  192000: '192000 Hz',
};
const MIN_TRACKS = 1;

export default function MixSettingsPanel({
  trackCount, disabled, onStart, savedDuration, savedCrossfade, onSave,
}: Props) {
  const duration = savedDuration && savedDuration >= 1 ? savedDuration : 120;
  const [crossfade, setCrossfade] = useState(savedCrossfade ?? 5);

  // Đồng bộ khi dữ liệu project tải xong / đổi project.
  useEffect(() => { if (savedCrossfade != null) setCrossfade(savedCrossfade); }, [savedCrossfade]);

  // Lưu khi rời ô nhập (chỉ khi hợp lệ và thực sự đổi).
  const persist = () => {
    if (!onSave) return;
    if (!(crossfade >= 1 && crossfade <= 60)) return;
    if (crossfade === savedCrossfade) return;
    onSave(duration, crossfade);
  };
  const [sampleRate, setSampleRate] = useState(0);        // mặc định Auto
  const [bitDepth, setBitDepth] = useState<24 | 32>(32);  // mặc định 32-bit float

  const enoughTracks = trackCount >= MIN_TRACKS;

  const inputCls =
    'w-24 rounded-lg border border-white/10 bg-background px-2 py-1 text-sm text-white outline-none focus:border-accent';

  return (
    <div className="space-y-3">
      <Row label="Crossfade">
        <input
          type="number"
          min={1}
          max={60}
          className={inputCls}
          value={crossfade}
          onChange={(e) => setCrossfade(Number(e.target.value))}
          onBlur={persist}
        />
        <span className="text-xs text-gray-400">sec</span>
      </Row>

      <p className="text-xs text-gray-500">
        🎶 Mixset: mỗi bài phát <b>1 lần</b> theo thứ tự ở tab ③ Lời bài hát — độ dài
        video = tổng độ dài các bài (trừ phần crossfade nối bài).
      </p>

      <Row label="Sample Rate">
        <select
          className={inputCls + ' w-40'}
          value={sampleRate}
          onChange={(e) => setSampleRate(Number(e.target.value))}
        >
          {SAMPLE_RATES.map((sr) => (
            <option key={sr} value={sr}>
              {SR_LABEL[sr]}
            </option>
          ))}
        </select>
      </Row>

      <Row label="Bit Depth">
        <div className="flex gap-3 text-sm text-gray-200">
          {[24, 32].map((bd) => (
            <label key={bd} className="flex items-center gap-1">
              <input
                type="radio"
                checked={bitDepth === bd}
                onChange={() => setBitDepth(bd as 24 | 32)}
              />
              {bd === 32 ? '32 (float)' : '24'}
            </label>
          ))}
        </div>
      </Row>

      <p className="text-xs text-gray-600">
        🎧 Chất lượng tối đa: xuất WAV 32-bit float (không nén). Auto giữ đúng
        sample rate gốc cao nhất, không upsample giả.
      </p>

      {!enoughTracks && (
        <div className="rounded-lg bg-yellow-500/10 px-3 py-2 text-xs text-yellow-300">
          ⚠️ Chưa có bài nào để mix.
        </div>
      )}

      <button
        disabled={disabled || !enoughTracks}
        onClick={() =>
          onStart({
            duration_minutes: duration,
            crossfade_seconds: crossfade,
            sample_rate: sampleRate,
            bit_depth: bitDepth,
          })
        }
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
      >
        ▶ Start Mix
      </button>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-gray-300">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}
