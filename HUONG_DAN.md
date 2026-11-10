# Cover Audio Tool — Hướng dẫn nhanh

Tool làm phần **âm thanh** cho nhạc cover, tách ra từ tool "Cover nhạc" (2026-11-10).
Gồm 4 bước, giữ nguyên mọi setting đang dùng:

1. **① Tải nhạc gốc** — tìm và tải bài gốc + lời (nhaccuatui / YouTube Music).
2. **② Suno Cover** — tạo bản cover bằng Suno (tempo, style, master giữ nguyên như tool chính).
3. **③ Lời bài hát** — kiểm tra / căn lời.
4. **④ Mix** — cân âm lượng, ghép các bài thành 1 bản mix.

Không có phần dựng video và đăng YouTube.

## Cài đặt trên Windows (chỉ làm 1 lần)

Máy cần có sẵn:
- **Python 3.11** (khi cài nhớ tick "Add Python to PATH") — https://www.python.org/downloads/
- **ffmpeg** — mở PowerShell gõ: `winget install Gyan.FFmpeg`
- **Google Chrome** (dùng để mở Suno).

Sau đó bấm đúp **`install.bat`** và chờ cài xong (lần đầu có thể mất 10–20 phút).

## Cài đặt trên Mac (macOS)

1. Mở ứng dụng **Terminal** (Cmd + Space → gõ "Terminal").
2. Cài **Homebrew** nếu chưa có (dán lệnh ở https://brew.sh vào Terminal, Enter).
3. Dán lệnh sau vào Terminal rồi Enter:
   ```bash
   brew install python@3.11 ffmpeg
   ```
4. Cài **Google Chrome** (dùng để mở Suno; không có Chrome thì tool tự dùng Chromium).
5. Trong thư mục tool, bấm đúp **`install_mac.command`** và chờ cài xong.
   - Nếu Mac báo *"không thể mở vì từ nhà phát triển không xác định"*: **chuột phải** vào file →
     **Open (Mở)** → **Open** lần nữa. Chỉ cần làm 1 lần cho mỗi file.
   - Nếu báo *"không có quyền"*: mở Terminal, gõ `chmod +x ` (có dấu cách), kéo thả thư mục
     tool vào, xoá dấu `/` cuối rồi thêm `/*.command`, Enter. Ví dụ:
     ```bash
     chmod +x ~/Downloads/MasterAudio-main/*.command
     ```
6. Hằng ngày: bấm đúp **`start_mac.command`** → trình duyệt tự mở **http://localhost:8200**.

Mac chip M1/M2/M3 chạy được nhưng tách giọng (Demucs) sẽ chậm hơn máy Windows có card NVIDIA.

## Sử dụng hằng ngày

1. Bấm đúp **`start.bat`** (Mac: **`start_mac.command`**). Trình duyệt tự mở **http://localhost:8200**.
2. Lần đầu: bấm **⚙ Settings** →
   - **Đăng nhập Suno** và **nhaccuatui** (mỗi người tự đăng nhập tài khoản của mình).
   - Dán **API Key** của mình nếu dùng AI (địa chỉ API và model đã điền sẵn).
3. Tạo project mới → làm lần lượt ① → ② → ③ → ④.

Tool chạy cổng **8200** nên dùng song song được với tool Cover nhạc đầy đủ (cổng 8100).

## File nhạc nằm ở đâu?

- Mỗi project có thư mục **`media\...\<tên project>\nhac_mix\`** chứa **đúng 1 file nhạc
  đã mix** (`<tên project>.wav`). Mix lại thì file này được thay bằng bản mới nhất.
- Bản mix gốc kèm thông tin chi tiết nằm trong `outputs\<số project>\<số mix>\`.

## Setting đã giữ sẵn

- `data\master_config.json` — thông số master (EQ, compressor, LUFS…).
- `data\suno_workflow.json` — chế độ chạy Suno.
- `data\app.db` — địa chỉ API + model AI (không kèm API key), chưa có project nào.
- Các thông số Suno/tempo/thể loại nằm sẵn trong mã nguồn, không cần chỉnh.

**Không** kèm theo: mật khẩu, cookie đăng nhập Suno/nhaccuatui, API key — mỗi người tự đăng nhập/nhập key.

## Khi chia sẻ cho người khác

Nén cả thư mục `Cover Audio Tool` (sau khi đã dùng, **bỏ** các thư mục `.venv`,
`.browser_profile_*`, `media`, `outputs`, `stems` và file `data\app.db` của mình nếu
không muốn chia sẻ project/đăng nhập của bạn).

## Lưu ý về lyrics trên Suno

Lyrics gửi lên Suno phải giữ nguyên chữ tự nhiên (không thay dấu cách bằng `_`, không
làm biến dạng chữ). Bài bị Suno báo bản quyền sẽ được đánh dấu `rejected` và tool
chuyển sang bài tiếp theo.
