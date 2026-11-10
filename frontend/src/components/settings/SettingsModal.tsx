import { useState, useEffect } from 'react';
import { useSettings, useUpdateSettings, useTestApi } from '../../api/settings';
import type { AppSettingsUpdate, ApiTestResult } from '../../types';
import { NctLoginSettings } from './NctLoginSettings';
import { SunoLoginSettings } from './SunoLoginSettings';

interface Props {
  open: boolean;
  onClose: () => void;
}

export function SettingsModal({ open, onClose }: Props) {
  const { data: settings, isLoading } = useSettings();
  const updateMutation = useUpdateSettings();
  const testMutation = useTestApi();

  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<ApiTestResult | null>(null);

  // Sync state khi settings load
  useEffect(() => {
    if (settings) {
      setBaseUrl(settings.api_base_url);
      setModel(settings.api_model);
      setApiKey(''); // Không hiện key thật — placeholder sẽ cho biết trạng thái
      setDirty(false);
      setTestResult(null);
    }
  }, [settings, open]);

  if (!open) return null;

  async function handleTest() {
    setTestResult(null);
    try {
      // Gửi giá trị đang gõ (trống → server dùng cấu hình đã lưu).
      const result = await testMutation.mutateAsync({
        api_key: apiKey.trim() || undefined,
        api_base_url: baseUrl.trim() || undefined,
        api_model: model.trim() || undefined,
      });
      setTestResult(result);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setTestResult({
        ok: false,
        model: model || '',
        base_url: baseUrl || '',
        error_type: 'RequestFailed',
        error: `Không gọi được backend: ${msg}`,
        retryable: true,
      });
    }
  }

  async function handleSave() {
    const payload: AppSettingsUpdate = {};
    if (apiKey.trim()) payload.api_key = apiKey.trim();
    if (baseUrl.trim()) payload.api_base_url = baseUrl.trim();
    if (model.trim()) payload.api_model = model.trim();

    await updateMutation.mutateAsync(payload);
    onClose();
  }

  async function handleClearKey() {
    await updateMutation.mutateAsync({ clear_api_key: true });
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-[#1a1d2e] rounded-xl p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-white">⚙️ Global Settings</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            ✕
          </button>
        </div>

        {isLoading ? (
          <p className="text-gray-400">Loading...</p>
        ) : (
          <div className="space-y-5">
            {/* API Key */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">
                API Key
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setDirty(true);
                }}
                placeholder={
                  settings?.has_api_key
                    ? 'sk-•••••••• (set — enter new to replace)'
                    : 'sk-...'
                }
                className="w-full bg-[#0f1117] border border-white/10 rounded-lg
                           px-3 py-2 text-sm text-white placeholder:text-gray-600
                           focus:outline-none focus:border-violet-500"
              />

              {/* Status + Clear */}
              <div className="flex items-center justify-between mt-1.5">
                <span
                  className={`text-xs ${
                    settings?.has_api_key ? 'text-green-400' : 'text-gray-500'
                  }`}
                >
                  {settings?.has_api_key ? '● Key is set' : '○ No key set'}
                </span>
                {settings?.has_api_key && (
                  <button
                    onClick={handleClearKey}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    Clear Key
                  </button>
                )}
              </div>
              <p className="text-xs text-yellow-400/70 mt-1">
                ⚠️ API key is stored in plaintext in{' '}
                <code className="text-yellow-300">data/app.db</code>. Do not
                share this file.
              </p>
            </div>

            {/* Base URL */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">
                API Base URL
              </label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => {
                  setBaseUrl(e.target.value);
                  setDirty(true);
                }}
                className="w-full bg-[#0f1117] border border-white/10 rounded-lg
                           px-3 py-2 text-sm text-white
                           focus:outline-none focus:border-violet-500"
              />
            </div>

            {/* Model */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">
                Model
              </label>
              <input
                type="text"
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  setDirty(true);
                }}
                className="w-full bg-[#0f1117] border border-white/10 rounded-lg
                           px-3 py-2 text-sm text-white
                           focus:outline-none focus:border-violet-500"
              />
            </div>

            {/* Info */}
            <p className="text-xs text-gray-500 border-t border-white/5 pt-4">
              ℹ️ Settings apply to all projects. Leave API key empty to use the
              <code className="text-gray-400 mx-1">OPENAI_API_KEY</code>
              environment variable as fallback.
            </p>

            {/* Kết quả Test API */}
            {testResult && (
              <div
                className={`rounded-lg px-3 py-2.5 text-sm border ${
                  testResult.ok
                    ? 'bg-green-500/10 border-green-500/30 text-green-300'
                    : 'bg-red-500/10 border-red-500/30 text-red-300'
                }`}
              >
                {testResult.ok ? (
                  <div>
                    <div className="font-medium">
                      ✅ API hoạt động
                      {typeof testResult.latency_ms === 'number' && (
                        <span className="text-green-400/70 font-normal">
                          {' '}
                          ({testResult.latency_ms} ms)
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-green-400/70 mt-1">
                      Model: <code>{testResult.model}</code>
                      {testResult.reply ? ` · trả lời: "${testResult.reply}"` : ''}
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="font-medium">❌ Gọi API thất bại</div>
                    <div className="text-xs text-red-300/90 mt-1">
                      {testResult.error}
                    </div>
                    {testResult.retryable && (
                      <div className="text-xs text-yellow-400/80 mt-1">
                        ⏳ Lỗi tạm thời — có thể chờ ~120s rồi bấm Test lại.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Đăng nhập nhaccuatui (tab ① Tải nhạc gốc) */}
            <NctLoginSettings />

            {/* Đăng nhập Suno (tab ② Suno Cover) */}
            <SunoLoginSettings />

            {/* Cover Audio Tool: không có Thumbnail / YouTube / máy tạo video. */}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 mt-6">
          <button
            onClick={handleTest}
            disabled={testMutation.isPending}
            className="px-4 py-2 text-sm border border-white/15 hover:border-white/30
                       hover:bg-white/5 disabled:opacity-40 rounded-lg
                       text-gray-200 font-medium"
          >
            {testMutation.isPending ? '⏳ Testing…' : '🔌 Test API'}
          </button>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-400 hover:text-white"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={updateMutation.isPending || !dirty}
              title="Chỉ lưu API Key / Base URL / Model. Phần YouTube/GPMLogin có nút Lưu riêng."
              className="px-4 py-2 text-sm bg-violet-600 hover:bg-violet-500
                         disabled:opacity-40 rounded-lg text-white font-medium"
            >
              {updateMutation.isPending ? 'Đang lưu…' : 'Lưu API (OpenAI)'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
