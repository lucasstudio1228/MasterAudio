import SunoLoginPanel from '../cover/SunoLoginPanel';

/** Cài đặt → đăng nhập Suno (để tab ② tự làm cover trên Suno). */
export function SunoLoginSettings() {
  return (
    <div className="space-y-3 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">🎤 Suno — làm bản cover</h3>
      <p className="text-xs text-gray-400">
        Tool dùng tài khoản Suno của bạn (credit của bạn) để tự làm cover ở tab ② Suno Cover.
      </p>
      <SunoLoginPanel />
    </div>
  );
}
