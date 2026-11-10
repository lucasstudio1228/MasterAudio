import NctLoginPanel from '../cover/NctLoginPanel';

/** Cài đặt → đăng nhập nhaccuatui (để tab ① tải được file nhạc gốc). */
export function NctLoginSettings() {
  return (
    <div className="space-y-3 border-t border-white/10 pt-5">
      <h3 className="text-base font-semibold text-white">🎵 nhaccuatui — tải nhạc gốc</h3>
      <p className="text-xs text-gray-400">
        Không đăng nhập thì tool vẫn tìm được bài và lấy được lời, nhưng chưa tải được file nhạc.
      </p>
      <NctLoginPanel />
    </div>
  );
}
