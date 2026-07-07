# Hermes Agent VPS — Bộ tài liệu hướng dẫn sử dụng

Bộ tài liệu thao tác trên **Dashboard quản trị** (và Management API tương ứng) để quản lý Hermes Agent chạy trên VPS. Mỗi nút bấm trên dashboard tương ứng với một API endpoint — tài liệu mô tả cả hai để vừa phục vụ người dùng phổ thông, vừa phục vụ tích hợp tự động.

## Mục lục

| # | Tài liệu | Nội dung |
|---|----------|----------|
| 1 | [Đăng nhập Dashboard](01-dang-nhap-dashboard.md) | Tạo tài khoản, đăng nhập, đổi mật khẩu, API key |
| 2 | [Cấu hình Model AI](02-cau-hinh-model-ai.md) | Chọn provider + model, nhập API key, **ChatGPT OAuth (Codex)** |
| 3 | [Kết nối Zalo cá nhân](03-ket-noi-zalo-ca-nhan.md) | Quét QR kết nối bot, cài đặt **Zalo Owner (sếp)** |
| 4 | [Cấu hình kênh chat](04-cau-hinh-kenh-chat.md) | Telegram, Discord, Slack, Signal, WhatsApp |
| 5 | [Vai trò & quy tắc bot](05-vai-tro-quy-tac-bot.md) | Chọn vai trò (CSKH, sales, spa...), quy tắc an toàn |
| 6 | [Vận hành & giám sát](06-van-hanh-giam-sat.md) | Trạng thái, log, restart, nâng cấp, cron, OpenViking |
| 7 | [Checklist onboarding khách mới](07-checklist-onboarding.md) | Quy trình triển khai từ A→Z cho khách hàng mới |
| 8 | [Kết nối WhatsApp](08-ket-noi-whatsapp.md) | Quét QR kết nối bot WhatsApp (Baileys), chọn mode + bật, **API tích hợp dashboard** |

## Kiến trúc hệ thống (tóm tắt)

```
Internet :443 (HTTPS)
       │
       ▼
   Caddy (reverse proxy + SSL tự động)
       │
  ┌────┴──────────────┬──────────────────────┐
  ▼                   ▼                      ▼
hermes-gateway    hermes-dashboard      hermes-mgmt
(bot xử lý tin    (Web UI chat          (Management API :9997
 nhắn các kênh)    :9119)                — dashboard quản trị gọi vào đây)
                                             │
                                        Zalo sidecar (Node, :3838 nội bộ)
```

- **Dashboard chat** (`https://<domain>/`): giao diện chat trực tiếp với agent.
- **Dashboard quản trị**: giao diện quản lý (kết nối Zalo, chọn model, vai trò...) — gọi Management API tại `https://<domain>/api/...`.
- Mọi thay đổi cấu hình qua dashboard **tự động restart** service liên quan, không cần SSH.

## Quy ước trong tài liệu

- `$VPS` = `https://<domain-cua-ban>` (ví dụ `https://bot.tino.vn`)
- `$MGMT_KEY` = giá trị `HERMES_MGMT_API_KEY` được in ra khi cài đặt (lưu trong `/opt/hermes/.env`)
- Mọi API trả về định dạng: `{"ok": true|false, "data": {...}, "error": null|"..."}`
- Gọi API cần header `Authorization: Bearer $MGMT_KEY` **hoặc** cookie session sau khi đăng nhập

## Tài liệu liên quan

- [Deploy Guide](../deploy-guide.md) — cài đặt VPS từ đầu (dành cho kỹ thuật)
- [Rule Policy](../hermes-agent-rule-policy.md) — chính sách quy tắc của agent
