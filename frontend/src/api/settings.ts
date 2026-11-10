import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AppSettings,
  AppSettingsUpdate,
  ApiTestRequest,
  ApiTestResult,
  ChatGPTWindowStatus,
  ThumbAISettings,
  ThumbAISettingsUpdate,
  ThumbAITestRequest,
  ThumbAITestResult,
} from '../types';

export function useSettings() {
  return useQuery<AppSettings>({
    queryKey: ['settings'],
    queryFn: async () => {
      const res = await api.get('/api/settings');
      return res.data;
    },
    staleTime: 60_000, // Không refetch quá thường xuyên
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: AppSettingsUpdate) => {
      const res = await api.patch('/api/settings', data);
      return res.data as AppSettings;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}

export function useTestApi() {
  return useMutation({
    mutationFn: async (data: ApiTestRequest) => {
      // Timeout dài phía client vì proxy có thể chậm (server tự giới hạn 60s).
      const res = await api.post('/api/settings/test', data, {
        timeout: 75_000,
      });
      return res.data as ApiTestResult;
    },
  });
}

// ── Thumbnail AI (key RIÊNG cho model ảnh, vd. ShopAIKey) ─────────
export function useThumbAISettings() {
  return useQuery<ThumbAISettings>({
    queryKey: ['settings', 'thumbnail-ai'],
    queryFn: async () => {
      const res = await api.get('/api/settings/thumbnail-ai');
      return res.data;
    },
    staleTime: 60_000,
  });
}

export function useUpdateThumbAISettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: ThumbAISettingsUpdate) => {
      const res = await api.patch('/api/settings/thumbnail-ai', data);
      return res.data as ThumbAISettings;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'thumbnail-ai'] });
      queryClient.invalidateQueries({ queryKey: ['video'] });
    },
  });
}

/** Kiểm tra key MIỄN PHÍ (GET /v1/models) — không tạo ảnh, không tốn phí. */
export function useTestThumbAI() {
  return useMutation({
    mutationFn: async (data: ThumbAITestRequest) => {
      const res = await api.post('/api/settings/thumbnail-ai/test', data, {
        timeout: 45_000,
      });
      return res.data as ThumbAITestResult;
    },
  });
}

// ── ChatGPT web (máy vẽ thumbnail mặc định) — cửa sổ để bạn TỰ đăng nhập ──
export function useChatGPTWindow() {
  return useQuery<ChatGPTWindowStatus>({
    queryKey: ['settings', 'thumbnail-chatgpt'],
    queryFn: async () => (await api.get('/api/settings/thumbnail-chatgpt')).data,
    refetchInterval: (query) => (query.state.data?.open ? 3000 : false),
  });
}

function useChatGPTWindowAction(action: 'open' | 'close') {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post(`/api/settings/thumbnail-chatgpt/${action}`)).data as ChatGPTWindowStatus,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'thumbnail-chatgpt'] });
      queryClient.invalidateQueries({ queryKey: ['settings', 'thumbnail-ai'] });
      queryClient.invalidateQueries({ queryKey: ['global-tasks'] });
    },
  });
}

// ── Máy tạo video/ảnh qua GPMLogin (Muse.ai / Google Flow) ─────────
export interface EngineOption {
  key: string;
  label: string;
}

export interface VideoEngines {
  clip_engine: string;
  image_engine: string;
  image_fallback: boolean;
  muse_gpm_profile_id: string;
  flow_gpm_profile_id: string;
  clip_engines: EngineOption[];
  image_engines: EngineOption[];
}

export type VideoEnginesUpdate = Partial<
  Pick<VideoEngines, 'clip_engine' | 'image_engine' | 'image_fallback' |
    'muse_gpm_profile_id' | 'flow_gpm_profile_id'>
>;

export function useVideoEngines() {
  return useQuery<VideoEngines>({
    queryKey: ['settings', 'video-engines'],
    queryFn: async () => (await api.get('/api/settings/video-engines')).data,
  });
}

export function useUpdateVideoEngines() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: VideoEnginesUpdate) =>
      (await api.put('/api/settings/video-engines', data)).data as VideoEngines,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings', 'video-engines'], data);
      queryClient.invalidateQueries({ queryKey: ['video'] });
    },
  });
}

export const useOpenChatGPT = () => useChatGPTWindowAction('open');
export const useCloseChatGPT = () => useChatGPTWindowAction('close');
