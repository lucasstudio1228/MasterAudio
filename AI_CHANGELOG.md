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

## 2026-11-12 10:45 — Chạy auto 2 kiểu (Audio + Video / Chỉ audio) + nút mở thư mục audio/video trên máy

- **Yêu cầu:** "Chạy auto có 2 kiểu, auto làm cả audio và video. Chạy auto chỉ audio. Sau khi tạo xong audio, lời bài hát sẽ có nút ấn vào thư mục trên máy tính để nghe. Video sau khi render xong cũng vậy, có nút bấm vào thư mục trên máy tính để xem final".
- **Nguyên nhân:** lô Mix Set/Track lẻ và nút 🚀 luôn chạy tới final.mp4 + nháp YouTube, không có cách dừng ở audio; tool chưa có nút mở thư mục trên máy (chỉ có nút tải file qua trình duyệt).
- **File đã đổi:**
  - `backend/batch_service.py`: `create_cover_mixset`/`create_cover_singles` nhận `audio_only` (lưu `params_json.audio_only`, project `auto_video/auto_upload = not audio_only`); hàm mới `_is_audio_only`, `set_audio_only`, `audio_folder`, `_verify_audio`; `_cover_chain` ở chế độ Chỉ audio dừng sau master (Track lẻ) hoặc sau ghép mix không dựng video (Mix Set); `_verify_product` chấm xong theo audio khi lô Chỉ audio.
  - `backend/routers/projects.py`: form `/mixset`, `/singles` nhận `audio_only`; endpoint mới `GET /api/projects/{id}/folders`, `POST /api/projects/{id}/open-folder?what=audio|video` (mở File Explorer; video chọn sẵn final.mp4).
  - `backend/routers/tasks.py`: `/api/tasks/auto-run?audio_only=` — đổi chế độ lô có sẵn, Chỉ audio thì không dựng video.
  - Frontend: `types/index.ts`, `api/projects.ts` (`useProjectFolders`, `useOpenProjectFolder`), `api/tasks.ts`, `TaskBar.tsx` (2 nút «🎧 Auto chỉ audio» / «🚀 Auto audio + video»), `ProjectFormModal.tsx` (chọn «Kiểu chạy auto»), `CoverPanel.tsx` (nút «📂 Mở thư mục nghe audio» ở tab ③ Lời bài hát), `VideoPanel.tsx` (nút «📂 Mở thư mục video final»).
  - Test mới `tests/test_audio_only_mode.py` (chỉ tool chính).
- **Trước:** auto luôn làm cả audio + video; muốn nghe/xem phải tự tìm thư mục media.
- **Sau:** chọn được Audio + Video (mặc định, như cũ) hoặc Chỉ audio khi tạo lô và ở thanh tác vụ; nút mở thư mục chỉ hiện khi đã có audio / final.mp4.
- **Kiểm thử:** `tests.test_audio_only_mode` + `tests.test_cover_singles_batch` 15 OK; 8 module project/task (gồm `test_tasks_auto`) 64 OK. `npm run build` OK (tool chính, Cover Audio Tool). Kiểm tra trên server thử cổng 8101: nút mở thư mục audio của #27 → POST open-folder 200; nút video final hiện ở #19; form tạo lô hiện lựa chọn Kiểu chạy auto; không tạo lô thật.
- **Tồn đọng:** backend 8100 phải khởi động lại (khi không có job) mới có endpoint mới — trước đó nút «Auto chỉ audio» gửi tới backend cũ sẽ bị bỏ qua tham số và chạy cả video. Cover Audio Tool (8200) cũng cần khởi động lại.

## 2026-11-12 11:40 — Tab Suno Cover: nút «🗑 Xoá bản này» cho từng bản cover + thùng rác khôi phục theo bài

- **Yêu cầu:** "phần này có nhiều bản, thêm nút chức năng xoá bản nào không sử dụng. Để ở thùng rác, có thể phục hồi" (danh sách Bài gốc → bản cover, vd Lưu Niên có 4 bản).
- **Nguyên nhân:** mỗi bài có nhiều bản Suno nhưng tab Suno Cover chỉ có «Báo lỗi»/«Tạo lại», không có cách bỏ bản không dùng; thùng rác track đã có ở backend nhưng chỉ dùng được ở phần thêm file nhạc.
- **File đã đổi:**
  - `backend/routers/tracks.py`: `/tracks/recovery` trả thêm `source_id` cho mỗi mục (hàm mới `_snapshot_source_id`); `_orphan_cover_files` bỏ qua file đang nằm trong thùng rác (trước đó 1 bản đã xoá hiện trùng 2 lần: mục thùng rác + "file cũ").
  - `frontend/src/api/tracks.ts`: kiểu `source_id`; xoá/khôi phục track làm mới luôn query `sources` và `cover-songs`.
  - `frontend/src/components/cover/SunoCoverPanel.tsx`: nút «🗑 Xoá bản này» ở mỗi bản cover (hỏi xác nhận, khoá khi bài đang chạy Suno); khối «🗑 Thùng rác (N)» dưới mỗi bài: nghe thử, «♻ Khôi phục» từng bản, «♻ Khôi phục tất cả».
  - Test: `tests/test_track_recovery.py` thêm `test_delete_one_cover_version_then_restore_it` (chỉ tool chính).
- **Trước:** không xoá được từng bản cover ở tab Suno Cover.
- **Sau:** xoá bản không dùng → vào thùng rác (file trên máy giữ nguyên), khôi phục lại đúng vị trí/bài gốc/lời. Bài đã «Loại bài» vẫn khôi phục bằng nút của bài như cũ (không hiện thùng rác riêng).
- **Kiểm thử:** `tests.test_track_recovery` + `tests.test_audio_only_mode` 9 OK. Chạy thêm 6 module cover: lỗi chỉ nằm ở `tests.test_suno_cover` (FakeDriver thiếu tham số `manual_editor` — test cũ từ trước, không liên quan thay đổi này). `npm run build` OK. Server thử 8101: project #27 hiện 4 nút «🗑 Xoá bản này»; không xoá dữ liệu thật.
- **Tồn đọng:** khởi động lại backend 8100/8200 khi rảnh để có `source_id` (trước đó khối thùng rác theo bài chưa hiện, nút xoá vẫn chạy). Cập nhật FakeDriver trong `tests/test_suno_cover.py` cho tham số `manual_editor`.
