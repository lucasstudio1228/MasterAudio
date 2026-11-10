import { useState } from 'react';
import type { ReactNode } from 'react';
import Sidebar from './Sidebar';
import TaskBar from './TaskBar';
import { SettingsModal } from '../settings/SettingsModal';
import { useSettings } from '../../api/settings';

interface Props {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}

export default function AppShell({ title, actions, children }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { data: settings } = useSettings();

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex min-h-0 flex-1">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between gap-4 border-b border-white/5 px-6 py-4">
          <h1 className="text-xl font-semibold text-white">{title}</h1>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {actions}
            <button
              onClick={() => setSettingsOpen(true)}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-2 text-sm text-gray-400 transition-colors hover:border-violet-500/50 hover:text-white"
              title="Global Settings"
            >
              <span>⚙️</span>
              <span>Settings</span>
              {settings && !settings.has_api_key && (
                <span
                  className="h-2 w-2 rounded-full bg-yellow-400"
                  title="API key not configured"
                />
              )}
            </button>
          </div>
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
      </div>
      {/* Luôn hiện tác vụ đang chạy dù đang xem project nào */}
      <TaskBar />

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
