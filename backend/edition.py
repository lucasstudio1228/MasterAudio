"""Phiên bản tool: Cover Music by Lucas Studio — trước đây «Cover Audio Tool» (tách từ Cover nhạc, 2026-11-10).

Chỉ gồm ① Tải nhạc gốc → ② Suno Cover → ③ Lời bài hát → ④ Mix.
Không dựng video, không đăng YouTube.
"""
AUDIO_ONLY = True
APP_NAME = "Cover Music by Lucas Studio"
# Loại job được phép chạy trên video_job_manager (Suno dùng chung manager này).
ALLOWED_JOB_KINDS = {"suno"}
