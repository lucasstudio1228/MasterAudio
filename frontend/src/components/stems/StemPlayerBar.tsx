interface Props {
  trackName:   string;
  isPlaying:   boolean;
  isLoading:   boolean;
  currentTime: number;
  duration:    number;
  error:       string | null;
  onPlay:      () => void;
  onPause:     () => void;
  onResume:    () => void;
  onStop:      () => void;
  onSeek:      (time: number) => void;
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function StemPlayerBar({
  trackName, isPlaying, isLoading, currentTime, duration,
  error, onPlay, onPause, onResume, onStop, onSeek,
}: Props) {
  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50
                    bg-[#1a1d2e] border-t border-white/10
                    px-4 py-2.5 flex items-center gap-4
                    shadow-2xl shadow-black/50">

      {/* Play/Pause/Stop */}
      <div className="flex items-center gap-2 shrink-0">
        {isLoading ? (
          <div className="w-9 h-9 flex items-center justify-center
                          text-violet-400 animate-pulse text-sm">
            ⏳
          </div>
        ) : isPlaying ? (
          <button onClick={onPause}
                  className="w-9 h-9 flex items-center justify-center
                             rounded-full bg-violet-600 hover:bg-violet-500
                             text-white text-sm">
            ⏸
          </button>
        ) : (
          <button onClick={currentTime > 0 ? onResume : onPlay}
                  className="w-9 h-9 flex items-center justify-center
                             rounded-full bg-violet-600 hover:bg-violet-500
                             text-white text-sm">
            ▶
          </button>
        )}
        <button onClick={onStop}
                className="w-7 h-7 flex items-center justify-center
                           rounded-full border border-white/10
                           hover:border-white/30 text-gray-400
                           hover:text-white text-xs">
          ⏹
        </button>
      </div>

      {/* Track name */}
      <span className="text-sm text-gray-300 truncate max-w-[180px]
                        shrink-0" title={trackName}>
        🎵 {trackName}
      </span>

      {/* Progress bar */}
      <div className="flex-1 flex items-center gap-2">
        <span className="text-xs text-gray-500 tabular-nums w-10 text-right">
          {formatTime(currentTime)}
        </span>
        <div className="flex-1 relative h-1.5 bg-white/10 rounded-full
                        cursor-pointer group"
             onClick={(e) => {
               const rect = e.currentTarget.getBoundingClientRect();
               const pct  = (e.clientX - rect.left) / rect.width;
               onSeek(pct * duration);
             }}>
          <div
            className="absolute left-0 top-0 h-full bg-violet-500
                       rounded-full transition-[width] duration-100"
            style={{ width: `${progress}%` }}
          />
          {/* Hover dot */}
          <div
            className="absolute top-1/2 -translate-y-1/2 w-3 h-3
                       bg-violet-400 rounded-full opacity-0
                       group-hover:opacity-100 transition-opacity"
            style={{ left: `${progress}%`, marginLeft: "-6px" }}
          />
        </div>
        <span className="text-xs text-gray-500 tabular-nums w-10">
          {formatTime(duration)}
        </span>
      </div>

      {/* Error */}
      {error && (
        <span className="text-xs text-red-400 truncate max-w-[200px]">
          {error}
        </span>
      )}
    </div>
  );
}
