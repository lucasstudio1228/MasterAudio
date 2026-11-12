# AI_CHANGELOG — Cover Audio Tool

## 2026-11-10 01:05 — Tạo tool từ «Cover nhạc»
- **Yêu cầu:** tách phần Audio (①②③④) thành tool riêng, giữ nguyên setting, người khác tải về dùng được ngay; thêm thư mục `nhac_mix` chứa 1 file nhạc đã mix.
- **Thay đổi so với tool gốc:** `backend/edition.py` (AUDIO_ONLY), `backend/video/job_manager.py` (chỉ chạy job "suno"), `backend/routers/mixes.py` (không auto dựng video; xuất nhac_mix), `backend/batch_service.py` (hoàn tất = có mix.wav / file cover), `backend/main.py` (CORS 5184), giao diện bỏ ⑤⑥ + Settings video, cổng 8200, `HUONG_DAN.md`.
- **Kiểm thử:** test mix/batch của tool gốc chạy trên mã này → OK; chạy thử giao diện → OK.
- **Tồn đọng:** chưa thử `install.bat` trên máy sạch.

## 2026-11-12 10:35 — Remix → Cover không ăn ngay sau «Save as New Song»: thử lại tối đa 3 lần

- **Yêu cầu:** "đang lỗi. Cho chạy và Claude giám sát để có quy trình chuẩn" (project #27 Lưu Niên - Jack J97, thẻ lỗi "Đã bấm Remix → Cover nhưng Suno không chuyển sang form Create").
- **Nguyên nhân:** log `_suno.log` + ảnh `_debug/create_remix_cover_no_navigation_101746.png`: lượt 1 tool bấm ⋯ → Remix → Cover chỉ ~11 giây sau khi người dùng bấm Save as New Song; menu Remix vẫn mở, Suno không nhận cú bấm (bài vừa lưu còn đang xử lý) → tool dừng cả hàng đợi ngay. Lượt chạy lại (10:19) cùng bài đó: bấm Cover → chuyển sang `/create?remix_clip=<id>&remix_type=cover` trong 1 giây, form điền đủ lyrics/style/thanh trượt. Selector không sai, chỉ thiếu bước chờ/thử lại.
- **File đã đổi:** `backend/cover/suno_cover.py` (`_remix_cover_from_library` lặp tối đa 3 lần qua hàm mới `_remix_cover_once`; giữa các lần: Escape đóng menu, chờ 15s rồi 30s, tải lại trang bài, chờ hết "Preparing…"; mỗi lần hỏng lưu ảnh `remix_cover_no_navigation_try<N>`), `tests/test_suno_semi_manual.py` (+`RemixCoverRetryTests`: lần 2 thành công; 3 lần hỏng → báo lỗi, không tự `goto`). Cover Audio Tool và GitHub MasterAudio: cùng sửa `suno_cover.py` + mục "Quy trình chuẩn Suno Cover" trong `HUONG_DAN.md`.
- **Trước:** 1 lần bấm Cover không chuyển trang → dừng cả hàng đợi, người dùng phải bấm chạy lại.
- **Sau:** tool tự chờ và bấm lại (tổng tối đa ~2 phút); vẫn không tự điều hướng /create (tránh mất audio nguồn), vẫn không bấm Create.
- **Kiểm thử:** `tests.test_suno_semi_manual` → 12 OK; 19 module test Suno: 269 test, 16 FAIL/ERROR đều là lỗi có sẵn từ trước (không có lỗi mới). Theo dõi trực tiếp lượt chạy #27: Remix → Cover OK, form Cover đã điền, đang chờ người dùng bấm Create.
- **Tồn đọng:** backend 8100 phải restart khi KHÔNG có job mới dùng mã mới (hiện đang có job #27). Tempo trong Editor vẫn "không đọc được" lúc Save (đọc được sau ở form: 32 BPM). 16 test Suno lỗi sẵn chưa xử lý.

## 2026-11-12 11:05 — Lô sản xuất Track lẻ giữ cả 2 bản Suno

- **Yêu cầu:** "Tool hiện chỉ nhận 1 bản" → người dùng chọn «Giữ cả 2 bản».
- **Nguyên nhân:** `_cover_chain` trong `backend/batch_service.py` cố ý giữ bản đầu và đưa bản thứ 2 của mỗi bài vào thùng rác track ("mỗi dòng = đúng một bài"). Project #27 Lưu Niên: Suno tạo/tải/master đủ 2 bản nhưng `Lưu Niên - cover 2.wav` bị chuyển vào thùng rác lúc 10:25.
- **File đã đổi:** `backend/batch_service.py` (bỏ bước đưa bản 2 vào thùng rác khi project là `single`; Mix Set giữ quy tắc cũ để mix không phát một bài 2 lần; hàm mới `_first_cover_audio` để video Track lẻ luôn dùng bản 1 thay vì file có mtime mới nhất), `tests/test_cover_singles_batch.py` (+`FirstCoverAudioTests`). Cover Audio Tool + GitHub MasterAudio: cùng sửa `batch_service.py`. Dữ liệu: khôi phục `Lưu Niên - cover 2.wav` vào #27 bằng API `POST /api/projects/27/tracks/recovery/restore` (trash id 48).
- **Trước:** Track lẻ chạy theo lô chỉ còn 1 bản trong project; bản 2 nằm trong thùng rác.
- **Sau:** Track lẻ giữ đủ 2 bản (cover.wav vị trí 1, cover 2.wav vị trí 2); video, lời .srt và tên bài vẫn theo bản 1. Mix Set không đổi.
- **Kiểm thử:** `tests.test_cover_singles_batch tests.test_continue_chain tests.test_track_recovery tests.test_suno_semi_manual` → 34 OK. #27 sau khôi phục có 2 CoverSong cùng source 167.
- **Tồn đọng:** backend 8100 cần restart (hiện không có job) để dùng mã mới (cả bản sửa Remix → Cover). Job dựng video #27 đang ở trạng thái "Đã huỷ" — cần bấm chạy tiếp.
