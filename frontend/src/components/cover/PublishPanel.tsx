const card = 'rounded-xl border border-white/5 bg-surface p-4';
const h2 = 'mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400';
const btn = 'rounded-lg bg-surface-2 px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10';

// Tab ⑥ — đợt C sẽ chuyển phần xem trước + đăng nháp YouTube từ tab Video sang đây.
export default function PublishPanel({ onGoToVideo }: { onGoToVideo: () => void }) {
  return (
    <div className={`${card} max-w-2xl`}>
      <h2 className={h2}>⑥ Duyệt &amp; đăng</h2>
      <p className="rounded-lg bg-blue-600/15 p-2 text-sm text-blue-100">
        🚧 Đợt C: tab này sẽ có nút <b>▶ Xem trước video</b> và nút <b>Tự động tải nháp YouTube</b>
        (AI viết tiêu đề + mô tả, đánh dấu «không dành cho trẻ em» và «có dùng AI»). Tool chỉ lưu
        nháp — bạn tự công khai.
      </p>
      <p className="mt-3 text-sm text-gray-300">
        Hiện tại phần đăng nháp YouTube vẫn nằm cuối tab ⑤ Video.
      </p>
      <button className={`${btn} mt-3`} onClick={onGoToVideo}>Sang tab ⑤ Video →</button>
    </div>
  );
}
