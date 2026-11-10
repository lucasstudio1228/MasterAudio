"""
migrations.py – Tự động phát hiện và xử lý schema thay đổi.
Chạy tại startup, trước create_db_and_tables().
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"


def _get_conn():
    return sqlite3.connect(str(DB_PATH))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate_remove_project_api_fields():
    """
    Migration M001: Xóa api_key, api_base_url, api_model khỏi bảng project.
    Dùng pattern: create_temp → copy → drop_old → rename.
    """
    if not DB_PATH.exists():
        return  # DB chưa tạo, không cần migrate

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "api_key"):
            return  # Đã migrate rồi

        print("  [Migration M001] Removing API fields from project table...")
        conn.execute("BEGIN")

        # Lấy schema hiện tại, lọc bỏ 3 cột API
        cur = conn.execute("PRAGMA table_info(project)")
        all_cols = [(row[1], row[2]) for row in cur.fetchall()]
        keep_cols = [
            (name, typ) for name, typ in all_cols
            if name not in ("api_key", "api_base_url", "api_model")
        ]
        col_names = [name for name, _ in keep_cols]
        col_defs = ", ".join(f"{name} {typ}" for name, typ in keep_cols)
        col_list = ", ".join(col_names)

        conn.execute(f"CREATE TABLE project_new ({col_defs})")
        conn.execute(
            f"INSERT INTO project_new ({col_list}) SELECT {col_list} FROM project")
        conn.execute("DROP TABLE project")
        conn.execute("ALTER TABLE project_new RENAME TO project")

        conn.execute("COMMIT")
        print("  [Migration M001] Done.")
    except Exception as e:
        conn.execute("ROLLBACK")
        raise RuntimeError(f"Migration M001 failed: {e}") from e
    finally:
        conn.close()


def migrate_create_stem_directories():
    """M002: Tạo thư mục stems/."""
    stems_root = Path(__file__).parent.parent / "stems"
    stems_root.mkdir(exist_ok=True)


def migrate_add_denoise_columns():
    """
    M003: Thêm cột den_other/den_bass/den_drums/den_vocals vào trackstem
    (độ mạnh khử noise thủ công mỗi stem, 0.0 = auto theo volume như cũ).
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "trackstem"):
            return  # Bảng chưa có → create_db_and_tables sẽ tạo đủ cột
        cols = ["den_other", "den_bass", "den_drums", "den_vocals"]
        missing = [c for c in cols if not _column_exists(conn, "trackstem", c)]
        if not missing:
            return  # Đã migrate rồi
        print(f"  [Migration M003] Adding denoise columns: {missing}")
        for col in missing:
            conn.execute(
                f"ALTER TABLE trackstem ADD COLUMN {col} FLOAT DEFAULT 0.0")
        conn.commit()
        print("  [Migration M003] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M003 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_video_columns():
    """
    M004: Thêm cột video_idea + auto_video vào bảng project.
    - video_idea TEXT DEFAULT ''  : ý tưởng lưu sẵn để AI viết prompt.
    - auto_video BOOLEAN DEFAULT 0 : project CŨ mặc định TẮT auto-render.
      (Project MỚI tạo qua ORM nhận default True từ model, ghi rõ giá trị
       lúc INSERT nên không phụ thuộc DEFAULT của cột.)
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        added = []
        if not _column_exists(conn, "project", "video_idea"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN video_idea TEXT DEFAULT ''")
            added.append("video_idea")
        if not _column_exists(conn, "project", "auto_video"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN auto_video BOOLEAN DEFAULT 0")
            added.append("auto_video")
        if not added:
            return  # Đã migrate rồi
        print(f"  [Migration M004] Adding project video columns: {added}")
        conn.commit()
        print("  [Migration M004] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M004 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_video_style():
    """
    M005: Thêm cột video_style vào bảng project.
    - video_style TEXT DEFAULT '2d' : phong cách ảnh/video ("2d"/"3d").
      Cả project CŨ lẫn MỚI mặc định 2D theo yêu cầu.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "video_style"):
            return  # Đã migrate rồi
        print("  [Migration M005] Adding project column: video_style")
        conn.execute(
            "ALTER TABLE project ADD COLUMN video_style TEXT DEFAULT '2d'")
        conn.commit()
        print("  [Migration M005] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M005 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_youtube_upload_columns():
    """
    M006: Chuẩn bị cho tính năng auto-upload YouTube qua GPMLogin.
    - project: thêm instrument / music_style / auto_upload (project cũ: auto TẮT).
    - appsettings: thêm gpm_api_base_url / gpm_exe_path (cấu hình Local API).
    Hai bảng mới (channelmapping, youtubeupload) do create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        added = []
        if _table_exists(conn, "project"):
            if not _column_exists(conn, "project", "instrument"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN instrument TEXT DEFAULT ''")
                added.append("project.instrument")
            if not _column_exists(conn, "project", "music_style"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN music_style TEXT DEFAULT ''")
                added.append("project.music_style")
            if not _column_exists(conn, "project", "auto_upload"):
                conn.execute(
                    "ALTER TABLE project ADD COLUMN auto_upload BOOLEAN DEFAULT 0")
                added.append("project.auto_upload")
        if _table_exists(conn, "appsettings"):
            if not _column_exists(conn, "appsettings", "gpm_api_base_url"):
                conn.execute(
                    "ALTER TABLE appsettings ADD COLUMN gpm_api_base_url "
                    "TEXT DEFAULT 'http://localhost:9495'")
                added.append("appsettings.gpm_api_base_url")
            if not _column_exists(conn, "appsettings", "gpm_exe_path"):
                conn.execute(
                    "ALTER TABLE appsettings ADD COLUMN gpm_exe_path TEXT")
                added.append("appsettings.gpm_exe_path")
        if not added:
            return  # Đã migrate rồi
        print(f"  [Migration M006] Adding YouTube upload columns: {added}")
        conn.commit()
        print("  [Migration M006] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M006 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_suno_columns():
    """
    M007: Chuẩn bị STEP 0 (tạo nhạc nền Suno).
    - project: thêm music_source (mặc định 'local' → giữ nguyên import thủ công;
      'suno' = tạo 15 bài WAV từ Suno). Project cũ mặc định 'local'.
    Hai bảng mới (sunobatch, sunocandidate) do create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "music_source"):
            return  # Đã migrate rồi
        print("  [Migration M007] Adding project column: music_source")
        conn.execute(
            "ALTER TABLE project ADD COLUMN music_source TEXT DEFAULT 'local'")
        conn.commit()
        print("  [Migration M007] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M007 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_suno_idea_column():
    """
    M008: thêm project.suno_idea (ý tưởng/nhạc cụ cho STEP 0 — AI viết Styles từ
    đây, luôn nhạc không lời). Project cũ mặc định '' (rỗng).
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "suno_idea"):
            return  # Đã migrate rồi
        print("  [Migration M008] Adding project column: suno_idea")
        conn.execute(
            "ALTER TABLE project ADD COLUMN suno_idea TEXT DEFAULT ''")
        conn.commit()
        print("  [Migration M008] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M008 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_batch_id():
    """
    M009: thêm project.batch_id (nhóm project cùng 1 lô sản xuất hàng loạt).
    Project cũ mặc định NULL (tạo lẻ). Bảng mới `batchrun` do
    create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return  # create_db_and_tables sẽ tạo đủ cột
        if _column_exists(conn, "project", "batch_id"):
            return  # Đã migrate rồi
        print("  [Migration M009] Adding project column: batch_id")
        conn.execute("ALTER TABLE project ADD COLUMN batch_id TEXT")
        conn.commit()
        print("  [Migration M009] Done.")
    except Exception as e:
        raise RuntimeError(f"Migration M009 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_mix_settings():
    """
    M010: thêm project.mix_duration_minutes + mix_crossfade_seconds — thời lượng
    mix mà chuỗi tự động (Suno→Mix→Video) dùng; trước đây cứng 60'/15s.
    """
    if not DB_PATH.exists():
        return

    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        for col, default in (("mix_duration_minutes", 120.0),
                             ("mix_crossfade_seconds", 5.0)):
            if _column_exists(conn, "project", col):
                continue
            print(f"  [Migration M010] Adding project column: {col}")
            conn.execute(
                f"ALTER TABLE project ADD COLUMN {col} FLOAT DEFAULT {default}")
        conn.commit()
    except Exception as e:
        raise RuntimeError(f"Migration M010 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_trash_columns():
    """
    M011: thêm project.deleted_at + trash_dir (Thùng rác 7 ngày trước khi xoá
    vĩnh viễn).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        for col, ddl in (("deleted_at", "DATETIME"), ("trash_dir", "VARCHAR")):
            if not _column_exists(conn, "project", col):
                print(f"  [Migration M011] Adding project column: {col}")
                conn.execute(f"ALTER TABLE project ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M011 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_channel_visual_look():
    """
    M012: thêm channelmapping.visual_look (phong cách kênh — mỗi kênh 1 look
    riêng: phong cách ảnh + kiểu thumbnail + màu/chuyển cảnh video). Rỗng = tự gán
    ở lần đọc đầu (video/channel_looks.ensure_channel_looks).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "channelmapping"):
            return
        if not _column_exists(conn, "channelmapping", "visual_look"):
            print("  [Migration M012] Adding channelmapping column: visual_look")
            conn.execute("ALTER TABLE channelmapping ADD COLUMN visual_look "
                         "VARCHAR NOT NULL DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M012 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_thumbnail_ai_settings():
    """
    M013: cấu hình Thumbnail AI qua API (key/base URL/model riêng + bật/tắt +
    kiểm tra chữ) trong appsettings.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "appsettings"):
            return
        cols = {
            "thumb_api_key": "VARCHAR",
            "thumb_api_base_url": "VARCHAR NOT NULL DEFAULT 'https://api.shopaikey.com'",
            "thumb_api_model": "VARCHAR NOT NULL DEFAULT 'gemini-3-pro-image-preview'",
            "thumb_ai_enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "thumb_ai_verify": "BOOLEAN NOT NULL DEFAULT 1",
        }
        for col, ddl in cols.items():
            if not _column_exists(conn, "appsettings", col):
                print(f"  [Migration M013] Adding appsettings column: {col}")
                conn.execute(f"ALTER TABLE appsettings ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M013 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_thumbnail_engine():
    """
    M014: máy vẽ thumbnail mặc định (chatgpt | api | pil) trong appsettings —
    mặc định ChatGPT web từ 2026-10-23.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "appsettings"):
            return
        if not _column_exists(conn, "appsettings", "thumb_engine"):
            print("  [Migration M014] Adding appsettings column: thumb_engine")
            conn.execute("ALTER TABLE appsettings ADD COLUMN thumb_engine "
                         "VARCHAR NOT NULL DEFAULT 'chatgpt'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M014 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_channel_id():
    """
    M015: thêm project.channel_id — kênh YouTube người dùng chọn cho project,
    độc lập với nhạc cụ/thể loại. NULL = tra theo (instrument, music_style) như cũ.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "channel_id"):
            print("  [Migration M015] Adding project column: channel_id")
            conn.execute("ALTER TABLE project ADD COLUMN channel_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_project_channel_id "
                         "ON project (channel_id)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M015 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_intro_cast():
    """
    M016: thêm project.intro_mode ('thumbnail' = intro dựng từ ảnh thumbnail —
    MẶC ĐỊNH cả project cũ; 'font' = chữ font đè clip 00 như trước) và
    project.cast_mode ('character_pet' mặc định; 'scenery' = chỉ cảnh vật).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "intro_mode"):
            print("  [Migration M016] Adding project column: intro_mode")
            conn.execute("ALTER TABLE project ADD COLUMN intro_mode TEXT "
                         "DEFAULT 'thumbnail'")
        if not _column_exists(conn, "project", "cast_mode"):
            print("  [Migration M016] Adding project column: cast_mode")
            conn.execute("ALTER TABLE project ADD COLUMN cast_mode TEXT "
                         "DEFAULT 'character_pet'")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M016 failed: {e}") from e
    finally:
        conn.close()


def migrate_create_custom_look_table():
    """
    M017: bảng customlook — phong cách kênh người dùng tự nhập (tên + mô tả →
    câu thẩm mỹ tiếng Anh, kế thừa 1 look có sẵn). Kể từ đây 2 kênh được dùng
    chung 1 phong cách (không còn ràng buộc mỗi kênh 1 look khác nhau).
    """
    if not DB_PATH.exists():
        return
    from backend.video.channel_looks import CUSTOM_TABLE_SQL
    conn = _get_conn()
    try:
        if not _table_exists(conn, "customlook"):
            print("  [Migration M017] Creating table: customlook")
            conn.execute(CUSTOM_TABLE_SQL)
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M017 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_coversong_source_id():
    """
    M018: thêm coversong.source_id — bản cover này hát lại bài gốc nào (tab ①
    Tải nhạc gốc). Bảng mới `sourcesong` do create_db_and_tables() tạo.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "coversong"):
            return
        if not _column_exists(conn, "coversong", "source_id"):
            print("  [Migration M018] Adding coversong column: source_id")
            conn.execute("ALTER TABLE coversong ADD COLUMN source_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_coversong_source_id "
                         "ON coversong (source_id)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M018 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_suno_status():
    """
    M019: thêm sourcesong.suno_status — bài gốc đang chờ tạo lại bản cover trên
    Suno (bản cũ bị lỗi) hay không.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "suno_status"):
            print("  [Migration M019] Adding sourcesong column: suno_status")
            conn.execute("ALTER TABLE sourcesong ADD COLUMN suno_status TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M019 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_suno_fields():
    """
    M020: đợt B — tool tự làm cover trên Suno: sourcesong.suno_error (lý do lỗi),
    suno_song_ids (id 2 bài Suno đã tạo → thử lại chỉ TẢI, không tạo lại tốn
    credit), vocal_gender ("" tự đoán | male | female).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        for col, ddl in (("suno_error", "TEXT"),
                         ("suno_song_ids", "TEXT DEFAULT ''"),
                         ("vocal_gender", "TEXT DEFAULT ''")):
            if not _column_exists(conn, "sourcesong", col):
                print(f"  [Migration M020] Adding sourcesong column: {col}")
                conn.execute(f"ALTER TABLE sourcesong ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M020 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_channelmapping_music_line():
    """
    M021: channelmapping.music_line — dòng nhạc của kênh (VN / US-UK / Chinese /
    Japanese…) thay cho ô «nhạc cụ» trong Settings của tool Cover.
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "channelmapping"):
            return
        if not _column_exists(conn, "channelmapping", "music_line"):
            print("  [Migration M021] Adding channelmapping column: music_line")
            conn.execute("ALTER TABLE channelmapping ADD COLUMN music_line TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M021 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_music_line():
    """
    M022: project.music_line — dòng nhạc của bài (VN / US-UK / Chinese…) để tự
    chọn kênh theo dòng nhạc + thể loại (tool Cover).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        if not _column_exists(conn, "project", "music_line"):
            print("  [Migration M022] Adding project column: music_line")
            conn.execute("ALTER TABLE project ADD COLUMN music_line TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M022 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_lyrics_source():
    """
    M023: sourcesong.lyrics_source — lời lấy từ đâu khi nhaccuatui chưa có lời
    (tool tự tìm trên Google).
    """
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "lyrics_source"):
            print("  [Migration M023] Adding sourcesong column: lyrics_source")
            conn.execute("ALTER TABLE sourcesong ADD COLUMN lyrics_source TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M023 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_tempo_fields():
    """M024: bản nhạc gốc đã đổi tempo, dùng làm đầu vào bắt buộc cho Suno Cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        fields = {
            "tempo_factor": "REAL DEFAULT 1.0",
            "tempo_file_path": "TEXT DEFAULT ''",
        }
        for col, ddl in fields.items():
            if not _column_exists(conn, "sourcesong", col):
                print(f"  [Migration M024] Adding sourcesong column: {col}")
                conn.execute(f"ALTER TABLE sourcesong ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M024 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_editor_song_id():
    """M025: song trung gian đã lưu từ Suno Editor để resume Remix/Cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "suno_editor_song_id"):
            print("  [Migration M025] Adding sourcesong column: suno_editor_song_id")
            conn.execute("ALTER TABLE sourcesong ADD COLUMN suno_editor_song_id TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M025 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_suno_lyrics():
    """M026: lyrics đã qua bước 6 (ChatGPT web) để điền vào Suno Cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "lyrics_suno_text"):
            print("  [Migration M026] Adding sourcesong column: lyrics_suno_text")
            conn.execute(
                "ALTER TABLE sourcesong ADD COLUMN lyrics_suno_text TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M026 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_suno_style_prompt():
    """M027: cache prompt Styles do API AI viết cho từng bài Suno Cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        for col in ("suno_style_prompt", "suno_style_signature"):
            if not _column_exists(conn, "sourcesong", col):
                print(f"  [Migration M027] Adding sourcesong column: {col}")
                conn.execute(
                    f"ALTER TABLE sourcesong ADD COLUMN {col} TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M027 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_featured_instrument():
    """M028: nhạc cụ nổi bật tùy chọn cho từng bài Suno Cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "featured_instrument"):
            print("  [Migration M028] Adding sourcesong column: featured_instrument")
            conn.execute(
                "ALTER TABLE sourcesong ADD COLUMN featured_instrument TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M028 failed: {e}") from e
    finally:
        conn.close()


def migrate_cover_settings_follow_genre():
    """M029: cài đặt video cover THEO THỂ LOẠI project (2026-10-29).

    Trước đây mọi project đều được tạo sẵn language='vi', cover_style='ballad'
    — kể cả project Deep House / Mix Việt. Giờ ô trống = «theo thể loại», nên
    đổi về trống những giá trị còn là mặc định cũ (vi / ballad) hoặc trùng với
    gợi ý theo thể loại. Giá trị người dùng đã tự chọn khác (vd 'en' cho project
    không phải US-UK) được giữ. Chỉ chạy 1 lần (ghi dấu trong app_migration)."""
    if not DB_PATH.exists():
        return
    from backend.cover.genre_defaults import genre_defaults
    name = "M029_cover_settings_follow_genre"
    conn = _get_conn()
    try:
        if not (_table_exists(conn, "coversettings") and _table_exists(conn, "project")):
            return
        conn.execute("CREATE TABLE IF NOT EXISTS app_migration (name TEXT PRIMARY KEY)")
        if conn.execute("SELECT 1 FROM app_migration WHERE name = ?", (name,)).fetchone():
            return
        rows = conn.execute(
            "SELECT c.id, c.language, c.cover_style, p.music_style, p.music_line, "
            "p.description, p.name FROM coversettings c "
            "JOIN project p ON p.id = c.project_id").fetchall()
        changed = 0
        for cid, lang, style, music_style, music_line, desc, pname in rows:
            d = genre_defaults(music_style or "", music_line or "", desc or "", pname or "")
            new_lang = "" if (lang or "") in ("", "vi", d["language"]) else lang
            new_style = "" if (style or "") in ("", "ballad", d["cover_style"]) else style
            if (new_lang, new_style) != (lang, style):
                conn.execute("UPDATE coversettings SET language = ?, cover_style = ? "
                             "WHERE id = ?", (new_lang, new_style, cid))
                changed += 1
        conn.execute("INSERT INTO app_migration (name) VALUES (?)", (name,))
        conn.commit()
        if changed:
            print(f"  [Migration M029] {changed} project: cài đặt cover chuyển sang «theo thể loại»")
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M029 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_coversettings_show_waveform():
    """M030: bật/tắt sóng âm nhảy theo nhạc trên video cover (mặc định bật)."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "coversettings"):
            return
        if not _column_exists(conn, "coversettings", "show_waveform"):
            print("  [Migration M030] Adding coversettings column: show_waveform")
            conn.execute(
                "ALTER TABLE coversettings ADD COLUMN show_waveform BOOLEAN DEFAULT 1")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M030 failed: {e}") from e
    finally:
        conn.close()


def migrate_default_style_real():
    """M031: phong cách ảnh mặc định đổi 2D → Tả thực điện ảnh (2026-10-29).

    Project còn để '2d' (mặc định cũ) mà CHƯA có ảnh nào → chuyển sang 'real'.
    Project đã có ảnh giữ nguyên để ảnh/clip cũ không lệch phong cách.
    Chỉ chạy 1 lần (ghi dấu trong app_migration)."""
    if not DB_PATH.exists():
        return
    name = "M031_default_style_real"
    conn = _get_conn()
    try:
        if not _table_exists(conn, "project"):
            return
        conn.execute("CREATE TABLE IF NOT EXISTS app_migration (name TEXT PRIMARY KEY)")
        if conn.execute("SELECT 1 FROM app_migration WHERE name = ?", (name,)).fetchone():
            return
        from backend.video import config as vconfig
        changed = 0
        for pid, pname in conn.execute(
                "SELECT id, name FROM project WHERE video_style = '2d'").fetchall():
            try:
                has_images = any(vconfig.images_dir(pid, pname).glob("*.png"))
            except Exception:
                has_images = True
            if not has_images:
                conn.execute("UPDATE project SET video_style = 'real' WHERE id = ?", (pid,))
                changed += 1
        conn.execute("INSERT INTO app_migration (name) VALUES (?)", (name,))
        conn.commit()
        if changed:
            print(f"  [Migration M031] {changed} project chưa có ảnh: 2D → Tả thực điện ảnh")
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M031 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_editor_bpm():
    """M032: BPM Suno Editor tùy chỉnh riêng khi người dùng tạo lại một bài."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "suno_editor_bpm"):
            print("  [Migration M032] Adding sourcesong column: suno_editor_bpm")
            conn.execute(
                "ALTER TABLE sourcesong ADD COLUMN suno_editor_bpm INTEGER DEFAULT 0")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M032 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_sourcesong_preserve_style():
    """M033: lượt tạo lại chỉ đổi Tempo phải giữ nguyên prompt Styles đã dùng."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "sourcesong"):
            return
        if not _column_exists(conn, "sourcesong", "suno_preserve_style"):
            print("  [Migration M033] Adding sourcesong column: suno_preserve_style")
            conn.execute(
                "ALTER TABLE sourcesong ADD COLUMN suno_preserve_style BOOLEAN DEFAULT 0")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M033 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_coversong_tempo():
    """M034: chỉnh tempo bản cover đã tải về (tab ② Suno Cover — thanh tempo + OK)."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if not _table_exists(conn, "coversong"):
            return
        for col, ddl in (("tempo_factor", "REAL DEFAULT 1.0"),
                         ("tempo_backup_path", "TEXT DEFAULT ''")):
            if not _column_exists(conn, "coversong", col):
                print(f"  [Migration M034] Adding coversong column: {col}")
                conn.execute(f"ALTER TABLE coversong ADD COLUMN {col} {ddl}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M034 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_mix_gain_and_project_kind():
    """M035: chỉnh tay độ to từng bài khi Mix + loại project Mix Set / Track lẻ."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if _table_exists(conn, "coversong") and not _column_exists(conn, "coversong",
                                                                   "mix_gain_db"):
            print("  [Migration M035] Adding coversong column: mix_gain_db")
            conn.execute("ALTER TABLE coversong ADD COLUMN mix_gain_db REAL DEFAULT 0.0")
        if _table_exists(conn, "project") and not _column_exists(conn, "project", "kind"):
            print("  [Migration M035] Adding project column: kind")
            conn.execute("ALTER TABLE project ADD COLUMN kind TEXT DEFAULT 'mixset'")
            conn.execute("UPDATE project SET kind = 'mixset' WHERE kind IS NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_project_kind ON project (kind)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M035 failed: {e}") from e
    finally:
        conn.close()


def migrate_strip_lyrics_header():
    """M036: bỏ dòng "Bài hát: Tên - Ca sĩ" (nhaccuatui chèn đầu lời) khỏi lời gốc
    đã lưu. Chỉ sửa lời GỐC (sourcesong/coversong.lyrics_text) — lời bước 6 do
    người dùng làm (lyrics_suno_text) giữ nguyên. Bài đã căn lời thì không đụng
    coversong để khỏi lệch với file căn lời."""
    if not DB_PATH.exists():
        return
    from backend.cover.lyrics import strip_lyrics_header
    conn = _get_conn()
    try:
        fixed = 0
        for table, extra in (("sourcesong", ""),
                             ("coversong", " AND align_status IN ('none','failed')")):
            if not _table_exists(conn, table) or not _column_exists(conn, table, "lyrics_text"):
                continue
            if extra and not _column_exists(conn, table, "align_status"):
                continue
            rows = conn.execute(
                f"SELECT id, lyrics_text FROM {table} WHERE lyrics_text IS NOT NULL{extra}"
            ).fetchall()
            for row_id, text in rows:
                new = strip_lyrics_header(text).strip()
                if new and new != (text or "").strip():
                    conn.execute(f"UPDATE {table} SET lyrics_text = ? WHERE id = ?",
                                 (new, row_id))
                    fixed += 1
        if fixed:
            print(f"  [Migration M036] Bỏ dòng tiêu đề khỏi lời: {fixed} bản ghi")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M036 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_footage_scene_mode():
    """M037: bố cục footage trong TV/máy chiếu, mặc định tự theo cast_mode."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if _table_exists(conn, "project") and not _column_exists(
                conn, "project", "footage_scene_mode"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN footage_scene_mode TEXT "
                "NOT NULL DEFAULT 'auto'")
            print("  [Migration M037] Adding project.footage_scene_mode")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M037 failed: {e}") from e
    finally:
        conn.close()


def migrate_add_project_creative_instrument():
    """M038: giữ lựa chọn nhạc cụ sáng tạo cho mọi kiểu project cover."""
    if not DB_PATH.exists():
        return
    conn = _get_conn()
    try:
        if _table_exists(conn, "project") and not _column_exists(
                conn, "project", "creative_instrument"):
            conn.execute(
                "ALTER TABLE project ADD COLUMN creative_instrument BOOLEAN "
                "NOT NULL DEFAULT 1")
            print("  [Migration M038] Adding project.creative_instrument")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration M038 failed: {e}") from e
    finally:
        conn.close()


def run_all():
    """Gọi hàm này ở startup, trước create_db_and_tables()."""
    migrate_remove_project_api_fields()   # M001
    migrate_create_stem_directories()      # M002
    migrate_add_denoise_columns()          # M003
    migrate_add_project_video_columns()    # M004
    migrate_add_project_video_style()      # M005
    migrate_add_youtube_upload_columns()   # M006
    migrate_add_suno_columns()             # M007
    migrate_add_suno_idea_column()         # M008
    migrate_add_project_batch_id()         # M009
    migrate_add_project_mix_settings()     # M010
    migrate_add_project_trash_columns()    # M011 — Thùng rác
    migrate_add_channel_visual_look()      # M012 — phong cách kênh
    migrate_add_thumbnail_ai_settings()    # M013 — thumbnail AI qua API
    migrate_add_thumbnail_engine()         # M014 — thumbnail ChatGPT mặc định
    migrate_add_project_channel_id()       # M015 — kênh chọn riêng từng project
    migrate_add_project_intro_cast()       # M016 — intro thumbnail + chỉ cảnh vật
    migrate_create_custom_look_table()     # M017 — phong cách kênh tự nhập
    migrate_add_coversong_source_id()      # M018 — cover ↔ bài gốc nhaccuatui
    migrate_add_sourcesong_suno_status()   # M019 — chờ tạo lại cover trên Suno
    migrate_add_sourcesong_suno_fields()   # M020 — tool tự làm cover trên Suno
    migrate_add_channelmapping_music_line()  # M021 — dòng nhạc của kênh
    migrate_add_project_music_line()  # M022 — dòng nhạc của project
    migrate_add_sourcesong_lyrics_source()  # M023 — lời tìm trên Google
    migrate_add_sourcesong_tempo_fields()  # M024 — xuất nhạc đã chỉnh tempo cho Suno
    migrate_add_sourcesong_editor_song_id()  # M025 — resume sau Save as New Song
    migrate_add_sourcesong_suno_lyrics()  # M026 — bước 6 lyrics qua ChatGPT web
    migrate_add_sourcesong_suno_style_prompt()  # M027 — Styles Cover do API AI viết
    migrate_add_sourcesong_featured_instrument()  # M028 — nhạc cụ riêng từng bài
    migrate_cover_settings_follow_genre()  # M029 — cài đặt cover theo thể loại
    migrate_add_coversettings_show_waveform()  # M030 — sóng âm trên video
    migrate_default_style_real()           # M031 — mặc định Tả thực điện ảnh
    migrate_add_sourcesong_editor_bpm()    # M032 — BPM Editor riêng khi tạo lại
    migrate_add_sourcesong_preserve_style()  # M033 — tạo lại chỉ đổi Tempo
    migrate_add_coversong_tempo()          # M034 — chỉnh tempo bản cover đã tải
    migrate_add_mix_gain_and_project_kind()  # M035 — độ to từng bài + Mix Set/Track lẻ
    migrate_strip_lyrics_header()          # M036 — bỏ "Bài hát: Tên - Ca sĩ" đầu lời
    migrate_add_footage_scene_mode()       # M037 — TV/máy chiếu trình bày footage
    migrate_add_project_creative_instrument()  # M038 — nhạc cụ sáng tạo dùng chung
    # Thêm migration mới vào đây theo thứ tự
