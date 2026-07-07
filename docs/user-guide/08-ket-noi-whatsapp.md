# 8. Kết nối WhatsApp

Cho phép bot trả lời tin nhắn qua **WhatsApp** (qua bridge Baileys tích hợp sẵn của
Hermes — mô phỏng phiên WhatsApp Web). Người dùng chỉ cần: chọn chế độ → quét QR →
bật. Không cần SSH, không cần cấu hình tay.

> **Khác Zalo ở đâu?** WhatsApp **không có khái niệm "owner/sếp"**. Chỉ 1 tài khoản
> WhatsApp (tài khoản bot) và một bước **bật (enable)** sau khi quét QR. Ai được phép
> nhắn bot thì khai qua `allowed_users` (theo số điện thoại).

---

## 8.1. Luồng kết nối (state machine)

```
                POST /connect {mode}
disconnected ─────────────────────────► pending ──(user quét QR)──► paired
     ▲                                     │                          │
     │                                 GET /qr  (ảnh PNG)             │ POST /enable {mode, allowed_users}
     │                                     │                          ▼
     └──────────── POST /disconnect ──────┴──────────────────────► connected
```

`GET /api/whatsapp/status` trả về `data.status` là một trong:

| status | Ý nghĩa | Dashboard nên hiện |
|--------|---------|--------------------|
| `disconnected` | Chưa thiết lập gì | Nút **"Kết nối WhatsApp"** |
| `pending` | Đang chờ quét QR | Ảnh QR + spinner "Đang chờ quét…" |
| `paired` | Đã quét xong, **chưa bật** | Form chọn mode + nút **"Bật"** |
| `connected` | Đang chạy | Badge xanh "Đang hoạt động" + nút "Ngắt" |

> Bridge WhatsApp không tự lộ QR ra HTTP — mgmt-api chạy một tiến trình phụ để bắt
> mã QR rồi render thành PNG. Chi tiết kỹ thuật ở `management-api/hermes_mgmt/routes/whatsapp.py`.

---

## 8.2. Xác thực (auth)

Mọi endpoint yêu cầu **1 trong 2**:

- Header `Authorization: Bearer <HERMES_MGMT_API_KEY>` (server-to-server), **hoặc**
- Cookie phiên đăng nhập dashboard (`POST /api/auth/login`) — trình duyệt tự gửi.

Trong dashboard (đã đăng nhập), gọi thẳng bằng cookie, **không cần** nhúng API key vào frontend.

---

## 8.3. Tham chiếu API

Base path: `/api/whatsapp`. Mọi response bọc trong envelope chung:

```json
{ "ok": true, "data": { ... }, "error": null }
```

### `GET /status`

Poll endpoint này (khuyên 2–3 giây/lần khi đang ở màn kết nối).

```jsonc
// 200 — ví dụ khi đang chạy
{
  "ok": true,
  "data": {
    "status": "connected",        // disconnected | pending | paired | connected
    "enabled": true,              // WHATSAPP_ENABLED trong .env
    "mode": "self-chat",          // bot | self-chat
    "allowed_users": "*",         // chuỗi đã lưu (số điện thoại, "," hoặc "*")
    "paired": true,               // đã có creds.json (đã quét QR) chưa
    "bridge_connected": true,     // bridge của gateway đang online không
    "pairing": null,              // trạng thái tiến trình quét QR: pending|connected|null
    "qr_ready": false,            // QR đã sẵn sàng để hiển thị chưa
    "valid_modes": ["bot", "self-chat"]
  },
  "error": null
}
```

### `POST /connect`

Bắt đầu quét QR. Trả về **ngay**; QR sinh bất đồng bộ (poll `/status` + hiện `/qr`).

```jsonc
// Request body (tùy chọn) — pre-lưu mode để /enable dùng lại mặc định
{ "mode": "self-chat" }           // hoặc "bot"; bỏ trống cũng được

// 200 — chưa pair → bắt đầu QR
{ "ok": true, "data": { "status": "pending", "qr_url": "/api/whatsapp/qr" }, "error": null }

// 200 — đã pair sẵn (bỏ qua QR, sang thẳng /enable)
{ "ok": true, "data": { "status": "paired", "qr_url": null }, "error": null }

// 400 — mode sai
// 503 — chưa cài được bridge deps, hoặc không spawn được tiến trình quét QR
```

### `GET /qr`

Trả **PNG thô** — gắn thẳng vào `<img>`. **Không** phải JSON envelope.

```html
<img src="/api/whatsapp/qr" alt="Quét bằng WhatsApp" />
```

- `200` + `image/png` — ảnh QR (header `Cache-Control: no-store`).
- `404` — QR **chưa sẵn sàng** (đợi 1–2s sau `/connect` rồi thử lại) **hoặc** đã quét xong.
- `503` — tiến trình quét QR chưa chạy (gọi `/connect` trước).

> Vì trình duyệt cache ảnh, nên thêm query đổi mỗi lần refresh: `/api/whatsapp/qr?t=${Date.now()}`.

### `POST /enable`

Bật WhatsApp: ghi cấu hình + restart gateway. **Yêu cầu đã pair** (`creds.json` tồn tại).

```jsonc
// Request body
{
  "mode": "bot",                 // bot | self-chat (bỏ trống → lấy mode đã lưu / "self-chat")
  "allowed_users": "84901234567" // BẮT BUỘC khi mode=bot: số có mã QG, cách nhau ","; hoặc "*" cho tất cả
}

// 200
{ "ok": true, "data": { "status": "enabled", "mode": "bot", "restarted": true }, "error": null }

// 409 — chưa quét QR (thiếu creds.json)
// 400 — mode sai, hoặc mode=bot mà thiếu allowed_users
```

### `POST /disconnect`

Đăng xuất + xoá phiên + `WHATSAPP_ENABLED=false` + restart gateway. Lần sau muốn dùng lại phải quét QR mới.

```jsonc
// 200
{ "ok": true, "data": { "status": "disconnected" }, "error": null }
```

### `GET /logs?lines=200`

Tail các dòng log liên quan WhatsApp (lọc từ log Hermes). `lines` ∈ `[10, 1000]`, mặc định 200.

```jsonc
{ "ok": true, "data": { "lines": ["[gateway.log] ...", "..."], "count": 42 }, "error": null }
```

---

## 8.4. Hai chế độ (mode)

| mode | Bot phản hồi ai | `allowed_users` |
|------|------------------|-----------------|
| **`self-chat`** | Chỉ tin nhắn **bạn tự gửi cho chính mình** (chat với "You") | Không cần |
| **`bot`** | Tin nhắn từ người khác | **Bắt buộc**: danh sách số (mã QG, không dấu `+`), cách nhau `,`; hoặc `"*"` = tất cả |

Ví dụ `allowed_users`: `84901234567,84987654321` (số Việt Nam bỏ số 0 đầu, thêm `84`).

---

## 8.5. Ví dụ tích hợp frontend

```js
const API = "/api/whatsapp";
// Nếu gọi server-to-server, thêm: { headers: { Authorization: `Bearer ${MGMT_KEY}` } }
const opts = { credentials: "include" };

async function getStatus() {
  const r = await fetch(`${API}/status`, opts);
  return (await r.json()).data;
}

// 1) Người dùng bấm "Kết nối" → chọn mode
async function connect(mode) {
  const r = await fetch(`${API}/connect`, {
    ...opts,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  return (await r.json()).data; // { status: "pending" | "paired", ... }
}

// 2) Poll trạng thái + refresh ảnh QR tới khi paired/connected
function startPolling(onState) {
  const img = document.getElementById("wa-qr");
  const timer = setInterval(async () => {
    const s = await getStatus();
    onState(s);
    if (s.qr_ready) img.src = `${API}/qr?t=${Date.now()}`; // ép reload ảnh
    if (s.status === "paired" || s.status === "connected") clearInterval(timer);
  }, 2500);
  return () => clearInterval(timer);
}

// 3) Sau khi paired → bật
async function enable(mode, allowedUsers) {
  const r = await fetch(`${API}/enable`, {
    ...opts,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, allowed_users: allowedUsers }),
  });
  if (r.status === 409) throw new Error("Chưa quét QR");
  if (r.status === 400) throw new Error("Thiếu allowed_users cho mode=bot");
  return (await r.json()).data; // { status: "enabled", restarted: true }
}

async function disconnect() {
  await fetch(`${API}/disconnect`, { ...opts, method: "POST" });
}
```

### Gợi ý UX

1. Màn **chọn mode** (2 lựa chọn: *Chỉ mình tôi* = self-chat / *Bot cho khách* = bot).
2. Nếu chọn `bot` → hiện ô nhập số điện thoại được phép (hoặc toggle "Cho phép tất cả" = `*`).
3. Bấm **Kết nối** → `connect(mode)` → hiện QR (`<img id="wa-qr">`) → `startPolling`.
4. `status` chuyển `paired` → hiện nút **Bật** → `enable(mode, allowedUsers)`.
5. `status` = `connected` → badge xanh "Đang hoạt động".

---

## 8.6. Lưu ý vận hành

- **Phiên đăng nhập lưu trên đĩa VPS** — bot không bị đăng xuất khi restart dịch vụ.
- **Điện thoại phải online**: giống WhatsApp Web, máy chủ WhatsApp trên điện thoại phải bật net (Baileys là phiên linked-device).
- **Chỉ 1 phiên quét tại một thời điểm**: đừng gọi `/connect` song song nhiều tab. Nếu đang `connected` mà `/connect`, API trả `paired` chứ không tạo phiên mới.
- **Đổi số/đổi tài khoản**: gọi `/disconnect` rồi `/connect` lại để quét QR tài khoản khác.
- **Enable chỉ cần env**: `WHATSAPP_ENABLED=true` là gateway tự nhận (không đụng `config.yaml`). API tự ghi vào cả 2 store `.env` và restart gateway giúp.
- **Tăng tốc lần đầu**: cài VPS với `install.sh --with-whatsapp` để pre-cài sẵn dependencies (Baileys) → quét QR không phải chờ. Không có flag thì lần `/connect` đầu tiên sẽ tự `npm install` (mất vài phút).

---

## 8.7. Xử lý sự cố nhanh

| Triệu chứng | Nguyên nhân / cách xử lý |
|-------------|--------------------------|
| `GET /qr` trả 404 mãi | QR chưa sinh xong (đợi 1–2s) hoặc đã quét xong → poll `/status`, nếu `paired` thì chuyển bước bật |
| `/connect` trả 503 | Node/bridge chưa sẵn sàng → kiểm tra `hermes-gateway` chạy chưa, hoặc cài lại `install.sh --with-whatsapp` |
| `/enable` trả 409 | Chưa quét QR — bắt người dùng quét trước |
| `/enable` trả 400 | `mode=bot` mà chưa nhập `allowed_users` (dùng `*` nếu muốn mở cho tất cả) |
| Bật xong `bridge_connected` vẫn `false` | Đợi ~10s cho gateway restart + bridge kết nối lại; xem `GET /logs` |
