import { Link, useLocation } from 'react-router-dom';

export default function Sidebar() {
  const { pathname } = useLocation();
  const isProjects = pathname === '/' || pathname.startsWith('/projects');
  return (
    <aside className="flex w-56 flex-col border-r border-white/5 bg-surface p-4">
      <div className="mb-8 flex items-center gap-2 px-2 text-lg font-bold text-white">
        <span>🎵</span>
        <span>Cover Audio</span>
      </div>
      <nav className="flex flex-col gap-1">
        <Link
          to="/projects"
          className={`rounded-lg px-3 py-2 text-sm font-medium ${
            isProjects
              ? 'bg-accent/20 text-accent'
              : 'text-gray-300 hover:bg-surface-2'
          }`}
        >
          Projects
        </Link>
      </nav>
      <div className="mt-auto px-2 text-xs text-gray-500">Local · lossless WAV</div>
    </aside>
  );
}
