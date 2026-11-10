import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

// ── GPMLogin profiles + config ──────────────────────────────────
export type GpmProfile = {
  id: string;
  name: string;
  group_id?: string;
  group_name?: string;
};

export type GpmProfilesResult = {
  ok: boolean;
  base_url: string;
  profiles: GpmProfile[];
  error?: string;
};

export function useGpmProfiles() {
  return useQuery<GpmProfilesResult>({
    queryKey: ["gpm-profiles"],
    queryFn: async () => (await api.get("/api/youtube/gpm/profiles")).data,
    staleTime: 30_000,
  });
}

export type GpmConfig = {
  gpm_api_base_url: string;
  gpm_exe_path: string | null;
};

export function useGpmConfig() {
  return useQuery<GpmConfig>({
    queryKey: ["gpm-config"],
    queryFn: async () => (await api.get("/api/youtube/gpm/config")).data,
  });
}

export function useUpdateGpmConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<GpmConfig>) =>
      api.patch("/api/youtube/gpm/config", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gpm-config"] });
      qc.invalidateQueries({ queryKey: ["gpm-profiles"] });
    },
  });
}

// ── Channel mappings ────────────────────────────────────────────
export type ChannelMapping = {
  id: number;
  instrument: string;      // nhạc cụ (tool cũ) — tool Cover không dùng nữa
  // Dòng nhạc của kênh: VN / US-UK / Chinese / Japanese… (gợi ý, có thể trống).
  music_line: string;
  music_style: string;
  gpm_profile_id: string;
  channel_name: string;
  default_hashtags: string;
  language: string;
  // Phong cách hình ảnh của kênh (chọn tự do — nhiều kênh dùng chung được).
  visual_look?: string;
  look_label?: string;
};

// Không gửi instrument → server giữ nguyên nhạc cụ cũ của kênh.
export type MappingBody = Omit<ChannelMapping, "id" | "look_label" | "instrument">;

// ── Phong cách kênh (thumbnail + ảnh + màu/chuyển cảnh video) ─────
export type ChannelLook = {
  key: string;
  icon: string;
  label: string;
  desc: string;
  styles: { key: string; label: string; icon: string }[];
  transitions: string[];
  // true = phong cách người dùng tự nhập (sửa / xoá được).
  custom: boolean;
  // Look có sẵn mà phong cách tự nhập kế thừa font / màu / chuyển cảnh.
  base: string;
  // Mô tả gốc người dùng gõ (rỗng với look có sẵn).
  user_desc: string;
  used_by: string;
  used_by_ids: number[];
};

export type CustomLookBody = {
  label: string;
  description: string;
  base: string;
  styles: string[];
  icon: string;
};

export function useChannelLooks() {
  return useQuery<ChannelLook[]>({
    queryKey: ["channel-looks"],
    queryFn: async () => (await api.get("/api/youtube/looks")).data,
  });
}

function useLookMutation<T>(fn: (v: T) => Promise<{ data: unknown }>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channel-looks"] });
      qc.invalidateQueries({ queryKey: ["channel-mappings"] });
    },
  });
}

// Mô tả tiếng Việt được dịch sang tiếng Anh (gọi API) → không dùng timeout mặc định.
export function useCreateLook() {
  return useLookMutation((body: CustomLookBody) =>
    api.post("/api/youtube/looks", body, { timeout: 120_000 }));
}

export function useUpdateLook() {
  return useLookMutation(({ key, body }: { key: string; body: CustomLookBody }) =>
    api.patch(`/api/youtube/looks/${key}`, body, { timeout: 120_000 }));
}

export function useDeleteLook() {
  return useLookMutation((key: string) => api.delete(`/api/youtube/looks/${key}`));
}

/** Lỗi API → chuỗi dễ đọc (ưu tiên `detail` của FastAPI). */
export function apiErrorText(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  return e?.message ?? String(err);
}

export function useChannelMappings() {
  return useQuery<ChannelMapping[]>({
    queryKey: ["channel-mappings"],
    queryFn: async () => (await api.get("/api/youtube/mappings")).data,
  });
}

export function useCreateMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MappingBody) => api.post("/api/youtube/mappings", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channel-mappings"] });
      qc.invalidateQueries({ queryKey: ["channel-looks"] });
    },
  });
}

export function useUpdateMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: MappingBody }) =>
      api.patch(`/api/youtube/mappings/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channel-mappings"] });
      qc.invalidateQueries({ queryKey: ["channel-looks"] });
    },
  });
}

export function useDeleteMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete(`/api/youtube/mappings/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channel-mappings"] });
      qc.invalidateQueries({ queryKey: ["channel-looks"] });
    },
  });
}
