# AI_CHANGELOG — Cover Audio Tool

## 2026-11-10 01:05 — Tạo tool từ «Cover nhạc»
- **Yêu cầu:** tách phần Audio (①②③④) thành tool riêng, giữ nguyên setting, người khác tải về dùng được ngay; thêm thư mục `nhac_mix` chứa 1 file nhạc đã mix.
- **Thay đổi so với tool gốc:** `backend/edition.py` (AUDIO_ONLY), `backend/video/job_manager.py` (chỉ chạy job "suno"), `backend/routers/mixes.py` (không auto dựng video; xuất nhac_mix), `backend/batch_service.py` (hoàn tất = có mix.wav / file cover), `backend/main.py` (CORS 5184), giao diện bỏ ⑤⑥ + Settings video, cổng 8200, `HUONG_DAN.md`.
- **Kiểm thử:** test mix/batch của tool gốc chạy trên mã này → OK; chạy thử giao diện → OK.
- **Tồn đọng:** chưa thử `install.bat` trên máy sạch.
