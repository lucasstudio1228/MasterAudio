import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE } from "./client";
import type { VideoStatus } from "../types";

const base = (pid: number) => `/api/projects/${pid}/video`;

export function useVideoStatus(projectId: number) {
  return useQuery<VideoStatus>({
    queryKey: ["video", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/status`)).data,
    // Poll khi có job đang chạy để cập nhật % + message
    refetchInterval: (q) => {
      const s = q.state.data?.job?.status;
      return (s === "pending" || s === "running" || s === "paused") ? 1500 : false;
    },
  });
}

export type VideoStyleOption = {
  key: string;
  icon: string;
  label: string;
  desc: string;
};

export function useVideoStyles(projectId: number) {
  return useQuery<{ default: string; styles: VideoStyleOption[] }>({
    queryKey: ["video-styles"],
    queryFn: async () => (await api.get(`${base(projectId)}/styles`)).data,
    staleTime: Infinity,        // danh sách phong cách gần như tĩnh
  });
}

export type ThumbnailFontOption = { key: string; label: string; note: string };

// Công thức thumbnail riêng của project (bố cục/kiểu chữ/màu theo phong cách kênh).
export type ThumbnailRecipeInfo = {
  look: string;
  look_label: string;
  look_icon: string;
  look_styles: string[];
  layout: string;
  treatment: string;
  palette: string;
  font: string;
  font_auto: boolean;
  shot: string;
  light: string;
  transition: string;
  legacy: boolean;
  subtitle: string;
  // Chữ + style thumbnail do ChatGPT/model ảnh tự thiết kế (false = đã khoá font tay).
  ai_text?: boolean;
  ai_lettering?: string;
  ai_lettering_label?: string;
  ai_design?: string;
  ai_design_label?: string;
};

export type ThumbnailFontsData = {
  selected: string;
  default: string;
  auto: boolean;            // true = font tự chọn theo phong cách kênh
  auto_font: string;        // font tự chọn (khi auto)
  suggested: string[];      // font hợp phong cách kênh
  recipe: ThumbnailRecipeInfo | null;
  fonts: ThumbnailFontOption[];
};

// Font tiêu đề thumbnail + intro — RIÊNG từng project ("auto" = theo phong cách kênh).
export function useThumbnailFonts(projectId: number) {
  return useQuery<ThumbnailFontsData>({
    queryKey: ["thumbnail-fonts", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/thumbnail-fonts`)).data,
  });
}

export function useSetThumbnailFont(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.put(`${base(projectId)}/thumbnail-fonts`, { key }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["thumbnail-fonts"] }),
  });
}

// Máy tạo clip (chung mọi project): flow | gemini | muse.
export function useSetClipEngine(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (engine: string) => api.put(`${base(projectId)}/clip-engine`, { engine }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video"] }),
  });
}

// Ảnh mẫu: tên project viết bằng font `key` trên ảnh bìa của chính project.
export function thumbnailFontPreviewUrl(projectId: number, key: string,
                                        bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/thumbnail-fonts/${encodeURIComponent(key)}/preview${q}`;
}

function useVideoAction(projectId: number, path: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: any) =>
      api.post(`${base(projectId)}/${path}`, body ?? {}),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useCancelVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function usePauseVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/pause`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useResumeVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/resume`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export const useUploadYoutube = (pid: number) => useVideoAction(pid, "upload-youtube");
// Body {engine: "chatgpt" | "ai" | "pil"} — "chatgpt" = ChatGPT web (tài khoản của bạn,
// mặc định); "ai" tốn phí theo model ảnh (Cài đặt → Thumbnail AI); "pil" = chữ font.
export const useMakeThumbnail = (pid: number) => useVideoAction(pid, "thumbnail");
export const useUploadShort = (pid: number) => useVideoAction(pid, "upload-short");

export type ShortSettings = { auto_short: boolean; seconds: number; crop_x: number; hook: boolean };
export function useShortSettings(projectId: number) {
  return useQuery<ShortSettings>({
    queryKey: ["short-settings"],
    queryFn: async () => (await api.get(`${base(projectId)}/short-settings`)).data,
  });
}
export function useSaveShortSettings(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { auto_short?: boolean; short_seconds?: number }) =>
      api.post(`${base(projectId)}/short-settings`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["short-settings"] }),
  });
}

// Mở đúng cửa sổ/profile của tool (theo dõi/đăng nhập/kiểm tra).
export function useOpenVideoBrowser(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (site: "muse" | "flow" | "gemini" | "chatgpt") =>
      api.post(`${base(projectId)}/open-browser`, null, { params: { site } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}
export const useCloseVideoBrowser = (pid: number) => useVideoAction(pid, "close-browser");

export type YoutubeHistoryItem = {
  id: number;
  channel_name: string;
  title: string;
  kind?: "long" | "short";
  hashtags: string;
  status: string;
  error_message: string | null;
  created_at: string | null;
};

export function useYoutubeHistory(projectId: number) {
  return useQuery<YoutubeHistoryItem[]>({
    queryKey: ["youtube-history", projectId],
    queryFn: async () =>
      (await api.get(`${base(projectId)}/youtube-history`)).data,
    // Cập nhật lịch sử khi có job upload đang chạy.
    refetchInterval: (q) => (q.state.data ? false : false),
  });
}

export const useGenImages = (pid: number) => useVideoAction(pid, "images");
export const usePreparePrompts = (pid: number) => useVideoAction(pid, "prompts/prepare");
export function usePromptManifest(pid: number) {
  return useQuery<{ manifest: null | {
    workflow_version?: number;
    scene_count?: number;
    // prompts: 3 ảnh (0 bìa, 1 nhân vật chính, 2 linh thú).
    prompts?: Record<string, string>;
    // motions: prompt cảnh, khoá theo CHỈ SỐ CLIP (0..total_clips-1).
    motions?: Record<string, string>;
    continuity?: {
      character_sheet?: string; pet_sheet?: string; pet_name?: string;
      scene_sheet?: string; scene?: string;   // scene: chế độ 1 ảnh bìa
    };
    creative_brief?: { music?: string; visual?: string };
  } }>({
    queryKey: ["prompt-manifest", pid],
    queryFn: async () => (await api.get(`${base(pid)}/prompts`)).data,
  });
}
export const useGenClips  = (pid: number) => useVideoAction(pid, "clips");
export const useAssemble  = (pid: number) => useVideoAction(pid, "assemble");
export const useRunAll    = (pid: number) => useVideoAction(pid, "run-all");
export const useRebuild   = (pid: number) => useVideoAction(pid, "rebuild");
export const useRegenMotions = (pid: number) => useVideoAction(pid, "motions/regenerate");
// Ghép lại final.mp4 với footage đặt ở CUỐI video, phát tới hết video.
export const useRenderWithFootage = (pid: number) => useVideoAction(pid, "footage/render");

export function useUploadFootage(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      // Footage có thể vài trăm MB → upload lâu hơn 30s mặc định; không giới hạn thời gian.
      return api.post(`${base(projectId)}/footage`, form, { timeout: 0 });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useDeleteFootage(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    // name = xoá 1 footage; bỏ trống = xoá tất cả.
    mutationFn: (name?: string) =>
      api.delete(`${base(projectId)}/footage`, { params: name ? { name } : {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function videoFootageUrl(projectId: number, bust?: string | number,
                                name?: string): string {
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (bust != null) params.set("t", String(bust));
  const q = params.toString();
  return `${API_BASE}${base(projectId)}/footage${q ? `?${q}` : ""}`;
}

// Tạo lại 1 ảnh / 1 clip lẻ theo chỉ số — giữ nguyên các cái khác.
export function useRegenImage(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (index: number) =>
      api.post(`${base(projectId)}/images/${index}/regenerate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useRegenClip(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (index: number) =>
      api.post(`${base(projectId)}/clips/${index}/regenerate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

// URL xem trước ảnh/clip lẻ (kèm cache-buster để sau khi tạo lại thấy bản mới).
export function videoImageUrl(projectId: number, index: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/image/${index}${q}`;
}

export function videoClipUrl(projectId: number, index: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/clip/${index}${q}`;
}

export function videoDownloadUrl(projectId: number): string {
  return `${API_BASE}${base(projectId)}/download`;
}

// URL ảnh thumbnail (kèm chữ). Thêm cache-buster để sau mỗi lần ghép lại, ảnh
// mới hiển thị thay vì bản cache cũ của trình duyệt.
export function videoThumbnailUrl(projectId: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/thumbnail${q}`;
}

// ── "Tạo video thủ công" (bán tự động) ───────────────────────────
// Tool mở Flow + đưa 2 bảng nhân vật vào ô soạn; người dùng dán prompt + bấm
// Tạo; tool tải về + đặt tên clip_NN.mp4.
export type ManualClipItem = {
  index: number;
  name: string;
  ingredients: number[];
  prompt: string;        // đúng chữ tool sẽ gửi Flow (đã bọc phong cách + phủ định)
  done: boolean;
};

export type ManualState = {
  active: boolean;
  phase: "opening" | "preparing" | "waiting_user" | "downloading" | "done" | "error" | "stopped" | string;
  current: number | null;
  prompt: string;
  ingredients: number[];
  blocked: string | null;
  total: number;
  done: number[];
  todo: number[];
  message: string;
  updated_at?: number;
};

export function useManualClips(projectId: number, poll: boolean) {
  return useQuery<{ total: number; clips: ManualClipItem[]; state: ManualState | null }>({
    queryKey: ["manual-clips", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/clips/manual`)).data,
    refetchInterval: poll ? 2000 : false,
    retry: false,
  });
}

export function useStartManualClips(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: { only?: number[]; assemble?: boolean }) =>
      api.post(`${base(projectId)}/clips/manual`, body ?? {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", projectId] });
      qc.invalidateQueries({ queryKey: ["manual-clips", projectId] });
    },
  });
}

export function useImportClip(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ index, file }: { index: number; file: File }) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.post(`${base(projectId)}/clips/${index}/import`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", projectId] });
      qc.invalidateQueries({ queryKey: ["manual-clips", projectId] });
    },
  });
}
