# MasterAudio — Cover Audio Tool

Tool làm **phần âm thanh** cho nhạc cover: từ bài gốc → bản cover Suno đã master → file mix
`.wav` kèm lời `.srt` cho CapCut. Không có phần dựng video / đăng YouTube.

| Bước | Tab trong tool | Việc làm |
|---|---|---|
| ① | **Tải nhạc gốc** | Tìm và tải bài gốc + lời (nhaccuatui / YouTube Music) |
| ② | **Suno Cover** | Tạo bản cover bằng Suno, tải về và master |
| ③ | **Lời bài hát** | Kiểm tra / căn lời theo nhạc |
| ④ | **Mix** | Cân âm lượng, ghép các bài thành 1 bản mix dài (chỉ project Mix Set) |

Cách dùng chi tiết từng bước: xem **[HUONG_DAN.md](HUONG_DAN.md)**.

---

## 1. Tải tool về

Trên trang GitHub bấm **Code → Download ZIP** rồi giải nén vào nơi bạn muốn để tool
(ví dụ `D:\Tools\Cover Audio Tool`). Người biết git có thể dùng
`git clone https://github.com/lucasstudio1228/MasterAudio.git`.

## 2. Cài đặt trên Windows (chỉ làm 1 lần)

**Máy cần có sẵn:**
- **Python 3.11** — https://www.python.org/downloads/ (khi cài nhớ tick **"Add Python to PATH"**).
- **ffmpeg** — mở PowerShell gõ: `winget install Gyan.FFmpeg`
- **Google Chrome** — dùng để mở Suno.
- Windows 10/11 (đã có sẵn Edge WebView2 để hiện cửa sổ tool).

**Cài:** bấm đúp **`install.bat`** trong thư mục tool, chờ chữ **CAI DAT XONG** (lần đầu
khoảng 10–20 phút). Cài xong sẽ có:
- File **`CoverAudioTool.exe`** trong thư mục tool.
- Lối tắt **Cover Audio Tool** (icon tai nghe tím) ngoài **Desktop**.

## 3. Mở tool

Bấm đúp **Cover Audio Tool** ngoài Desktop → tool mở trong **cửa sổ riêng** (không cần
trình duyệt, không có cửa sổ đen).

- **Đóng cửa sổ = tắt tool.** Nếu đang có việc chạy (Suno, mix…), tool hỏi lại trước khi đóng.
- Cách cũ vẫn dùng được: bấm đúp **`start.bat`** → trình duyệt mở **http://localhost:8200**.
- Tool chạy cổng **8200**, nên dùng song song được với tool Cover nhạc đầy đủ (cổng 8100).

**Lần đầu mở tool:** bấm **⚙ Settings** →
- **Đăng nhập Suno** và **nhaccuatui** (mỗi người tự đăng nhập tài khoản của mình).
- Dán **API Key** của mình nếu dùng AI (địa chỉ API và model đã điền sẵn).

## 4. Cập nhật lên bản mới (không cần xoá bản cũ, không mất dữ liệu)

Project, nhạc, setting, đăng nhập Suno/nhaccuatui và API key **được giữ nguyên**.

1. Chờ các việc đang chạy xong, rồi **đóng tool** (đóng cửa sổ tool hoặc cửa sổ `start.bat`).
   Tool đang mở thì file cập nhật sẽ dừng và nhắc bạn đóng.
2. Bấm đúp **`update.bat`** trong thư mục tool → chờ chữ **CAP NHAT XONG**.
3. Mở lại tool bằng lối tắt **Cover Audio Tool** ngoài Desktop.

`update.bat` tự làm: tải bản mới từ GitHub → chép đè phần mã → xoá file cũ không còn dùng →
cài thêm thư viện nếu bản mới cần → tạo lại `CoverAudioTool.exe` và lối tắt Desktop. Mã cũ được
cất trong thư mục `_ban_cu_truoc_cap_nhat\` (xoá đi cũng được khi tool chạy ổn).

**Bản cũ chưa có `update.bat`?** Mở link
https://raw.githubusercontent.com/lucasstudio1228/MasterAudio/main/update.bat → chuột phải →
**Lưu thành (Save as)** → lưu vào **thư mục tool** (cạnh `start.bat`), tên `update.bat`.
Sau đó làm như các bước trên.

## 5. Lỗi thường gặp trên Windows

| Hiện tượng | Cách xử lý |
|---|---|
| Không thấy lối tắt / mất file `CoverAudioTool.exe` | Chạy lại `update.bat` (tự tạo lại file `.exe` và lối tắt) |
| Lần đầu mở, tool hỏi cài "thư viện cửa sổ ứng dụng" | Bấm **OK** (cần mạng, ~1 phút) |
| Cửa sổ tool báo "Không mở được tool" | Chụp màn hình gửi người hỗ trợ (kèm file `data\logs\app_server.log`) |
| Cửa sổ tool không hiện / màn hình trắng | Dùng tạm `start.bat` (mở bằng trình duyệt) |
| `update.bat` báo "Tool dang chay" | Đóng tool rồi bấm lại `update.bat` |
| "Khong tim thay ffmpeg" | `winget install Gyan.FFmpeg`, đóng rồi mở lại tool |

---

## Cài đặt trên Mac (macOS) — chip M và chip Intel

Trên Mac tool mở bằng **trình duyệt** (chưa có cửa sổ ứng dụng như Windows).

**Xem máy mình chip gì:** bấm **logo quả táo** (góc trên bên trái) → **Giới thiệu về máy Mac
này (About This Mac)**:
- **"Chip: Apple M1/M2/M3/M4…"** → Mac chip M.
- **"Bộ xử lý (Processor): … Intel Core i5/i7/i9…"** → Mac chip Intel (xem thêm mục bên dưới).

Không cần chọn gì: **`install_mac.command` tự nhận ra loại chip** và cài đúng bộ thư viện.

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
7. Cập nhật: đóng cửa sổ `start_mac.command`, bấm đúp **`update_mac.command`**. Bản cũ chưa có
   file này thì tải ở
   https://raw.githubusercontent.com/lucasstudio1228/MasterAudio/main/update_mac.command
   (**Lưu thành** vào thư mục tool) rồi chạy lệnh `chmod +x` như bước 5.

Mac chip M chạy được nhưng tách giọng (Demucs) chậm hơn máy Windows có card NVIDIA.

### Mac chip Intel

- **Yêu cầu:** macOS 12 (Monterey) trở lên, RAM từ 8 GB (khuyên 16 GB), trống ổ đĩa ~10 GB.
- Cài giống hệt các bước trên. Khi chạy `install_mac.command`, cửa sổ sẽ ghi
  *"May Mac chip INTEL (x86_64)"* — tool tự dùng **PyTorch 2.2.2** (bản cuối cùng còn hỗ trợ
  Mac Intel) và các thư viện trong `constraints-mac-intel.txt`. Lần cài đầu mất **10–20 phút**.
- Cài xong, cửa sổ phải hiện dòng **`OK: torch 2.2.2 - numpy 1.26.4`**. Nếu thấy
  *[CANH BAO]* / *[LOI]* thì chụp màn hình gửi người hỗ trợ.
- **Tốc độ:** Mac Intel chỉ chạy AI bằng CPU nên bước **③ Lời bài hát** (AI nghe lời) và tách
  giọng **chậm hơn nhiều** (có thể vài phút mỗi bài). ①, ②, ④ chạy bình thường. Nên cắm sạc
  và để máy không ngủ khi đang chạy.
- **macOS cũ (Homebrew báo không hỗ trợ):**
  - Python: tải **Python 3.11 "macOS 64-bit universal2 installer"** ở
    https://www.python.org/downloads/macos/ rồi cài như phần mềm bình thường.
  - ffmpeg: tải file **ffmpeg** ở https://evermeet.cx/ffmpeg/ , giải nén, rồi mở Terminal gõ:
    ```bash
    sudo mkdir -p /usr/local/bin && sudo cp ~/Downloads/ffmpeg /usr/local/bin/ && sudo chmod +x /usr/local/bin/ffmpeg
    ```
    (Lệnh `sudo` sẽ hỏi mật khẩu máy Mac — bạn tự gõ.)
  - Sau đó bấm đúp `install_mac.command` như bình thường.

### Lỗi thường gặp trên Mac

| Hiện tượng | Cách xử lý |
|---|---|
| Bấm đúp file `.command` mở ra trình soạn thảo chữ | Chuột phải → **Open With (Mở bằng)** → **Terminal** |
| "Không tìm thấy Python 3.11" | Cài lại Python 3.11 (Homebrew hoặc python.org), đóng Terminal, chạy lại |
| "Không tìm thấy ffmpeg" | `brew install ffmpeg` (hoặc cách thủ công ở trên) |
| Trình duyệt không tự mở | Tự mở Chrome/Safari, vào http://localhost:8200 |
| Muốn cài lại từ đầu | Xoá thư mục ẩn `.venv` trong thư mục tool (Cmd + Shift + . để hiện file ẩn), chạy lại `install_mac.command` |

---

## Dữ liệu & chia sẻ

**Có sẵn trong bản tải về:**
- `data\master_config.json` — thông số master (EQ, compressor, LUFS…).
- `data\suno_workflow.json` — chế độ chạy Suno.
- Các thông số Suno / tempo / thể loại nằm sẵn trong mã nguồn, không cần chỉnh.
- `data\app.db` (project, setting) tự được tạo khi mở tool lần đầu; địa chỉ API + model AI đã điền sẵn.

**Không** kèm theo: mật khẩu, cookie đăng nhập Suno/nhaccuatui, API key — mỗi người tự đăng
nhập / nhập key của mình.

**Khi chia sẻ thư mục tool cho người khác**, bỏ ra: `.venv`, `.browser_profile_*`, `media`,
`outputs`, `stems`, `data\app.db`, `data\webview` và file `CoverAudioTool.exe` (máy kia tự tạo
lại khi chạy `install.bat`). Cách gọn nhất là gửi link GitHub này.

## Lưu ý về lyrics trên Suno

Lyrics gửi lên Suno giữ nguyên chữ tự nhiên (không thay dấu cách bằng `_`, không làm biến dạng
chữ, không có dòng tên bài / ca sĩ). Bài bị Suno báo bản quyền sẽ được đánh dấu `rejected` và
tool chuyển sang bài tiếp theo.
