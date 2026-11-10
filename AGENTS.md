# Quy tắc bảo trì cho AI

## Nhật ký thay đổi bắt buộc

- Mọi lần sửa mã nguồn, cấu hình, migration, test hoặc workflow phải nối thêm một mục vào `AI_CHANGELOG.md` trong cùng lượt sửa.
- Mỗi mục phải ghi ngày giờ, yêu cầu, nguyên nhân, file đã đổi, hành vi trước và sau, kiểm thử đã chạy, kết quả và việc còn tồn đọng.
- Không ghi API key, cookie, token đăng nhập hoặc dữ liệu bí mật vào nhật ký.
- Nếu chỉ đọc/kiểm tra và không thay đổi file thì không cần thêm mục.

## An toàn workflow Suno

- Không làm biến dạng lyrics để né bộ lọc bản quyền của Suno.
- Lyrics gửi lên Suno phải là Unicode NFC, dùng khoảng trắng tự nhiên, không dùng dấu `_` thay khoảng trắng và không có dòng tên bài/ca sĩ.
- Khi Suno báo lyrics/audio có bản quyền, đánh dấu bài `rejected`, giữ dữ liệu và chuyển sang bài tiếp theo; chỉ chạy lại khi người dùng có lyrics/audio tự sáng tác hoặc được phép sử dụng.
- Không restart backend khi đang có job; kiểm tra `/api/tasks/active` trước.
