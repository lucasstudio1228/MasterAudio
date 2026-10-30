# MasterAudio — Tempo 4.5× & Master Audio

Ứng dụng desktop Windows nhỏ gọn để xử lý audio hàng loạt, tách độc lập khỏi
workflow làm nhạc Cover/Suno.

MasterAudio cung cấp hai chức năng chính:

1. **Tăng/giảm tempo và giữ nguyên cao độ** — mặc định `4.5×`.
2. **Master audio** bằng EQ, compressor và chuẩn hóa loudness hai lượt.

Ngoài ra có chế độ kết hợp **Tempo → Master** để chạy liên tiếp chỉ bằng một nút.

> File gốc không bao giờ bị ghi đè hoặc xóa. Nếu tên kết quả đã tồn tại, tool tự
> thêm số thứ tự `(2)`, `(3)`…

## Tính năng

- Giao diện desktop bằng Tkinter, không cần mở trình duyệt hoặc chạy server.
- Chọn nhiều file cùng lúc hoặc quét toàn bộ thư mục và thư mục con.
- Tempo từ `0.2×` đến `5.0×`; mặc định `4.5×`.
- Dùng chuỗi `atempo` nhiều tầng để ffmpeg xử lý mức trên `2×` an toàn.
- Giữ nguyên cao độ khi thay đổi tốc độ.
- Master bằng EQ giảm đục/gắt, high-shelf, mono bass, compressor và loudnorm 2-pass.
- Ba preset loudness: Natural, Dynamic và Loud.
- Xuất WAV 24-bit/48 kHz, MP3 320 kbps hoặc cả hai.
- Có thanh tiến trình, nhật ký từng file và nút dừng ffmpeg an toàn.
- Hỗ trợ WAV, MP3, M4A, FLAC, AAC, OGG, WMA và AIFF.

## Yêu cầu

- Windows 10/11.
- Python 3.10 trở lên. Python cài đặt thông thường đã có Tkinter.
- [ffmpeg](https://ffmpeg.org/) hỗ trợ `loudnorm`, `atempo`, `acrossover` và
  `libmp3lame` nếu muốn xuất MP3.

Tool tìm ffmpeg theo thứ tự:

1. `ffmpeg\bin\ffmpeg.exe` nằm trong thư mục tool.
2. Lệnh `ffmpeg` trong biến môi trường `PATH` của Windows.

## Cài đặt nhanh

### Clone bằng Git

```powershell
git clone https://github.com/lucasstudio1228/MasterAudio.git
cd MasterAudio
start.bat
```

### Download ZIP

1. Mở repository và chọn **Code → Download ZIP**.
2. Giải nén ZIP.
3. Nhấp đúp `start.bat`.

Tool không yêu cầu package Python bên thứ ba. Nếu Windows báo không tìm thấy
Python, hãy cài Python từ [python.org](https://www.python.org/downloads/windows/)
và bật lựa chọn **Add Python to PATH**.

## Cài ffmpeg

### Dùng PATH

1. Tải bản `ffmpeg-release-essentials` tại
   [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/).
2. Giải nén, ví dụ vào `C:\ffmpeg`.
3. Thêm `C:\ffmpeg\bin` vào biến môi trường `PATH`.
4. Mở Terminal mới và chạy `ffmpeg -version` để kiểm tra.

### Đặt ffmpeg cạnh tool

Không muốn sửa PATH thì tạo cấu trúc:

```text
MasterAudio/
├─ ffmpeg/
│  └─ bin/
│     └─ ffmpeg.exe
├─ app.py
├─ audio_engine.py
└─ start.bat
```

## Hướng dẫn sử dụng

### 1. Chọn audio

- **Thêm file**: chọn một hoặc nhiều file audio.
- **Quét thư mục**: tìm tất cả file audio trong thư mục và thư mục con.
- **Bỏ mục chọn**: loại các dòng đang chọn; không xóa file trên ổ đĩa.
- **Xóa danh sách**: làm trống danh sách.

### 2. Chọn nơi lưu

Mặc định tool lưu vào `output` cạnh `app.py`. Có thể chọn bất kỳ folder khác.
Tool tự tạo:

```text
output/
├─ da_chinh_tempo/
└─ mastered/
```

### 3. Chọn thao tác

#### Tạo tempo

Nhập hệ số rồi bấm **Tạo tempo**.

- `4.5×`: phát nhanh gấp 4.5 lần.
- `1.0×`: giữ tốc độ gốc nhưng chuyển thành WAV 24-bit/48 kHz.
- `0.5×`: phát chậm còn một nửa.

Ví dụ: `Ten bai - tempo 4.5x.wav`.

#### Master audio

Chọn preset, định dạng rồi bấm **Master audio**.

| Preset | Mục tiêu | Phù hợp |
|---|---:|---|
| Natural | -14 LUFS | Cân bằng, YouTube, nghe lâu |
| Dynamic | -16 LUFS | Giữ dynamic, acoustic, jazz |
| Loud | -10 LUFS | Âm lượng mạnh hơn |

#### Tempo → Master

Bấm **Tempo → Master** để tạo file tempo trước, sau đó dùng chính file mới đó
làm đầu vào master. Cả hai kết quả đều được giữ lại.

### 4. Theo dõi và dừng

- Thanh tiến trình hiển thị số file đã xử lý.
- Khung **Nhật ký** hiển thị đường dẫn kết quả, LUFS và True Peak trước/sau.
- Nút **Dừng** kết thúc ffmpeg hiện tại và không chạy các file còn lại.
- File gốc và các file hoàn tất trước đó luôn được giữ nguyên.

## Chuỗi xử lý master

```text
High-pass 30 Hz
→ EQ giảm đục 300 Hz
→ EQ giảm gắt 3.5 kHz
→ High-shelf 10 kHz
→ Mono bass dưới 120 Hz
→ Compressor
→ Loudnorm 2-pass
→ WAV 24-bit/48 kHz hoặc MP3 320 kbps
```

Pass 1 đo tín hiệu sau EQ/nén. Pass 2 dùng số đo để chuẩn hóa chính xác và chừa
headroom True Peak cho bước encode.

## Cấu trúc source

```text
MasterAudio/
├─ app.py             # Giao diện Tkinter và hàng đợi
├─ audio_engine.py    # Tempo, ffmpeg, master/loudnorm 2-pass
├─ start.bat          # Điểm chạy trên Windows
├─ requirements.txt   # Không có dependency pip bắt buộc
├─ CHANGELOG.md
└─ README.md
```

## Xử lý lỗi thường gặp

### Không tìm thấy ffmpeg

Đặt `ffmpeg.exe` vào `ffmpeg\bin\ffmpeg.exe` hoặc thêm folder `bin` của ffmpeg
vào PATH rồi mở lại tool.

### Nhấp start.bat rồi cửa sổ đóng ngay

Mở PowerShell trong thư mục tool và chạy `python app.py` để xem lỗi.

### Xuất MP3 lỗi `Unknown encoder libmp3lame`

Cài bản ffmpeg có `libmp3lame`, hoặc chọn đầu ra WAV.

### File master im lặng hoặc không đo được loudness

Kiểm tra đầu vào có âm thanh thật, không phải file rỗng/hỏng, rồi thử WAV.

### Antivirus cảnh báo

Tool không tải audio lên internet và không sửa file gốc. Mọi lệnh ffmpeg chạy
cục bộ; toàn bộ source nằm trong `app.py` và `audio_engine.py`.

## Kiểm thử

Bản `1.0.0` đã được smoke-test với tín hiệu WAV qua chuỗi
`Tempo 4.5× → Master Natural -14 LUFS`. Các kiểm thử thuật toán atempo hiện có
trong repository phát triển gốc.

## An toàn dữ liệu

- Không ghi đè hoặc xóa file nguồn.
- Ghi qua file tạm và chỉ đổi tên khi ffmpeg hoàn tất.
- Tự thêm số thứ tự khi trùng tên.
- Nút Dừng terminate ffmpeg và dọn file tạm.
- Không gửi dữ liệu lên dịch vụ bên ngoài.

## Bản quyền

Copyright © 2026 Lucas Studio. Repository hiện chưa cấp giấy phép sử dụng lại
source cho bên thứ ba; mọi quyền được bảo lưu.
