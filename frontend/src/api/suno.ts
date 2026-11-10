import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  SunoConfigResponse,
  SunoGenerateStylesBody,
  SunoGenerateStylesResponse,
  SunoStartBody,
  SunoStatusResponse,
} from "../types";

const base = (pid: number) => `/api/projects/${pid}/suno`;

/** Cấu hình mặc định + preset (Styles/Exclusions xem trước). Tĩnh — cache lâu. */
export function useSunoConfig(projectId: number) {
  return useQuery<SunoConfigResponse>({
    queryKey: ["suno-config", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/config`)).data,
    staleTime: Infinity,
  });
}

/** Trạng thái batch + job. Poll khi job đang chạy để cập nhật % + counters. */
export function useSunoStatus(projectId: number) {
  return useQuery<SunoStatusResponse>({
    queryKey: ["suno-status", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/status`)).data,
    refetchInterval: (q) => {
      const s = q.state.data?.job?.status;
      return s === "pending" || s === "running" ? 1500 : false;
    },
  });
}

function useSunoPost<TBody = void>(projectId: number, path: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) =>
      api.post(`${base(projectId)}/${path}`, body ?? {}),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["suno-status", projectId] }),
  });
}

/** /dry-run: điền form + đọc credits/plan — KHÔNG tạo/tải, KHÔNG tiêu credit. */
export const useSunoDryRun = (pid: number) =>
  useSunoPost<SunoStartBody>(pid, "dry-run");

/** /start: LIVE — tạo 15 bài, tải WAV, xác thực, import. Người dùng chủ động bấm. */
export const useSunoStart = (pid: number) =>
  useSunoPost<SunoStartBody>(pid, "start");

/** /resume: tiếp tục batch đang dở — không tạo lại/tải lại/import lại phần đã xong. */
export const useSunoResume = (pid: number) => useSunoPost(pid, "resume");

/** /pause: dừng ở checkpoint gần nhất, giữ state để resume. */
export const useSunoPause = (pid: number) => useSunoPost(pid, "pause");

/** /cancel: huỷ hẳn batch (terminal). */
export const useSunoCancel = (pid: number) => useSunoPost(pid, "cancel");

/** /open-browser: mở UI Suno để đăng nhập + tự kiểm tra/sửa selector. Không
 * tạo/tải gì → không tốn credit. Giữ cửa sổ mở tới khi Đóng trình duyệt. */
export const useSunoOpenBrowser = (pid: number) =>
  useSunoPost(pid, "open-browser");

/** /close-browser: đóng cửa sổ đăng nhập/kiểm tra selector. */
export const useSunoCloseBrowser = (pid: number) =>
  useSunoPost(pid, "close-browser");

/** /generate-styles: AI viết Styles (+ Exclude) từ ý tưởng — LUÔN không lời,
 * KHÔNG mở trình duyệt, KHÔNG tiêu credit Suno (chỉ gọi LLM). */
export function useSunoGenerateStyles(projectId: number) {
  const qc = useQueryClient();
  return useMutation<SunoGenerateStylesResponse, unknown, SunoGenerateStylesBody>({
    mutationFn: async (body) =>
      (await api.post(`${base(projectId)}/generate-styles`, body)).data,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
}
