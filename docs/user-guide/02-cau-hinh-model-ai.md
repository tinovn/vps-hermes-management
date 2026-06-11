# 2. Cấu hình Model AI

Bot cần một "bộ não" LLM để trả lời. Có 2 cách cấp:

- **A. API key** của một nhà cung cấp (Anthropic, OpenAI, DeepSeek, Google...) — trả phí theo lượng dùng.
- **B. ChatGPT OAuth (Codex)** — đăng nhập bằng tài khoản ChatGPT Plus/Pro có sẵn, **không cần API key**.

## 2.1. Các provider hỗ trợ

Danh sách template có sẵn (xem qua `GET /api/providers`):

`anthropic`, `openai`, `google`, `deepseek`, `groq`, `mistral`, `xai`, `openrouter`, `together`, `nous-portal`, `huggingface`, `kimi`, `minimax`, `mimo`, `zai`, `codex` (ChatGPT OAuth).

## 2.2. Cách A — Cấu hình bằng API key

Trên dashboard: **Model AI → chọn nhà cung cấp → dán API key → Lưu**. Các bước phía dưới là API tương ứng (dashboard tự làm hết).

### Bước 1: Chọn provider + model

```bash
curl -X PUT "$VPS/api/config/provider" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider": "deepseek", "model": "deepseek/deepseek-chat"}'
```

### Bước 2: Nhập API key

```bash
curl -X PUT "$VPS/api/config/api-key" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider": "deepseek", "api_key": "sk-..."}'
```

Key được ghi vào cả 2 nơi cấu hình (`/opt/hermes/.env` + `~/.hermes/.env`) và gateway **tự restart** để áp dụng.

### Bước 3: Kiểm tra key hợp lệ

```bash
curl -X POST "$VPS/api/config/test-key" \
  -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider": "deepseek", "api_key": "sk-..."}'
# → {"ok": true, "data": {"status_code": 200}} = key dùng được
```

> Nên **test trước khi lưu** — dashboard có nút "Kiểm tra key" làm đúng việc này.

### Xoá API key

```bash
curl -X DELETE "$VPS/api/config/api-key?provider=deepseek" \
  -H "Authorization: Bearer $MGMT_KEY"
```

## 2.3. Cách B — ChatGPT OAuth (Codex)

Dành cho khách đã có tài khoản **ChatGPT Plus/Pro/Team**: bot dùng trực tiếp gói ChatGPT, không phát sinh phí API.

### Quy trình trên dashboard

1. Vào **Model AI → Kết nối ChatGPT** → bấm **Bắt đầu**.
2. Dashboard hiện ra **đường link** (ví dụ `https://auth.openai.com/codex/device`) và **mã xác nhận** (ví dụ `41JU-ST9W8`).
3. Mở link trên điện thoại/máy tính, **đăng nhập tài khoản ChatGPT**, nhập mã xác nhận.
4. Quay lại dashboard — trạng thái tự chuyển sang **Đã kết nối** trong vài giây. Hệ thống tự động:
   - Chuyển model provider sang `codex`
   - Restart gateway → bot bắt đầu dùng ChatGPT ngay

### API tương ứng

| Bước | API | Ghi chú |
|------|-----|---------|
| Bắt đầu | `POST /api/codex/auth/start` | Trả về `{url, code}` để hiển thị |
| Theo dõi | `GET /api/codex/auth/status` | Poll đến khi `status: "connected"` |
| Ngắt kết nối | `POST /api/codex/auth/disable` | Body tuỳ chọn `{"to_provider": "deepseek"}` để chuyển ngay sang provider khác |
| Import thủ công | `POST /api/codex/auth/import` | Dán nội dung `auth.json` lấy từ máy khác (fallback) |

### Lưu ý quan trọng với Codex

- **Không chọn được model cụ thể** khi dùng Codex — tài khoản ChatGPT tự quyết định model mặc định (gửi model tuỳ chỉnh sẽ bị từ chối HTTP 400). Hệ thống tự xoá `model.default` khi bật Codex.
- Khi đang đăng nhập Codex, hệ thống **ưu tiên Codex hơn mọi provider khác**. Muốn đổi sang provider API key, **bắt buộc bấm "Ngắt kết nối ChatGPT"** (`POST /api/codex/auth/disable`) trước, rồi mới chọn provider mới.
- Token lưu trong `~/.hermes/auth.json` trên VPS; API không bao giờ trả token ra ngoài.

## 2.4. Xem cấu hình hiện tại

```bash
curl -H "Authorization: Bearer $MGMT_KEY" "$VPS/api/config"
```

Mọi giá trị nhạy cảm (api_key, token, secret, password) được **che tự động** dạng `sk-****ab12`.

## 2.5. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|-------------|-----------|
| Bot không trả lời sau khi đổi model | Chờ ~10s (gateway đang restart), kiểm tra `GET /api/status` |
| Test key trả `ok: false` HTTP 401 | Key sai hoặc hết hạn — tạo key mới ở trang nhà cung cấp |
| Đổi provider nhưng bot vẫn dùng ChatGPT | Chưa ngắt Codex — gọi `POST /api/codex/auth/disable` trước |
| `start` Codex trả `status: error` kèm `raw` | CLI in output lạ — gửi nội dung `raw` cho đội kỹ thuật |
