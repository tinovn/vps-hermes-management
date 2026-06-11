# 3. Kết nối Zalo cá nhân

Cho phép bot trả lời tin nhắn qua một **tài khoản Zalo cá nhân**. Có 2 khái niệm **khác nhau, dùng 2 tài khoản Zalo khác nhau**:

| Khái niệm | Là gì | Cách thiết lập |
|-----------|-------|----------------|
| **Bot** | Tài khoản Zalo mà bot "ngồi" trong đó để nhắn tin với khách | Quét QR đăng nhập |
| **Owner (sếp)** | Tài khoản Zalo **cá nhân của chủ doanh nghiệp** — người được phép ra lệnh quản trị cho bot | Nhập số điện thoại Zalo của sếp |

> ⚠️ **Không dùng chung 1 tài khoản** cho cả bot và owner. Bot là tài khoản riêng (nên dùng SIM riêng); owner là Zalo thật của sếp.

## 3.1. Kết nối bot (quét QR)

### Trên dashboard

1. Vào mục **Zalo** → bấm **Kết nối Zalo**.
2. Mã QR hiện ra trên trang (đợi 1–2 giây nếu chưa hiện).
3. Mở app Zalo trên điện thoại **của tài khoản bot** → biểu tượng QR (góc trên) → quét mã.
4. Xác nhận đăng nhập trên điện thoại. Trạng thái trên dashboard chuyển: `pending` → `scanned` → **`connected`** ✅.

Phiên đăng nhập **lưu trên đĩa VPS** — bot không bị đăng xuất khi restart dịch vụ.

### API tương ứng

| Bước | API | Ghi chú |
|------|-----|---------|
| Bắt đầu QR login | `POST /api/zalo/connect` | Trả về ngay; QR sinh bất đồng bộ |
| Lấy ảnh QR | `GET /api/zalo/qr` | Trả PNG thô — gắn thẳng `<img src="/api/zalo/qr">`. 404 = QR chưa sẵn sàng, thử lại sau 1–2s |
| Theo dõi trạng thái | `GET /api/zalo/status` | `data.status` ∈ `disconnected / pending / scanned / connected / error` |
| Ngắt kết nối | `POST /api/zalo/disconnect` | Đăng xuất + xoá phiên |

`GET /api/zalo/status` còn trả:
- `bot_uid` — UID tài khoản bot đã đăng nhập (không phải owner)
- `owner_set` — đã cài owner hay chưa (xem 3.2)
- `sidecar` — tiến trình Zalo nội bộ có đang chạy không

## 3.2. Cài đặt Owner (sếp) — BẮT BUỘC

Bot **chỉ hoạt động đầy đủ sau khi cài owner**: plugin Zalo chỉ được gateway kích hoạt khi đã biết owner UID. Owner là người duy nhất bot nhận lệnh quản trị qua tin nhắn.

### Trên dashboard

1. Sau khi bot `connected`, mục Zalo hiện ô **"Số Zalo của sếp"**.
2. Nhập **số điện thoại đăng ký Zalo của sếp** (ví dụ `0901234567`) → bấm **Lưu**.
3. Hệ thống tự động: tra số → tìm UID → lưu cấu hình → bật plugin → restart gateway. Sau ~10 giây bot sẵn sàng.

### API tương ứng

```bash
# Cài owner theo số điện thoại (khuyên dùng)
curl -X POST "$VPS/api/zalo/set-owner" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"phone": "0901234567"}'

# Hoặc cài trực tiếp bằng UID (nâng cao)
curl -X POST "$VPS/api/zalo/set-owner" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"uid": "123456789"}'

# Xem owner hiện tại
curl -H "Authorization: Bearer $MGMT_KEY" "$VPS/api/zalo/owner"
# → {"ok": true, "data": {"owner_uid": "...", "owner_set": true}}
```

**Điều kiện:** bot phải đang `connected` thì mới tra được số điện thoại (lỗi 409 nếu chưa quét QR).

## 3.3. Quy trình chuẩn (tóm tắt)

```
1. Kết nối Zalo (quét QR bằng TÀI KHOẢN BOT)     → status = connected
2. Nhập số Zalo của SẾP (set-owner)              → owner_set = true
3. Hệ thống tự bật plugin + restart gateway       → bot bắt đầu nhận tin
4. Sếp nhắn thử cho bot từ Zalo cá nhân           → bot phản hồi = OK
```

## 3.4. Đổi bot hoặc đổi sếp

- **Đổi tài khoản bot:** bấm **Ngắt kết nối** (`POST /api/zalo/disconnect`) → quét QR lại bằng tài khoản mới → cài lại owner nếu cần.
- **Đổi sếp:** chỉ cần gọi lại `set-owner` với số mới — ghi đè owner cũ, không cần quét QR lại.

## 3.5. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|-------------|-----------|
| 503 "Zalo sidecar chưa sẵn sàng" | Gateway đang khởi động hoặc plugin chưa cài — đợi vài giây rồi thử lại |
| QR trả 404 mãi | Gọi lại `POST /api/zalo/connect` rồi lấy QR lại |
| 409 khi set-owner | Bot chưa đăng nhập — quét QR trước |
| 404 "Không tìm thấy tài khoản Zalo cho số..." | Số chưa đăng ký Zalo hoặc chặn tìm kiếm qua SĐT — kiểm tra cài đặt quyền riêng tư Zalo của sếp |
| Bot connected nhưng không trả lời tin nhắn | Kiểm tra `owner_set` = true chưa; kiểm tra đã cấu hình Model AI chưa (mục 2) |
| Bot bị Zalo đăng xuất | Tài khoản bot bị Zalo nghi spam — đăng nhập lại bằng QR; xem thêm quy tắc an toàn tài khoản (mục 5) |
