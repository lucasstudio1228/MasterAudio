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

## 2026-11-16 17:30 — Gỡ toàn bộ phần video khỏi bản GitHub + bản cập nhật (patch) không cần cài lại

- **Yêu cầu:** "bản github này loại bỏ tất cả phần liên quan đến video. Tạo 1 bản patch mới để không phải xoá tất cả bản cũ đi cài lại".
- **Nguyên nhân:** bản GitHub (Cover Audio Tool) vẫn còn dựng video, thumbnail, đăng YouTube; người dùng cũ muốn lên bản mới phải xoá và cài lại từ đầu.
- **File đã đổi:**
  - Backend: xoá 30 file (`routers` video/youtube, 26 module `backend/video/*`, `cover/overlay`, `cover/youtube_text`); sửa `main.py`, `batch_service.py`, `routers/{mixes,projects,tasks,settings,track_qc}.py`, `mix_patch.py`, `video/service.py`, `video/prompt_workflow.py`, `project_progress.py` — lô luôn chạy chỉ audio.
  - Frontend: xoá `api/video.ts`, `api/youtube.ts`, `VideoPanel`, `ManualClipsPanel`, `PublishPanel`, 3 trang setting thumbnail/video/YouTube; sửa `ProjectFormModal`, `ProjectDetailPage` (4 tab), `TaskBar` (1 nút «🎧 Chạy tự động»), `AddTracksPanel`, `WorkflowStatusBar`, `RenameProjectDialog`, `CoverPanel`, `TrackQcPanel`, `ProjectsPage`, `SunoPanel`, `MixSettingsPanel`, `SunoCoverPanel`, `api/{projects,settings,trackQc}.ts`; build lại `frontend/dist`.
  - `.gitignore`: neo `/outputs/ /stems/ /Projects/ /media/ /Trash/` (trên Windows không phân biệt hoa thường, `Projects/`, `stems/` đã chặn nhầm `frontend/src/components/projects|stems` — 12 file này trước đây chưa từng lên GitHub), thêm `/_ban_cu_truoc_cap_nhat/`. `.gitattributes`: `*.ps1` CRLF.
  - Mới: `update.bat`, `update_mac.command`, `patch/apply_update.ps1`, `patch/removed_files.txt` (38 file cần xoá ở máy cũ).
  - `README.md`, `HUONG_DAN.md`: mục «Cập nhật lên bản mới», mục «Bản không còn phần video».
- **Trước:** có tab Video/Đăng YouTube, 2 nút auto; cập nhật phải cài lại.
- **Sau:** chỉ còn audio (4 bước); bấm đúp `update.bat` (Mac `update_mac.command`) để tải bản mới, sao lưu mã cũ vào `_ban_cu_truoc_cap_nhat`, chép đè mã, xoá file video cũ, giữ `.venv`, `data` (chỉ thêm file thiếu), media, outputs, đăng nhập, `.env`; pip chỉ chạy khi `requirements.txt` đổi. Tool đang chạy (cổng 8200) thì dừng, không cập nhật.
- **Kiểm thử:** `npm run build` OK. Thử `apply_update.ps1` trên bản sao 4beac96 có dữ liệu giả: xoá đúng 38 file, giữ setting/app.db/.env/media, chạy lần 2 xoá 0 file và bỏ qua pip. Server thử cổng 8299: 129 API, không còn đường dẫn video; giao diện 4 tab, không lỗi console. `update.bat`: tự chép ra TEMP, đường dẫn tiếng Việt, kiểm tra cổng, tải + giải nén OK. `bash -n update_mac.command` OK (chưa chạy thật trên Mac).
- **Tồn đọng:** chạy thử `update.bat` đầu-cuối sau khi đẩy lên GitHub; `update_mac.command` chưa thử trên máy Mac thật. Tool chính 8100 không đổi.

## 2026-11-16 17:45 — Mở tool bằng cửa sổ ứng dụng .exe (thay trình duyệt localhost)

- **Yêu cầu:** "Đưa giao diện về ứng dụng .exe thay vì chạy trên trình duyệt local host" + "đưa lên" GitHub.
- **Nguyên nhân:** `start.bat` mở cửa sổ đen + tab trình duyệt; đóng nhầm dễ rối.
- **File đã đổi:**
  - Mới `app_window.py`: cửa sổ pywebview (Edge WebView2 có sẵn trên Windows 10/11); tự bật server ẩn trên cổng đọc từ `start.bat`, chờ sẵn sàng rồi nạp giao diện; đóng cửa sổ thì tắt cả cây tiến trình server; đang có việc thì hỏi lại; server đã mở bằng start.bat thì chỉ gắn vào, không tắt; giữ localStorage ở `data/webview`; cho phép tải file; link ngoài mở trình duyệt; tự hỏi cài pywebview nếu thiếu; icon riêng trên taskbar.
  - Mới `launcher_exe/`: `Launcher.cs` (exe ~29 KB, build bằng csc có sẵn của Windows, tìm `.venv` cạnh exe hoặc thư mục cha), `build_exe.bat`, `setup_app.ps1` (tạo `CoverAudioTool.exe` + lối tắt Desktop «Cover Audio Tool», dùng IShellLinkW để giữ đường dẫn có dấu; lỗi không chặn cài đặt), `app.ico`, `make_icon.py`.
  - `install.bat`, `patch/apply_update.ps1` (bước 7): gọi `setup_app.ps1`. `update.bat`: thông báo mở bằng lối tắt Desktop.
  - `requirements.txt`: `pywebview>=6,<7; sys_platform == "win32"` (Mac bỏ qua). `.gitignore`: `/*.exe` (exe tự tạo trên máy, không đưa lên repo để tránh cảnh báo SmartScreen).
  - `README.md`, `HUONG_DAN.md`: mục «Mở tool bằng cửa sổ ứng dụng», sửa hướng dẫn sử dụng hằng ngày/cập nhật.
- **Trước:** start.bat + trình duyệt. **Sau:** lối tắt Desktop / CoverAudioTool.exe mở cửa sổ ứng dụng; start.bat vẫn dùng được. Mac không đổi (vẫn trình duyệt).
- **Kiểm thử:** bản sao 8299 DB trống: exe mở cửa sổ phóng to, server sẵn sàng ~3 giây, đóng cửa sổ thì server + tiến trình con tắt hết; kiểm tra logic đóng (đang có việc + Huỷ/OK, rảnh, server mở sẵn) đúng. Cập nhật bản b1cf6bb trong thư mục có dấu bằng `apply_update.ps1` mới: tự tạo exe + lối tắt, mở qua lối tắt OK, app.db giữ nguyên.
- **Tồn đọng:** chưa thử `install.bat` trên máy sạch; chưa thử nút tải file thật trong cửa sổ; máy chưa có WebView2 (Windows cũ) sẽ báo lỗi — dùng start.bat.

## 2026-11-16 18:30 — Viết lại hướng dẫn GitHub cho bản mới (README + HUONG_DAN)
- **Yêu cầu:** viết lại hướng dẫn trên GitHub cho phù hợp phiên bản mới.
- **Nguyên nhân:** hai file hướng dẫn chắp vá nhiều mục có ngày (12/11, 16/11), trùng nội dung nhau, còn mục lỗi thời («Chạy auto: 2 kiểu», «Bản không còn phần video» như nhật ký thay đổi).
- **File đã đổi:** `README.md`, `HUONG_DAN.md`, `AI_CHANGELOG.md`.
- **Trước:** README và HUONG_DAN gần giống hệt nhau, thông tin cửa sổ .exe nằm ở cuối, dòng «data\app.db có sẵn» sai (repo không kèm app.db).
- **Sau:** README = giới thiệu 4 bước + tải về + cài Windows (exe, lối tắt Desktop) + mở/đóng tool + cập nhật bằng update.bat + bảng lỗi Windows + cài Mac (chip M/Intel, bảng lỗi Mac) + dữ liệu & chia sẻ + lưu ý lyrics. HUONG_DAN = cách dùng hằng ngày: mở/đóng, tạo project (thủ công / Mix Set / Track lẻ), nút 🎧 Chạy tự động + Tạm dừng/Tiếp tục/Dừng lại, chi tiết ①–④ (quy trình Suno Cover, thùng rác), file nhạc + CapCut, bảng trục trặc. Bỏ các mục có ngày và mục lỗi thời.
- **Kiểm thử:** đối chiếu nhãn giao diện với mã (tên tab, + New Project, 3 kiểu tạo project, nút TaskBar, 🗑/♻, 📂 Mở thư mục nghe audio, ⬇ Lời cho CapCut, Mix History, Settings), install.bat/update.bat/apply_update.ps1/app_window.py; kiểm tra tệp UTF-8, không có ký tự điều khiển.
- **Kết quả:** chỉ đổi tài liệu, không đổi mã.
- **Tồn đọng:** không.

## 2026-11-17 — Tạo project không chạy auto, kéo thả bài cover có sẵn + file lời .srt, danh sách bài từ Excel, đổi tên «Cover Music by Lucas Studio»

- **Yêu cầu:** (1) thêm chế độ tạo bài / project thủ công, không chạy auto — người dùng chọn «Cả hai»: tạo từ danh sách mà không chạy + kiểu «Project từ bài cover có sẵn»; (2) kéo thả bài cover có sẵn thay vì «choose a file», bài nhập vào phải master + đồng bộ âm lượng, ghép lời để tạo file lời; (3) file danh sách bài nhận thêm Excel; đặt tên tool «Cover Music by Lucas Studio»; đẩy lên GitHub và viết lại README.
- **Nguyên nhân:** tạo Mix Set / Track lẻ từ danh sách luôn chạy auto ngay (tốn credit Suno); nhập bài cover có sẵn chỉ có ô chọn 1 file, không tự master; không có file lời .srt riêng từng bài; danh sách chỉ nhận .txt/.docx/.doc; tiến độ Track lẻ chưa chạy hiện nhầm «Đã xong Mix».
- **File đã đổi:**
  - Mới `backend/song_list.py`: đọc danh sách bài từ .txt/.docx/.doc (dò chữ UTF-16, best-effort)/.xlsx/.xlsm (đọc XML trong zip, trang tính đầu)/.xls (cần xlrd, không có thì khuyên lưu .xlsx)/.csv; bỏ hàng tiêu đề và cột STT; 2 cột ghép thành «Tên bài - Ca sĩ».
  - `backend/routers/projects.py`: `_read_mixset_lines` dùng `song_list`; mới `POST /api/projects/from-list` (tạo project + danh sách bài trạng thái «stopped», không chạy).
  - `backend/routers/sources.py`: `add_own_cover` thêm `auto_master` / `auto_align`; trả `master_queued`, `master_error`, `align_queued`.
  - `backend/routers/cover.py`: mới `GET /api/cover/songs/{id}/srt` (lời riêng 1 bài, UTF-8 BOM, CRLF, không có dòng tên bài).
  - `backend/project_progress.py`: nhãn tiến độ «Chưa bắt đầu» khi chưa có bước nào xong.
  - Đổi tên: `backend/edition.py`, `backend/main.py`, `frontend/index.html`, `Sidebar.tsx`, `start.bat` (dòng `REM APP_NAME:`), `app_window.py` (đọc tên từ dòng đó), `launcher_exe/setup_app.ps1` (tạo `CoverMusic.exe` + lối tắt «Cover Music by Lucas Studio», dọn lối tắt/exe tên cũ trỏ vào đúng thư mục), thông báo trong `install.bat`, `update.bat`, `install_mac.command`, `update_mac.command`, `patch/apply_update.ps1`.
  - Frontend: `api/projects.ts` (`useCreateFromList`), `api/sources.ts` (autoMaster/autoAlign), `api/cover.ts` (`songSrtUrl`), `ProjectFormModal.tsx` (4 kiểu tạo, lựa chọn «Chỉ tạo, tôi tự chạy» / «Tạo & chạy tự động», ô kéo thả file danh sách), `ProjectsPage.tsx`, mới `components/cover/CoverDropPanel.tsx` (kéo thả nhiều bài, gom bản (1)/(2), tự đọc «Tên bài - Ca sĩ», gắn file lời .txt/.lrc, tick master + đồng bộ âm lượng / AI căn lời), `SunoCoverPanel.tsx`, `CoverPanel.tsx` (nút ⬇ .srt từng bài); build lại `frontend/dist`.
  - `README.md`, `HUONG_DAN.md` viết lại theo bản mới.
- **Trước:** tạo từ danh sách = chạy auto ngay; nhập cover 1 file/lần, không master; chỉ có .lrc từng bài; danh sách không nhận Excel; tên «Cover Audio Tool».
- **Sau:** mặc định «Chỉ tạo, tôi tự chạy» (không tốn credit), chạy auto là tuỳ chọn; kiểu «📂 Project từ bài cover có sẵn» mở thẳng ô kéo thả; bài nhập được master về cùng LUFS + AI căn lời; mỗi bài có ⬇ .srt; danh sách nhận Excel/CSV; tên «Cover Music by Lucas Studio».
- **Kiểm thử:** parser 7/7 PASS (xlsx có STT + tiêu đề, xlsx 1 cột inlineStr, CSV `;`, txt gạch đầu dòng, .doc giả lập, .xls báo lời khuyên, đuôi lạ). API trên bản sao cổng 8299 DB trống: ALL PASS (from-list mixset/single, lỗi 422, `/api/tasks/active` rỗng, nhập 2 bản WAV + auto_master → master xong cả 2, .srt 404 trước khi căn, đúng sau khi lưu lời, không có tên bài). UI trong trình duyệt: form 4 kiểu, kiểu cover có sẵn chuyển sang `?tab=suno&drop=1`, kéo thả giả lập gom đúng 2 bản + lời, nhập + master xong, tab ③ có ⬇ .srt; Track lẻ chưa chạy hiện «Chưa bắt đầu».
- **Kết quả:** đạt; đã đồng bộ sang bản 8200 (`D:\Tools\Cover nhạc\Cover Audio Tool`).
- **Tồn đọng:** `.xls` cần thư viện xlrd (không có thì báo lưu .xlsx); `.doc` chỉ đọc best-effort; bản 8200 chưa chạy `setup_app.ps1` nên vẫn còn `CoverAudioTool.exe` cũ tới lần update/install sau; AI căn lời khi nhập chưa chạy thử thật (chỉ thử master). Tool chính 8100 không đổi.

## 2026-11-17 — Lời .srt: câu dài xuống dòng, tối đa 8 từ/dòng

- **Yêu cầu:** "phần lyrics đang hiện cả câu dài trên 1 dòng. Sửa chỉ để 8 từ trên 1 dòng, khoảng cách các dòng +1 (không được xít nhau quá)".
- **Nguyên nhân:** `build_srt` ghi nguyên câu trên 1 dòng → trong CapCut câu dài chạy tràn ngang.
- **File đã đổi:** `backend/cover/lyrics_export.py` (mới `wrap_lyric()` + `MAX_WORDS_PER_LINE = 8`; `build_srt` xuống dòng trong từng phụ đề), `HUONG_DAN.md` (cách tăng khoảng cách dòng trong CapCut), `AI_CHANGELOG.md`.
- **Trước:** mỗi phụ đề 1 dòng dài. **Sau:** câu > 8 từ chia đều thành nhiều dòng ≤ 8 từ (10 từ → 5 + 5, 11 → 6 + 5); áp dụng cho .srt bản mix và .srt từng bài. Mốc thời gian không đổi.
- **Kiểm thử:** `wrap_lyric` với câu 4/9/16/0 từ; `build_srt` câu 10 và 11 từ ra 2 dòng đúng; test API (.srt từng bài) PASS trên bản sao.
- **Kết quả:** đạt; đã chép sang bản 8200.
- **Tồn đọng:** SRT không mang được khoảng cách dòng — người dùng chỉnh «Khoảng cách dòng» trong CapCut (đã ghi trong HUONG_DAN).

## 2026-11-17 — Liên kết sang bản đầy đủ Music-Cover + update an toàn hơn

- **Yêu cầu:** "up lên github với đầy đủ hướng dẫn sử dụng cho cả 2 phiên bản" (MasterAudio = bản âm thanh, Music-Cover = tool chính).
- **Nguyên nhân:** hướng dẫn chưa trỏ tới bản đầy đủ; `apply_update.ps1` báo lỗi robocopy 16 nếu bản cài thiếu thư mục `frontend`/`backend`.
- **File đã đổi:** `README.md`, `HUONG_DAN.md` (thêm ghi chú + link tới https://github.com/lucasstudio1228/Music-Cover), `patch/apply_update.ps1` (bước sao lưu chỉ chạy khi thư mục tồn tại), `AI_CHANGELOG.md`.
- **Trước:** không có link sang bản video; update dừng giữa chừng nếu thiếu thư mục. **Sau:** có link; update bỏ qua phần sao lưu thiếu và chạy tiếp.
- **Kiểm thử:** chạy `apply_update.ps1` trên bản cài giả (có `data/app.db`, `media`): dữ liệu giữ nguyên, mã + `frontend/dist` mới được chép, file data còn thiếu được thêm.
- **Kết quả:** đạt. **Tồn đọng:** không.

## 2026-11-18 — Dán lời cho bài bị loại «thiếu lời» thì tool nhận lại bài

- **Yêu cầu:** "khi dán lời là phải nhận bài, đây hiện đang không pass" (ảnh: bài «Bỏ qua · thiếu lời» vẫn bị loại sau khi bấm Lưu lời).
- **Nguyên nhân:** `PUT /api/sources/{id}` chỉ lưu chữ lời; trạng thái `skipped_no_lyrics` và dòng báo lỗi giữ nguyên nên bài vẫn bị loại khỏi Suno Cover.
- **File đã đổi:** `backend/routers/sources.py` (`update_source`). Bản cũ cất ở `Trash/backup_truoc_dan_loi_20261118/` (máy).
- **Trước:** dán lời xong bài vẫn «Bỏ qua · thiếu lời». **Sau:** lời không trống → bài đã có file gốc chuyển «Đã tải» + tự xuất WAV tempo 4.5× chạy nền; bài chưa có file → tự xếp hàng tải bài gốc (giữ nguyên lời vừa dán). Lời trống thì vẫn để loại. Không bấm gì trên Suno.
- **Kiểm thử:** bản sao code + DB tạm (không mở trình duyệt): bài có file → `done`, hết lỗi, tempo 4.5× có file; bài chưa có file → được xếp tải, lời giữ nguyên; lời trống → vẫn `skipped_no_lyrics`. ALL PASS cho cả bản chính và bản 8200.
- **Tồn đọng:** lô Mix Set đã chạy qua bước bỏ bài thì không tự thêm lại bài vào lô đó — bấm chạy Suno cho bài sau khi nhận lại.

## 2026-11-18 23:50 — Sửa tool bấm nhầm trong giao diện Suno (nhảy sang trang Billing)

- **Yêu cầu:** khi ấn «Tạo lại» hoặc chạy auto, tool bấm nhầm trong giao diện Suno; chạy thử và theo dõi.
- **Nguyên nhân:** bài «Có Duyên Không Nợ» (2 lần 21:23 và 23:28) sau bước «Đã xoá form Create» trình duyệt nằm ở trang Billing/Subscription của Suno, tool vẫn đi tìm nút «Audio» trên trang đó rồi báo lỗi. `_real_click` bấm bằng toạ độ chuột mà không kiểm tra phần tử ở điểm đó có đúng là nút cần bấm không (bỏ qua kiểm tra «bị che» của Playwright) → khi popup/banner che hoặc layout vừa dịch, cú bấm rơi vào phần tử khác. `clear_form` còn bấm Confirm/Yes trong BẤT KỲ hộp thoại nào đang mở. Chạy lại thử (dry-run, 23:35) thì đi qua bình thường → lỗi xảy ra lúc có lúc không.
- **File đã đổi:** `backend/video/suno_driver.py` (`_real_click` + `_hit_ok`), `backend/cover/suno_cover.py` (`clear_form`, `_left_create_page`, `_prepare_create_form`, `prepare_cover`).
- **Trước:** bấm toạ độ mù; xác nhận mọi hộp thoại; rời trang Create vẫn làm tiếp.
- **Sau:** chỉ bấm chuột thật khi phần tử trên cùng tại điểm bấm đúng là nút đích (đo lại 1 lần, vẫn lệch thì để `locator.click()` có kiểm tra che xử lý); chỉ bấm Confirm khi hộp thoại có chữ clear/reset; sau mỗi bước Advanced / xoá form / More Options / Duration kiểm tra URL còn ở `/create`, lệch thì chụp ảnh `left_create_page`, mở lại trang Create làm lại 1 lần, vẫn lệch thì dừng rõ ràng, không bấm gì trên trang lạ.
- **Kiểm thử:** py_compile 4 bản; test Playwright trang giả (nút thường bấm được, nút bị link «Upgrade» che KHÔNG bị bấm, phát hiện rời trang Create) → ALL PASS trên tool chính và Cover Audio Tool. Chạy dry-run thật bài #175 trên Suno: qua bước xoá form bình thường (trước khi sửa; đã dừng job thử).
- **Kết quả:** đã áp dụng cho tool chính, Cover Audio Tool và 2 repo (chưa push).
- **Tồn đọng:** cần khởi động lại tool để nhận code mới; theo dõi log xem có dòng «bị phần tử khác che» / «rời trang Create» để biết phần tử nào hay che.
