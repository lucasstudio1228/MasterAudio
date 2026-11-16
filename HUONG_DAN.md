# Cover Audio Tool — Hướng dẫn sử dụng

Hướng dẫn **dùng tool hằng ngày**. Cách tải, cài đặt, cập nhật và sửa lỗi cài đặt: xem
**[README.md](README.md)**.

## Mở và đóng tool

- **Mở:** bấm đúp **Cover Audio Tool** ngoài Desktop (hoặc `CoverAudioTool.exe` trong thư mục
  tool). Tool hiện trong cửa sổ riêng, chờ vài giây cho tool khởi động.
  Mac: bấm đúp `start_mac.command` → trình duyệt mở http://localhost:8200.
- **Đóng:** bấm dấu ✕ của cửa sổ = **tắt tool**. Nếu đang có việc chạy (Suno, mix…), tool hỏi
  lại — chọn **Cancel** để giữ tool chạy tiếp.
- Nút **⬇** tải file vẫn hiện hộp chọn nơi lưu; link ngoài (nhaccuatui…) mở bằng trình duyệt.
- Vẫn dùng được `start.bat` như cũ. Nếu đã mở bằng `start.bat` rồi bấm thêm file `.exe`, cửa
  sổ chỉ hiện giao diện (tên cửa sổ có chữ *"đã mở sẵn"*); đóng cửa sổ không tắt `start.bat`.

**Lần đầu:** bấm **⚙ Settings** → đăng nhập **Suno** và **nhaccuatui**, dán **API Key** nếu
dùng AI.

## Tạo project

Bấm **+ New Project**, chọn 1 trong 3 kiểu:

| Kiểu | Dùng khi | Tool làm gì |
|---|---|---|
| ✍️ **Tạo 1 project thủ công** | Muốn tự làm từng bước | Chỉ tạo project, bạn tự bấm ① → ④ |
| 🎼 **Mix Set** | Nhiều bài ghép thành 1 bản mix dài | Mỗi bài: tải gốc → tempo 4.5× → Suno Cover → master, cuối cùng ghép mix |
| 🎵 **Track lẻ** | Mỗi bài là 1 project riêng | Mỗi bài: tải gốc → tempo 4.5× → Suno Cover → master. Bài lỗi thì bỏ qua, làm bài sau |

Mix Set và Track lẻ nhận **file danh sách bài** (`.txt` hoặc `.docx`), mỗi dòng 1 bài dạng
**Tên bài - Ca sĩ** (hoặc **Ca sĩ - Tên bài**). Project nằm trong thư mục `media\Mix Set\` hoặc
`media\Track lẻ\`.

**Nút 🎧 Chạy tự động** (thanh dưới cùng): cho project đang mở chạy tiếp tự động — tải bài →
Suno Cover → master (Mix Set thêm bước ghép mix). Dùng khi project thủ công đã sẵn sàng hoặc
khi muốn chạy lại phần còn dở.

Khi đang chạy, thanh dưới cùng có thêm **⏸ Tạm dừng**, **▶ Tiếp tục** và **⏹ Dừng lại**
(dừng tất cả; phần đã làm được giữ lại để Tiếp tục sau). Bấm vào một việc đang chạy để nhảy tới
đúng project / tab của việc đó.

## 4 bước trong mỗi project

### ① Tải nhạc gốc
Tìm và tải bài gốc + lời (nhaccuatui / YouTube Music). Tool đặt sẵn tempo **4.5×** cho bản dùng
để cover.

### ② Suno Cover — quy trình chuẩn (bán thủ công)

1. **Tool:** chuẩn hoá lời + soạn Styles, upload bản tempo lên Suno, mở **Suno Editor**.
2. **Bạn:** trong Editor chỉnh **Tempo** (tool gợi ý số, vd 32) → bấm **Save as New Song**.
3. **Tool:** tự nhận bài vừa lưu, mở trang bài → **⋯ → Remix → Cover**. Nếu Suno còn đang xử lý,
   tool tự chờ rồi bấm lại (tối đa 3 lần, ~2 phút).
4. **Tool:** điền form Cover — lời, Styles, Max Mode, thanh trượt (Weirdness 10, Style
   Influence 75, Audio Influence 50).
5. **Bạn:** xem lại form trong cửa sổ Suno rồi **tự bấm Create** (tool không bao giờ tự bấm).
6. **Tool:** nhận 2 bản Suno tạo ra, tải về, master, chuyển sang bài tiếp theo.

**Bỏ bớt bản không dùng (thùng rác):**
- Mỗi bản cover có nút **🗑 Xoá bản này** → bản đó vào thùng rác, không còn dùng để master /
  ghép mix. **File trên máy không bị xoá.**
- Dưới mỗi bài có **🗑 Thùng rác (N)**: mở ra để nghe thử và **♻ Khôi phục** từng bản (hoặc
  **♻ Khôi phục tất cả**).

**Bài bị Suno báo bản quyền:** tool đánh dấu bài `rejected`, giữ dữ liệu và chuyển sang bài
sau. Tool **không** sửa méo lời để né bộ lọc — lời gửi lên Suno luôn là chữ tự nhiên, không có
dòng tên bài / ca sĩ.

### ③ Lời bài hát
- Kiểm tra / căn lời theo bản cover (AI nghe lời).
- Nút **📂 Mở thư mục nghe audio**: mở thư mục nhạc đã xong trên máy để nghe thử.
- Nút **⬇ Lời cho CapCut (.srt)**: tải file lời để đưa vào CapCut.

### ④ Mix (chỉ project Mix Set)
Cân âm lượng và ghép các bài thành 1 bản mix dài. Các bản đã mix hiện trong **Mix History**,
mỗi bản có nút **Lời .srt (CapCut)**.

## File nhạc nằm ở đâu?

- Mỗi project có thư mục **`media\...\<tên project>\nhac_mix\`** chứa:
  - **`<tên project>.wav`** — đúng 1 file nhạc đã mix (mix lại thì được thay bằng bản mới nhất).
  - **`<tên project>.srt`** — **lời cho CapCut**: chỉ có lời, mỗi câu hiện đúng lúc được hát.
- Trong **CapCut**: kéo file `.wav` vào, rồi kéo file `.srt` vào (hoặc **Văn bản → Nhập phụ đề /
  Captions → Import**) — lời tự khớp theo nhạc, chỉ cần chọn font/màu.
- Bản mix gốc kèm thông tin chi tiết: `outputs\<số project>\<số mix>\`.

## Khi có trục trặc

| Hiện tượng | Cách xử lý |
|---|---|
| Cửa sổ báo "Không mở được tool" | Chụp màn hình gửi người hỗ trợ (kèm `data\logs\app_server.log`) |
| Lỡ đóng cửa sổ khi đang chạy | Mở lại tool, vào project, bấm **🎧 Chạy tự động** để làm tiếp |
| Suno đòi đăng nhập lại | **⚙ Settings → Đăng nhập Suno** |
| Muốn lấy bản mới nhất | Đóng tool → bấm đúp `update.bat` (xem README) |
