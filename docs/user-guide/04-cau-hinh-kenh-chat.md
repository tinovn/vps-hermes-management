# 4. Cấu hình kênh chat

Ngoài Zalo (xem [mục 3](03-ket-noi-zalo-ca-nhan.md)), bot hỗ trợ các kênh: **Telegram, Discord, Slack, Signal, WhatsApp**. Mỗi kênh chỉ cần dán token vào dashboard — hệ thống tự lưu và restart gateway.

## 4.1. Xem trạng thái các kênh

Dashboard hiển thị danh sách kênh kèm trạng thái bật/tắt. API:

```bash
curl -H "Authorization: Bearer $MGMT_KEY" "$VPS/api/channels"
# → [{"channel": "telegram", "enabled": true, "env_var": "TELEGRAM_BOT_TOKEN", "value": "sk-****ab12"}, ...]
```

Token hiển thị luôn được che, chỉ lộ 4 ký tự cuối.

## 4.2. Telegram

### Chuẩn bị token

1. Mở Telegram, chat với **@BotFather** → gửi `/newbot` → đặt tên bot.
2. BotFather trả về token dạng `123456789:ABC-DEF...` — copy lại.

### Bật kênh

Dashboard: **Kênh chat → Telegram → dán token → Lưu**. API:

```bash
curl -X PUT "$VPS/api/channels/telegram" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token": "123456789:ABC-DEF..."}'
```

### Giới hạn người được chat với bot (tuỳ chọn)

Mặc định **ai cũng nhắn được** với bot Telegram. Để giới hạn, truyền danh sách Telegram user ID:

```bash
curl -X PUT "$VPS/api/channels/telegram" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token": "123456789:ABC-DEF...", "allowed_users": ["111111111", "222222222"]}'
```

- Lấy user ID: nhắn cho bot **@userinfobot** trên Telegram.
- Gửi `allowed_users: []` (rỗng) để **gỡ giới hạn** — ai cũng chat được trở lại.

## 4.3. Discord

1. Vào [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → tab **Bot** → **Reset Token** → copy.
2. Mời bot vào server qua **OAuth2 URL Generator** (scope `bot`, quyền đọc/gửi tin nhắn).
3. Dán token:

```bash
curl -X PUT "$VPS/api/channels/discord" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token": "MTIz..."}'
```

## 4.4. Slack

Slack cần **2 token** (Socket Mode):

1. Tạo app tại [api.slack.com/apps](https://api.slack.com/apps) → bật **Socket Mode**.
2. **Bot Token** (`xoxb-...`): tab OAuth & Permissions → Install to Workspace.
3. **App Token** (`xapp-...`): tab Basic Information → App-Level Tokens (scope `connections:write`).

```bash
curl -X PUT "$VPS/api/channels/slack" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token": "xoxb-...", "extra": {"SLACK_APP_TOKEN": "xapp-..."}}'
```

## 4.5. Signal / WhatsApp

| Kênh | Giá trị cần nhập | Ghi chú |
|------|------------------|---------|
| Signal | Số tài khoản Signal (`SIGNAL_ACCOUNT`) | Cần cài đặt signal-cli phía VPS — liên hệ kỹ thuật |
| WhatsApp | Chế độ (`WHATSAPP_MODE`) | Cần thiết lập bổ sung ngoài phạm vi dashboard — liên hệ kỹ thuật |

```bash
curl -X PUT "$VPS/api/channels/signal" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token": "+84901234567"}'
```

## 4.6. Tắt một kênh

Dashboard: nút **Gỡ kênh**. API:

```bash
curl -X DELETE "$VPS/api/channels/telegram" \
  -H "Authorization: Bearer $MGMT_KEY"
```

Xoá toàn bộ token liên quan của kênh đó và restart gateway.

## 4.7. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|-------------|-----------|
| Lưu token xong bot không phản hồi | Đợi ~10s gateway restart; nhắn lại; xem log `GET /api/logs?service=hermes-gateway` |
| 404 "Unknown channel" | Tên kênh sai — hợp lệ: `telegram, discord, slack, slack_app, signal, whatsapp` |
| Telegram bot không trả lời 1 người cụ thể | Người đó không nằm trong `allowed_users` — thêm ID hoặc gỡ giới hạn |
| Token bị lộ | Vào trang nhà cung cấp **revoke token cũ**, tạo token mới, cập nhật lại kênh |
