import { useRef, useState, useCallback, useEffect } from "react";
import { getStemAudioUrl } from "../api/stems";

const STEM_NAMES = ["other", "vocals", "bass", "drums"] as const;
type StemName = (typeof STEM_NAMES)[number];

interface StemPlayerState {
  isPlaying:    boolean;
  isLoading:    boolean;
  currentTime:  number;    // seconds
  duration:     number;    // seconds
  activeTrackId: number | null;
  error:        string | null;
}

interface StemPlayerControls {
  play:   (trackId: number) => Promise<void>;
  pause:  () => void;
  resume: () => void;
  stop:   () => void;
  seek:   (time: number) => void;
  setVolume: (stem: StemName, value: number) => void;
}

export function useStemPlayer(): [StemPlayerState, StemPlayerControls] {
  const [state, setState] = useState<StemPlayerState>({
    isPlaying: false,
    isLoading: false,
    currentTime: 0,
    duration: 0,
    activeTrackId: null,
    error: null,
  });

  // Refs để giữ Web Audio objects (không trigger re-render)
  const ctxRef     = useRef<AudioContext | null>(null);
  const sourcesRef = useRef<Map<string, AudioBufferSourceNode>>(new Map());
  const gainsRef   = useRef<Map<string, GainNode>>(new Map());
  const buffersRef = useRef<Map<string, AudioBuffer>>(new Map());
  const startTimeRef  = useRef<number>(0);   // ctx.currentTime khi bắt đầu play
  const offsetRef     = useRef<number>(0);   // offset nếu đã seek/pause
  const rafRef        = useRef<number>(0);   // requestAnimationFrame ID
  const activeTrackRef = useRef<number | null>(null);

  // ── Cleanup khi unmount ──
  useEffect(() => {
    return () => {
      _cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function _cleanup() {
    cancelAnimationFrame(rafRef.current);
    sourcesRef.current.forEach(s => { try { s.stop(); } catch {} });
    sourcesRef.current.clear();
    gainsRef.current.clear();
    buffersRef.current.clear();
    if (ctxRef.current && ctxRef.current.state !== "closed") {
      ctxRef.current.close();
    }
    ctxRef.current = null;
    activeTrackRef.current = null;
  }

  // ── Time tracking ──
  function _startTimeTracking() {
    function tick() {
      if (!ctxRef.current) return;
      const elapsed = ctxRef.current.currentTime - startTimeRef.current + offsetRef.current;
      setState(s => ({ ...s, currentTime: elapsed }));
      rafRef.current = requestAnimationFrame(tick);
    }
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
  }

  // ── Play ──
  const play = useCallback(async (trackId: number) => {
    // Stop nếu đang play track khác
    if (activeTrackRef.current !== null && activeTrackRef.current !== trackId) {
      _cleanup();
    }

    setState(s => ({
      ...s, isLoading: true, error: null, activeTrackId: trackId
    }));
    activeTrackRef.current = trackId;

    try {
      // Tạo AudioContext mới
      const ctx = new AudioContext();
      ctxRef.current = ctx;

      // Fetch + decode 4 stems song song
      const fetchPromises = STEM_NAMES.map(async (name) => {
        const url = getStemAudioUrl(trackId, name);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to fetch ${name}: ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
        return { name, buffer: audioBuffer };
      });

      const results = await Promise.all(fetchPromises);

      // Kiểm tra nếu user đã stop/switch trong lúc loading
      if (activeTrackRef.current !== trackId) return;

      // Setup audio chains: source → gain → destination
      let maxDuration = 0;
      results.forEach(({ name, buffer }) => {
        buffersRef.current.set(name, buffer);
        maxDuration = Math.max(maxDuration, buffer.duration);

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.loop = false;

        const gain = ctx.createGain();
        gain.gain.value = 1.0;   // Default, sẽ được set bởi component

        source.connect(gain);
        gain.connect(ctx.destination);

        sourcesRef.current.set(name, source);
        gainsRef.current.set(name, gain);
      });

      // Start playback
      offsetRef.current = 0;
      startTimeRef.current = ctx.currentTime;
      sourcesRef.current.forEach(s => s.start(0));

      // Auto-stop khi hết bài
      const longestSource = sourcesRef.current.get(
        results.reduce((a, b) =>
          a.buffer.duration > b.buffer.duration ? a : b
        ).name
      );
      if (longestSource) {
        longestSource.onended = () => {
          setState(s => ({
            ...s, isPlaying: false, currentTime: maxDuration
          }));
          cancelAnimationFrame(rafRef.current);
        };
      }

      setState(s => ({
        ...s,
        isPlaying: true,
        isLoading: false,
        duration: maxDuration,
        currentTime: 0,
      }));
      _startTimeTracking();

    } catch (err) {
      setState(s => ({
        ...s,
        isLoading: false,
        isPlaying: false,
        error: err instanceof Error ? err.message : "Failed to load audio",
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Pause ──
  const pause = useCallback(() => {
    if (ctxRef.current && ctxRef.current.state === "running") {
      offsetRef.current += ctxRef.current.currentTime - startTimeRef.current;
      ctxRef.current.suspend();
      cancelAnimationFrame(rafRef.current);
      setState(s => ({ ...s, isPlaying: false }));
    }
  }, []);

  // ── Resume ──
  const resume = useCallback(() => {
    if (ctxRef.current && ctxRef.current.state === "suspended") {
      ctxRef.current.resume();
      startTimeRef.current = ctxRef.current.currentTime;
      setState(s => ({ ...s, isPlaying: true }));
      _startTimeTracking();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Stop ──
  const stop = useCallback(() => {
    _cleanup();
    setState({
      isPlaying: false,
      isLoading: false,
      currentTime: 0,
      duration: 0,
      activeTrackId: null,
      error: null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Seek ──
  const seek = useCallback((time: number) => {
    if (!ctxRef.current || !buffersRef.current.size) return;
    const ctx = ctxRef.current;
    const wasPlaying = ctx.state === "running";

    // Stop existing sources — null onended TRƯỚC khi stop.
    // stop() sẽ trigger onended của source cũ; nếu không clear thì
    // handler chạy và ghi đè state (isPlaying=false, currentTime=maxDuration)
    // → UI nhảy về cuối bài + hiện nút play sai. Đây là race đã fix.
    sourcesRef.current.forEach(s => {
      s.onended = null;
      try { s.stop(); } catch {}
    });
    sourcesRef.current.clear();

    // Xác định longest buffer để gắn LẠI onended (auto-stop khi hết bài)
    let longestName = "";
    let maxDuration = 0;
    buffersRef.current.forEach((buffer, name) => {
      if (buffer.duration > maxDuration) {
        maxDuration  = buffer.duration;
        longestName  = name;
      }
    });

    // Recreate sources from buffers, starting at new offset
    buffersRef.current.forEach((buffer, name) => {
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.loop = false;

      const gain = gainsRef.current.get(name);
      if (gain) {
        source.connect(gain);
      }

      // Gắn onended cho longest source mới — auto-stop đúng cách
      if (name === longestName) {
        source.onended = () => {
          setState(s => ({ ...s, isPlaying: false, currentTime: maxDuration }));
          cancelAnimationFrame(rafRef.current);
        };
      }

      sourcesRef.current.set(name, source);
      source.start(0, time);   // start from offset
    });

    offsetRef.current = time;
    startTimeRef.current = ctx.currentTime;
    // Restore isPlaying = wasPlaying (seek không được đổi trạng thái play/pause)
    setState(s => ({ ...s, currentTime: time, isPlaying: wasPlaying }));

    if (!wasPlaying) {
      ctx.suspend();
    }
  }, []);

  // ── Set Volume (INSTANT — Web Audio GainNode) ──
  const setVolume = useCallback((stem: StemName, value: number) => {
    const gain   = gainsRef.current.get(stem);
    const source = sourcesRef.current.get(stem);
    if (!gain || !source) return;

    if (value < 0.02) {
      // MUTE hoàn toàn — disconnect source khỏi gain để không còn
      // residual signal + tiết kiệm CPU. connect/disconnect giữa cùng
      // 2 node là idempotent nên gọi lại an toàn.
      try { source.disconnect(gain); } catch {}
      gain.gain.setTargetAtTime(0, gain.context.currentTime, 0.01);
    } else {
      // Reconnect nếu trước đó đã disconnect (no-op nếu đã nối)
      try { source.connect(gain); } catch {}
      // smoothing nhẹ để tránh click khi thay đổi đột ngột
      gain.gain.setTargetAtTime(value, gain.context.currentTime, 0.02);
    }
  }, []);

  return [state, { play, pause, resume, stop, seek, setVolume }];
}
