# Cover Music by Lucas Studio — Hướng dẫn sử dụng

Hướng dẫn **dùng tool hằng ngày**. Cách tải, cài đặt, cập nhật và sửa lỗi cài đặt: xem
**[README.md](README.md)**.

> Tool này chỉ làm phần âm thanh. Muốn làm cả video + nháp YouTube, dùng bản đầy đủ
> **[Music-Cover](https://github.com/lucasstudio1228/Music-Cover)** (có hướng dẫn riêng trong repo đó).

## Mở và đóng tool

- **Mở:** bấm đúp **Cover Music by Lucas Studio** ngoài Desktop (hoặc `CoverMusic.exe` trong thư
  mục tool). Tool hiện trong cửa sổ riêng, chờ vài giây cho tool khởi động.
  Mac: bấm đúp `start_mac.command` → trình duyệt mở http://localhost:8200.
- **Đóng:** bấm dấu ✕ của cửa sổ = **tắt tool**. Nếu đang có việc chạy (Suno, mix…), tool hỏi
  lại — chọn **Cancel** để giữ tool chạy tiếp.
- Nút **⬇** tải file vẫn hiện hộp chọn nơi lưu; link ngoài (nhaccuatui…) mở bằng trình duyệt.
- Vẫn dùng được `start.bat` như cũ. Nếu đã mở bằng `start.bat` rồi bấm thêm file `.exe`, cửa
  sổ chỉ hiện giao diện (tên cửa sổ có chữ *"đã mở sẵn"*); đóng cửa sổ không tắt `start.bat`.

**Lần đầu:** bấm **⚙ Settings** → đăng nhập **Suno** và **nhaccuatui**, dán **API Key** nếu
dùng AI. (Chỉ dùng bài cover có sẵn thì không cần đăng nhập.)

## Tạo project

Bấm **+ New Project**, chọn 1 trong 4 kiểu:

| Kiểu | Dùng khi | Tool làm gì |
|---|---|---|
| ✍️ **Tạo 1 project thủ công (trống)** | Muốn tự làm từng bước | Chỉ tạo project trống, bạn tự bấm ① → ④ |
| 📂 **Project từ bài cover có sẵn** | Đã có file cover (làm trên Suno hoặc nơi khác) | Tạo project rồi mở ngay ô **kéo thả bài cover** (xem bên dưới). Không tải bài gốc, không dùng Suno |
| 🎼 **Mix Set** | Nhiều bài ghép thành 1 bản mix dài | Tạo 1 project chứa cả danh sách bài |
| 🎵 **Track lẻ** | Mỗi bài là 1 project riêng | Mỗi dòng trong danh sách thành 1 project |

### Mix Set / Track lẻ: chạy tự động hay tự chạy?

Khi chọn Mix Set hoặc Track lẻ, form có 2 lựa chọn:

- **✍️ Chỉ tạo, tôi tự chạy** (mặc định) — tool chỉ tạo sẵn project và danh sách bài. **Chưa
  tải bài, chưa dùng Suno, chưa tốn credit.** Sau đó bạn:
  - tự bấm từng bước trong project (① Tải nhạc gốc → ② Suno Cover…), hoặc
  - bấm **🎧 Chạy tự động** ở thanh dưới cùng khi muốn tool làm tiếp.
- **🎧 Tạo & chạy tự động** — tool làm hết ngay: tải bài gốc → tempo 4.5× → Suno Cover →
  master (Mix Set thêm bước ghép mix). Cần tick ô xác nhận dùng credit Suno.

### File danh sách bài

Kéo thả file vào ô **📄** trong form (hoặc bấm vào ô để chọn file). Tối đa 50 bài.

| Loại file | Cách ghi |
|---|---|
| **Word** `.docx`, `.doc` / **chữ** `.txt` | Mỗi dòng 1 bài: **Tên bài - Ca sĩ** (hoặc **Ca sĩ - Tên bài**) |
| **Excel** `.xlsx`, `.xls` / `.csv` | Mỗi **hàng** 1 bài. Cột A = Tên bài, cột B = Ca sĩ — hoặc chỉ 1 cột ghi sẵn «Tên bài - Ca sĩ». Hàng tiêu đề (STT / Tên bài / Ca sĩ…) và cột số thứ tự tự được bỏ qua. Tool đọc **trang tính đầu tiên** |

Ví dụ Excel:

| STT | Tên bài | Ca sĩ |
|---|---|---|
| 1 | Nơi này có anh | Sơn Tùng M-TP |
| 2 | Em gái mưa | Hương Tràm |

File Office đời cũ (`.xls`, `.doc`) đôi khi không đọc được — tool sẽ báo; khi đó mở file bằng
Excel/Word → **Lưu thành** → chọn `.xlsx` / `.docx` rồi chọn lại.

Project nằm trong thư mục `media\Mix Set\` hoặc `media\Track lẻ\`.

**Nút 🎧 Chạy tự động** (thanh dưới cùng): cho project đang mở chạy tiếp tự động. Khi đang chạy
có thêm **⏸ Tạm dừng**, **▶ Tiếp tục** và **⏹ Dừng lại** (dừng tất cả; phần đã làm được giữ
lại). Bấm vào một việc đang chạy để nhảy tới đúng project / tab của việc đó.

## Nhập bài cover có sẵn (kéo thả)

Ở tab **② Suno Cover**, ô trên cùng **📂 Kéo thả bài cover có sẵn**:

1. **Kéo các file nhạc cover** (`.wav`, `.mp3`, `.flac`, `.m4a`) thả vào ô — kéo nhiều bài
   cùng lúc được. Có thể kéo kèm **file lời** `.txt` / `.lrc`.
   - Đặt tên file **Tên bài - Ca sĩ** (vd `Em gái mưa - Hương Tràm.wav`) → tool tự điền tên bài,
     ca sĩ.
   - Các bản cùng bài như `Em gái mưa - Hương Tràm (1).wav`, `(2).wav` được **gom thành 1 bài**.
   - File lời **cùng tên** với bài (vd `Em gái mưa - Hương Tràm.txt`) tự gắn vào đúng bài.
2. Mỗi bài hiện 1 dòng: kiểm tra **tên bài, ca sĩ**; mở **📝 Lời bài hát** để dán lời (hoặc kéo
   file lời thả thẳng vào dòng của bài đó). Lời chỉ gồm câu hát, không cần tên bài / ca sĩ.
3. Để nguyên 2 ô tick:
   - **Master + đồng bộ âm lượng** — mọi bài được làm đẹp và đưa về **cùng một mức âm lượng**
     (theo kiểu master đang chọn ở ô 🎚 Master, mặc định *natural -14 LUFS*).
   - **AI căn lời ngay** — bài có lời được AI dò thời gian từng câu.
4. Bấm **⬆ Nhập N bài**. Mỗi dòng báo ✅ khi xong; tiến độ master xem ở danh sách bên phải.
5. Sang tab **③ Lời bài hát** → chọn bài → nút **⬇ .srt** để tải **file lời của bài đó** cho
   CapCut. Project Mix Set: sang **④ Mix** ghép các bài rồi tải lời cả bản mix.

(Ô **⬆ Nhập bài cover tôi đã làm sẵn** phía dưới vẫn dùng được khi muốn nhập 1 bài kèm cả file
bài gốc.)

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
- **🤖 AI căn lời**: AI nghe bản cover và dò thời gian từng câu.
- **AI căn sai?** Bấm ▶ nghe câu đó, bấm **⏱ đây** đúng lúc câu bắt đầu (hoặc gõ lại giờ), rồi
  **Lưu mốc thời gian**. Cả bài lệch đều thì dùng nút **-0.5s / -0.2s / +0.2s / +0.5s**.
- Mỗi bài có nút **⬇ .lrc** và **⬇ .srt** (lời riêng bài đó, khớp thời gian bản cover).
- **⬇ Lời cho CapCut (.srt)**: lời cả bản **Mix** mới nhất (project Mix Set).
- **📂 Mở thư mục nghe audio**: mở thư mục nhạc đã xong trên máy để nghe thử.

### ④ Mix (chỉ project Mix Set)
Cân âm lượng và ghép các bài thành 1 bản mix dài. Các bản đã mix hiện trong **Mix History**,
mỗi bản có nút **Lời .srt (CapCut)**.

## File nhạc nằm ở đâu?

- Bản cover đã master: thư mục `ban_cover\mastered\` trong thư mục project.
- Mỗi project Mix Set có thư mục **`media\...\<tên project>\nhac_mix\`** chứa:
  - **`<tên project>.wav`** — đúng 1 file nhạc đã mix (mix lại thì được thay bằng bản mới nhất).
  - **`<tên project>.srt`** — **lời cho CapCut**: chỉ có lời, mỗi câu hiện đúng lúc được hát.
- Trong **CapCut**: kéo file `.wav` vào, rồi kéo file `.srt` vào (hoặc **Văn bản → Nhập phụ đề /
  Captions → Import**) — lời tự khớp theo nhạc, chỉ cần chọn font/màu.
- Câu dài trong file `.srt` được tự **xuống dòng, mỗi dòng tối đa 8 từ** (chia đều, vd 10 từ →
  5 + 5). File `.srt` không lưu được khoảng cách dòng, nên trong CapCut chọn phụ đề → **Văn bản →
  Cơ bản → Khoảng cách dòng (Line spacing)** → tăng thêm cho thoáng (vd +1), rồi bấm **Áp dụng
  cho tất cả** để mọi câu giống nhau.
- Bản mix gốc kèm thông tin chi tiết: `outputs\<số project>\<số mix>\`.

## Khi có trục trặc

| Hiện tượng | Cách xử lý |
|---|---|
| Cửa sổ báo "Không mở được tool" | Chụp màn hình gửi người hỗ trợ (kèm `data\logs\app_server.log`) |
| Lỡ đóng cửa sổ khi đang chạy | Mở lại tool, vào project, bấm **🎧 Chạy tự động** để làm tiếp |
| Suno đòi đăng nhập lại | **⚙ Settings → Đăng nhập Suno** |
| File danh sách báo "Dòng sai định dạng" | Mỗi dòng / hàng phải có dạng **Tên bài - Ca sĩ** (có dấu cách hai bên dấu `-`), hoặc 2 cột Tên bài / Ca sĩ trong Excel |
| Kéo thả bài cover mà file lời không tự gắn | Đặt tên file lời trùng tên file nhạc, hoặc kéo file lời thả thẳng vào dòng của bài |
| Nút **⬇ .srt** báo "chưa có lời đã căn" | Dán lời cho bài rồi bấm **🤖 AI căn lời bài này** ở tab ③ |
| Muốn lấy bản mới nhất | Đóng tool → bấm đúp `update.bat` (xem README) |
