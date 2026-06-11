# 1. Đăng nhập Dashboard

Có 2 cách xác thực với hệ thống quản trị:

1. **Tài khoản username/password** — dành cho người dùng thao tác trên dashboard (đăng nhập 1 lần, session 7 ngày).
2. **API key (Bearer token)** — dành cho kỹ thuật/tích hợp, dùng `HERMES_MGMT_API_KEY`.

## 1.1. Lấy API key lần đầu

Khi cài đặt xong VPS, màn hình cài đặt in ra:

```
Dashboard:      https://<domain>/
Management API: https://<domain>/api
MGMT_API_KEY:   xxxxxxxx... (64 ký tự)
```

**Lưu ngay `MGMT_API_KEY`** — đây là chìa khoá quản trị cao nhất. Nếu quên, SSH vào VPS lấy lại:

```bash
grep ^HERMES_MGMT_API_KEY /opt/hermes/.env
```

## 1.2. Tạo tài khoản đăng nhập (lần đầu tiên)

Hệ thống chưa có user nào sau khi cài. Tạo user đầu tiên bằng API key:

```bash
curl -X POST "$VPS/api/auth/create-user" \
  -H "Authorization: Bearer $MGMT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "MatKhauManh@2026"}'
```

> Mật khẩu được mã hoá bcrypt, không lưu dạng thô. Trên dashboard quản trị, bước này thường được thực hiện sẵn khi bàn giao cho khách.

## 1.3. Đăng nhập

**Trên trình duyệt:** mở `https://<domain>/login` → nhập username + password → bấm **Sign in**. Đăng nhập thành công sẽ tạo cookie session (HttpOnly, Secure) có hiệu lực **7 ngày**.

**Qua API:**

```bash
curl -X POST "$VPS/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "MatKhauManh@2026"}'
# → {"ok": true, "data": {"token": "...", "expires_at": "..."}}
```

### Chống dò mật khẩu

Đăng nhập sai **10 lần trong 15 phút** từ cùng một IP sẽ bị chặn tạm (HTTP 429). Bộ đếm nằm trong bộ nhớ — nếu cần gỡ chặn ngay: `systemctl restart hermes-mgmt` trên VPS.

## 1.4. Đổi mật khẩu

```bash
curl -X PUT "$VPS/api/auth/change-password" \
  -H "Authorization: Bearer $MGMT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "MatKhauCu", "new_password": "MatKhauMoi@2026"}'
```

Trên dashboard: **Cài đặt → Đổi mật khẩu**.

## 1.5. Đăng xuất / quản lý user

| Thao tác | API |
|----------|-----|
| Xem user hiện tại | `GET /api/auth/user` |
| Đăng xuất (xoá cookie) | `POST /api/auth/logout` |
| Xoá user (quay về chỉ dùng API key) | `DELETE /api/auth/user` |

## 1.6. Xử lý sự cố đăng nhập

| Triệu chứng | Nguyên nhân & cách xử lý |
|-------------|--------------------------|
| 401 "No user configured" | Chưa tạo user — làm mục 1.2 |
| 401 "Invalid username or password" | Sai thông tin; nếu quên mật khẩu, dùng API key tạo lại user (mục 1.2 ghi đè user cũ) |
| 429 Too Many Requests | Bị rate-limit — đợi 15 phút hoặc restart `hermes-mgmt` |
| Trình duyệt cảnh báo SSL | Domain chưa trỏ DNS về VPS → Caddy dùng chứng chỉ tự ký. Trỏ DNS rồi `systemctl restart caddy` |
